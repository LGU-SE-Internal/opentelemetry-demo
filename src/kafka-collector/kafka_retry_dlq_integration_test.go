package kafka_collector

import (
	"errors"
	"os"
	"testing"
	"time"

	"github.com/IBM/sarama"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
)

// Mock implementations for testing
type MockProcessor struct {
	mock.Mock
}

func (m *MockProcessor) Process(msg *sarama.ConsumerMessage) error {
	args := m.Called(msg)
	return args.Error(0)
}

type MockDLQProducer struct {
	mock.Mock
}

func (m *MockDLQProducer) Produce(originalMsg *sarama.ConsumerMessage, failureReason string, retryCount int) error {
	args := m.Called(originalMsg, failureReason, retryCount)
	return args.Error(0)
}

// Test error types as per spec
var (
	TransientProcessingError = errors.New("transient processing error")
	PermanentProcessingError = errors.New("permanent processing error")
	DLQProduceError          = errors.New("DLQ produce error")
)

// AC-1: Retry transient errors 1 + max attempts times with delay
func Test_AC1_TransientErrorRetriesWithDelay(t *testing.T) {
	// Setup config
	os.Setenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS", "3")
	os.Setenv("KAFKA_COLLECTOR_RETRY_DELAY_MS", "100")
	defer os.Unsetenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS")
	defer os.Unsetenv("KAFKA_COLLECTOR_RETRY_DELAY_MS")

	// Create test message
	msg := &sarama.ConsumerMessage{
		Topic: "test-topic", Partition: 0, Offset: 123,
		Value: []byte("test payload"),
		Headers: []*sarama.RecordHeader{
			{Key: []byte("original-header"), Value: []byte("original-value")},
		},
	}

	// Mock processor returns transient error every time
	mockProc := new(MockProcessor)
	mockProc.On("Process", msg).Return(TransientProcessingError).Times(4) // 1 initial + 3 retries

	start := time.Now()
	err := ProcessMessageWithRetry(msg, mockProc.Process)
	elapsed := time.Since(start)

	// Assert retries happened, total delay is at least 3 * 100ms
	assert.ErrorIs(t, err, TransientProcessingError)
	mockProc.AssertExpectations(t)
	assert.GreaterOrEqual(t, elapsed.Milliseconds(), int64(300), "Retry delay not applied")
}

// AC-2: Permanent errors are not retried
func Test_AC2_PermanentErrorNoRetries(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS", "10")
	defer os.Unsetenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS")

	msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
	mockProc := new(MockProcessor)
	mockProc.On("Process", msg).Return(PermanentProcessingError).Once()

	err := ProcessMessageWithRetry(msg, mockProc.Process)

	assert.ErrorIs(t, err, PermanentProcessingError)
	mockProc.AssertExpectations(t) // Only called once, no retries
}

// AC-3: Failed messages sent to DLQ when enabled
func Test_AC3_FailedMessageSentToDLQWhenEnabled(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_DLQ_ENABLED", "true")
	os.Setenv("KAFKA_COLLECTOR_DLQ_TOPIC_NAME", "test-dlq")
	os.Setenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS", "0")
	defer os.Clearenv()

	msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
	failureErr := PermanentProcessingError

	mockDLQ := new(MockDLQProducer)
	dlqProducer = mockDLQ // Inject mock
	mockDLQ.On("Produce", msg, failureErr.Error(), 0).Return(nil).Once()

	// Process message that fails permanently
	processSingleMessage(msg, func(m *sarama.ConsumerMessage) error {
		return failureErr
	})

	mockDLQ.AssertExpectations(t)
}

// AC-4: DLQ messages preserve metadata and add new headers
func Test_AC4_DLQMessagePreservesMetadataAndAddsHeaders(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_DLQ_ENABLED", "true")
	defer os.Clearenv()

	originalHeaders := []*sarama.RecordHeader{
		{Key: []byte("custom-header-1"), Value: []byte("custom-val-1")},
		{Key: []byte("custom-header-2"), Value: []byte("custom-val-2")},
	}
	msg := &sarama.ConsumerMessage{
		Topic: "original-topic", Partition: 5, Offset: 9876,
		Headers: originalHeaders, Value: []byte("test-data"),
	}
	failureReason := "permanent failure: invalid format"
	retryCount := 2

	var capturedMsg *sarama.ConsumerMessage
	mockDLQ := new(MockDLQProducer)
	dlqProducer = mockDLQ
	mockDLQ.On("Produce", msg, failureReason, retryCount).Run(func(args mock.Arguments) {
		capturedMsg = args.Get(0).(*sarama.ConsumerMessage)
	}).Return(nil).Once()

	// Trigger DLQ produce
	_ = dlqProducer.Produce(msg, failureReason, retryCount)

	// Verify original metadata preserved
	assert.Equal(t, 5, int(capturedMsg.Partition))
	assert.Equal(t, int64(9876), capturedMsg.Offset)
	// Verify original headers present
	headerMap := make(map[string]string)
	for _, h := range capturedMsg.Headers {
		headerMap[string(h.Key)] = string(h.Value)
	}
	assert.Equal(t, "custom-val-1", headerMap["custom-header-1"])
	assert.Equal(t, "custom-val-2", headerMap["custom-header-2"])
	// Verify new headers added
	assert.Equal(t, failureReason, headerMap["x-failure-reason"])
	assert.Equal(t, "2", headerMap["x-retry-count"])
	assert.Equal(t, "original-topic", headerMap["x-original-topic"])
}

// AC-5: DLQ not used when disabled
func Test_AC5_DisabledDLQDoesNotProduce(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_DLQ_ENABLED", "false")
	os.Setenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS", "0")
	defer os.Clearenv()

	msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
	mockDLQ := new(MockDLQProducer)
	dlqProducer = mockDLQ // No calls expected

	processSingleMessage(msg, func(m *sarama.ConsumerMessage) error {
		return PermanentProcessingError
	})

	mockDLQ.AssertNotCalled(t, "Produce")
}

// AC-6: Retried metric increments on each retry
func Test_AC6_RetriedMetricIncrements(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS", "2")
	defer os.Clearenv()

	// Reset metrics before test
	kafkaCollectorMessagesProcessedTotal.Reset()

	msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
	mockProc := new(MockProcessor)
	mockProc.On("Process", msg).Return(TransientProcessingError).Times(3) // 1 initial + 2 retries

	_ = ProcessMessageWithRetry(msg, mockProc.Process)

	// Verify retried count = 2
	retriedCount := testutil.ToFloat64(kafkaCollectorMessagesProcessedTotal.WithLabelValues("retried"))
	assert.Equal(t, float64(2), retriedCount)
}

// AC-7: DLQ produced metric increments on successful DLQ produce
func Test_AC7_DLQProducedMetricIncrements(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_DLQ_ENABLED", "true")
	defer os.Clearenv()
	kafkaCollectorMessagesProcessedTotal.Reset()

	msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
	mockDLQ := new(MockDLQProducer)
	dlqProducer = mockDLQ
	mockDLQ.On("Produce", msg, mock.Anything, mock.Anything).Return(nil).Once()

	processSingleMessage(msg, func(m *sarama.ConsumerMessage) error {
		return PermanentProcessingError
	})

	dlqCount := testutil.ToFloat64(kafkaCollectorMessagesProcessedTotal.WithLabelValues("dlq_produced"))
	assert.Equal(t, float64(1), dlqCount)
}

// AC-8: Failed metric increments when DLQ disabled or produce fails
func Test_AC8_FailedMetricIncrements(t *testing.T) {
	t.Run("DLQ disabled", func(t *testing.T) {
		os.Setenv("KAFKA_COLLECTOR_DLQ_ENABLED", "false")
		defer os.Clearenv()
		kafkaCollectorMessagesProcessedTotal.Reset()

		msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
		processSingleMessage(msg, func(m *sarama.ConsumerMessage) error {
			return PermanentProcessingError
		})

		failedCount := testutil.ToFloat64(kafkaCollectorMessagesProcessedTotal.WithLabelValues("failed"))
		assert.Equal(t, float64(1), failedCount)
	})

	t.Run("DLQ produce fails", func(t *testing.T) {
		os.Setenv("KAFKA_COLLECTOR_DLQ_ENABLED", "true")
		defer os.Clearenv()
		kafkaCollectorMessagesProcessedTotal.Reset()

		msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
		mockDLQ := new(MockDLQProducer)
		dlqProducer = mockDLQ
		mockDLQ.On("Produce", msg, mock.Anything, mock.Anything).Return(DLQProduceError).Once()

		processSingleMessage(msg, func(m *sarama.ConsumerMessage) error {
			return PermanentProcessingError
		})

		failedCount := testutil.ToFloat64(kafkaCollectorMessagesProcessedTotal.WithLabelValues("failed"))
		assert.Equal(t, float64(1), failedCount)
	})
}

// AC-9: DLQ produce error increments error metric
func Test_AC9_DLQProduceErrorIncrementsMetric(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_DLQ_ENABLED", "true")
	defer os.Clearenv()
	kafkaCollectorDLQProduceErrorsTotal.Reset()

	msg := &sarama.ConsumerMessage{Topic: "test-topic", Partition: 0, Offset: 123}
	mockDLQ := new(MockDLQProducer)
	dlqProducer = mockDLQ
	mockDLQ.On("Produce", msg, mock.Anything, mock.Anything).Return(DLQProduceError).Once()

	processSingleMessage(msg, func(m *sarama.ConsumerMessage) error {
		return PermanentProcessingError
	})

	errorCount := testutil.ToFloat64(kafkaCollectorDLQProduceErrorsTotal)
	assert.Equal(t, float64(1), errorCount)
}

// AC-10: Default config values used when env vars not set
func Test_AC10_DefaultConfigValues(t *testing.T) {
	// Unset all relevant env vars
	os.Unsetenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS")
	os.Unsetenv("KAFKA_COLLECTOR_RETRY_DELAY_MS")
	os.Unsetenv("KAFKA_COLLECTOR_DLQ_TOPIC_NAME")
	os.Unsetenv("KAFKA_COLLECTOR_DLQ_ENABLED")

	// Load config
	cfg := loadConfig()

	assert.Equal(t, 3, cfg.RetryMaxAttempts)
	assert.Equal(t, 1000, cfg.RetryDelayMs)
	assert.Equal(t, "kafka-collector-dlq", cfg.DLQTopicName)
	assert.Equal(t, true, cfg.DLQEnabled)
}
