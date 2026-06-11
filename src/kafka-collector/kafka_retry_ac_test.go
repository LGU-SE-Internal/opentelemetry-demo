package main

import (
	"context"
	"errors"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/IBM/sarama"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
)

// MockKafkaClient is a mock for sarama client to simulate retriable/non-retriable errors
type MockKafkaClient struct {
	mock.Mock
}

func (m *MockKafkaClient) Brokers() []*sarama.Broker    { return nil }
func (m *MockKafkaClient) Topics() ([]string, error)    { return nil, nil }
func (m *MockKafkaClient) Closed() bool                 { return false }
func (m *MockKafkaClient) Close() error                 { return nil }
func (m *MockKafkaClient) Config() *sarama.Config       { return nil }

// TestAC1_InitialConnectionExponentialBackoff verifies AC-1: retries connection with exponential backoff on retriable errors
func TestAC1_InitialConnectionExponentialBackoff(t *testing.T) {
	t.Parallel()

	// Set test env vars
	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "3")
	os.Setenv("KAFKA_RETRY_INITIAL_BACKOFF_MS", "100")
	os.Setenv("KAFKA_RETRY_MAX_BACKOFF_MS", "400")
	defer os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")
	defer os.Unsetenv("KAFKA_RETRY_INITIAL_BACKOFF_MS")
	defer os.Unsetenv("KAFKA_RETRY_MAX_BACKOFF_MS")

	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}

	start := time.Now()
	consumer, err := NewKafkaConsumerWithRetry(cfg)
	elapsed := time.Since(start)

	// Should fail after all attempts
	assert.Error(t, err)
	assert.Nil(t, consumer)

	// Total time should be ~100 + 200 + 400 = 700ms, within reasonable margin
	assert.GreaterOrEqual(t, elapsed.Milliseconds(), int64(600))
	assert.LessOrEqual(t, elapsed.Milliseconds(), int64(1000))
}

// TestAC2_RuntimeConnectionFailureNoCrash verifies AC-2: consumer retries subscription/polling without exiting when broker becomes unavailable post initial connection
func TestAC2_RuntimeConnectionFailureNoCrash(t *testing.T) {
	t.Parallel()

	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "5")
	os.Setenv("KAFKA_RETRY_INITIAL_BACKOFF_MS", "50")
	defer os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")
	defer os.Unsetenv("KAFKA_RETRY_INITIAL_BACKOFF_MS")

	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}

	// Create consumer (simulate successful initial connection)
	consumer, err := NewKafkaConsumerWithRetry(cfg)
	assert.NoError(t, err)
	assert.NotNil(t, consumer)
	defer consumer.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start consumption, simulate broker going down
	errChan := make(chan error, 1)
	go func() {
		errChan <- consumer.StartConsumption(ctx)
	}()

	// Wait to simulate broker failure
	time.Sleep(200 * time.Millisecond)

	// Check that consumer is still running, no exit after 2 seconds
	select {
	case err := <-errChan:
		t.Fatalf("Consumer exited unexpectedly after runtime broker failure: %v", err)
	case <-time.After(2 * time.Second):
		// Pass: consumer did not crash, is retrying
		cancel()
	}
}

// TestAC3_MessageConsumptionRetry verifies AC-3: message is retried on retriable error up to max attempts
func TestAC3_MessageConsumptionRetry(t *testing.T) {
	t.Parallel()

	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "3")
	defer os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")

	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}
	consumer, _ := NewKafkaConsumerWithRetry(cfg)
	assert.NotNil(t, consumer)
	defer consumer.Close()

	// Mock message
	msg := &sarama.ConsumerMessage{
		Topic:     "test-topic",
		Partition: 0,
		Offset:    123,
		Value:     []byte("test payload"),
	}

	// Count number of processing attempts
	attemptCount := 0
	consumer.SetMessageProcessor(func(ctx context.Context, msg *sarama.ConsumerMessage) error {
		attemptCount++
		return RetriableKafkaError{Err: context.DeadlineExceeded}
	})

	err := consumer.ProcessMessageWithRetry(msg)
	assert.Error(t, err)
	// Should be called exactly max attempts
	assert.Equal(t, 3, attemptCount)
}

// TestAC4_FailedMessageSentToDLQ verifies AC-4: failed messages are sent to DLQ after all attempts
func TestAC4_FailedMessageSentToDLQ(t *testing.T) {
	t.Parallel()

	dlqTopic := "test-dlq-topic"
	os.Setenv("KAFKA_DLQ_TOPIC", dlqTopic)
	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "2")
	defer os.Unsetenv("KAFKA_DLQ_TOPIC")
	defer os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")

	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}
	consumer, _ := NewKafkaConsumerWithRetry(cfg)
	assert.NotNil(t, consumer)
	defer consumer.Close()

	var sentDLQMsg *sarama.ConsumerMessage
	var sentDLQErr error
	consumer.SetDLQSender(func(msg *sarama.ConsumerMessage, err error) error {
		sentDLQMsg = msg
		sentDLQErr = err
		return nil
	})

	msg := &sarama.ConsumerMessage{
		Topic:  "test-topic",
		Value:  []byte("test payload"),
		Offset: 456,
	}

	consumer.SetMessageProcessor(func(ctx context.Context, msg *sarama.ConsumerMessage) error {
		return RetriableKafkaError{Err: context.DeadlineExceeded}
	})

	err := consumer.ProcessMessageWithRetry(msg)
	assert.NoError(t, err)
	// Verify message was sent to DLQ
	assert.NotNil(t, sentDLQMsg)
	assert.Equal(t, msg.Value, sentDLQMsg.Value)
	assert.Equal(t, msg.Offset, sentDLQMsg.Offset)
	assert.Error(t, sentDLQErr)
	assert.Equal(t, dlqTopic, consumer.GetDLQTopic())
}

// TestAC5_ConfigLoadedFromEnvVars verifies AC-5: parameters are loaded from env vars with defaults
func TestAC5_ConfigLoadedFromEnvVars(t *testing.T) {
	t.Parallel()

	// Clear any existing env vars
	os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")
	os.Unsetenv("KAFKA_RETRY_INITIAL_BACKOFF_MS")
	os.Unsetenv("KAFKA_RETRY_MAX_BACKOFF_MS")
	os.Unsetenv("KAFKA_DLQ_TOPIC")

	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}
	consumer, _ := NewKafkaConsumerWithRetry(cfg)
	assert.NotNil(t, consumer)
	defer consumer.Close()

	// Verify defaults are set
	assert.Equal(t, 5, consumer.GetRetryMaxAttempts())
	assert.Equal(t, 100*time.Millisecond, consumer.GetRetryInitialBackoff())
	assert.Equal(t, 10*time.Second, consumer.GetRetryMaxBackoff())
	assert.Equal(t, "kafka-collector-dlq", consumer.GetDLQTopic())

	// Set custom env vars
	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "10")
	os.Setenv("KAFKA_RETRY_INITIAL_BACKOFF_MS", "200")
	os.Setenv("KAFKA_RETRY_MAX_BACKOFF_MS", "5000")
	os.Setenv("KAFKA_DLQ_TOPIC", "custom-dlq")

	consumer2, _ := NewKafkaConsumerWithRetry(cfg)
	assert.NotNil(t, consumer2)
	defer consumer2.Close()

	// Verify custom values are loaded
	assert.Equal(t, 10, consumer2.GetRetryMaxAttempts())
	assert.Equal(t, 200*time.Millisecond, consumer2.GetRetryInitialBackoff())
	assert.Equal(t, 5*time.Second, consumer2.GetRetryMaxBackoff())
	assert.Equal(t, "custom-dlq", consumer2.GetDLQTopic())
}

// TestAC6_HealthCheckDuringRetry verifies AC-6: health returns 200, ready returns 503 during retry
func TestAC6_HealthCheckDuringRetry(t *testing.T) {
	t.Parallel()

	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "10")
	os.Setenv("KAFKA_RETRY_INITIAL_BACKOFF_MS", "1000")
	defer os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")
	defer os.Unsetenv("KAFKA_RETRY_INITIAL_BACKOFF_MS")

	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}

	// Start service with failing connection (retry loop active)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		StartServer(ctx, cfg)
	}()

	// Wait for server to start and enter retry loop
	time.Sleep(500 * time.Millisecond)

	// Check health endpoint
	resp, err := http.Get("http://localhost:13210/health")
	assert.NoError(t, err)
	assert.Equal(t, 200, resp.StatusCode)
	resp.Body.Close()

	// Check ready endpoint
	resp, err = http.Get("http://localhost:13210/ready")
	assert.NoError(t, err)
	assert.Equal(t, 503, resp.StatusCode)
	resp.Body.Close()
}

// TestAC7_GracefulShutdownDuringRetry verifies AC-7: retry aborts immediately on shutdown signal
func TestAC7_GracefulShutdownDuringRetry(t *testing.T) {
	t.Parallel()

	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "100")
	os.Setenv("KAFKA_RETRY_INITIAL_BACKOFF_MS", "1000")
	defer os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")
	defer os.Unsetenv("KAFKA_RETRY_INITIAL_BACKOFF_MS")

	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}

	start := time.Now()
	ctx, cancel := context.WithCancel(context.Background())
	errChan := make(chan error, 1)

	go func() {
		consumer, err := NewKafkaConsumerWithRetry(cfg)
		if err != nil {
			errChan <- err
			return
		}
		errChan <- consumer.StartConsumption(ctx)
	}()

	// Wait for retry loop to start
	time.Sleep(200 * time.Millisecond)

	// Trigger graceful shutdown
	cancel()

	// Wait for shutdown to complete
	select {
	case err := <-errChan:
		elapsed := time.Since(start)
		// Shutdown should complete within 1 second, not wait for all retries
		assert.NoError(t, err)
		assert.Less(t, elapsed.Milliseconds(), int64(1500))
	case <-time.After(5 * time.Second):
		t.Fatalf("Service hung during graceful shutdown in retry loop")
	}
}

// TestAC8_NonRetriableErrorsNotRetried verifies AC-8: non-retriable errors are not retried
func TestAC8_NonRetriableErrorsNotRetried(t *testing.T) {
	t.Parallel()

	// Test connection non-retriable error
	cfg := KafkaConsumerConfig{
		Brokers: []string{"localhost:9092"},
		Topic:   "test-topic",
	}

	start := time.Now()
	consumer, err := NewKafkaConsumerWithRetry(cfg)
	elapsed := time.Since(start)

	// Should fail immediately, no retries
	assert.Error(t, err)
	var nonRetriableErr NonRetriableKafkaError
	assert.True(t, errors.As(err, &nonRetriableErr))
	assert.Nil(t, consumer)
	// Elapsed time should be very small (no backoff waits)
	assert.Less(t, elapsed.Milliseconds(), int64(100))

	// Test message processing non-retriable error
	os.Setenv("KAFKA_RETRY_MAX_ATTEMPTS", "5")
	defer os.Unsetenv("KAFKA_RETRY_MAX_ATTEMPTS")

	consumer, _ = NewKafkaConsumerWithRetry(cfg)
	assert.NotNil(t, consumer)
	defer consumer.Close()

	attemptCount := 0
	consumer.SetMessageProcessor(func(ctx context.Context, msg *sarama.ConsumerMessage) error {
		attemptCount++
		return NonRetriableKafkaError{Err: errors.New("invalid message format")}
	})

	var dlqCalled bool
	consumer.SetDLQSender(func(msg *sarama.ConsumerMessage, err error) error {
		dlqCalled = true
		return nil
	})

	msg := &sarama.ConsumerMessage{Value: []byte("invalid")}
	err = consumer.ProcessMessageWithRetry(msg)
	assert.NoError(t, err)
	// Should only be called once, no retries
	assert.Equal(t, 1, attemptCount)
	// Should be sent to DLQ directly
	assert.True(t, dlqCalled)
}
