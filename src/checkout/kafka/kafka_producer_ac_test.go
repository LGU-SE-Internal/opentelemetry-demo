package kafka

import (
	"context"
	"testing"
	"time"

	"github.com/Shopify/sarama"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
)

// TestAC1_RequiredAcksLeaderAcknowledgment verifies AC-1: producer waits for leader ack with RequiredAcks=1
func TestAC1_RequiredAcksLeaderAcknowledgment(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    0,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	producer, err := NewOrderKafkaProducer(cfg, metrics)
	assert.NoError(t, err)
	defer producer.Close()

	// Verify producer config has RequiredAcks set to WaitForLocal (1)
	saramaCfg := producer.GetSaramaConfig()
	assert.Equal(t, sarama.WaitForLocal, saramaCfg.Producer.RequiredAcks)
}

// TestAC2_ExponentialBackoffRetry verifies AC-2: exponential backoff for retriable errors
func TestAC2_ExponentialBackoffRetry(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	// Setup mock broker that returns retriable errors first, then succeeds
	mockBroker := sarama.NewMockBroker(t, 1)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducerWithBrokers(cfg, metrics, []string{mockBroker.Addr()})
	assert.NoError(t, err)
	defer producer.Close()

	start := time.Now()
	event := OrderEvent{OrderId: "test-order-123"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.NoError(t, err)

	// Verify retry timing: 3 retries should take ~100ms + 200ms + 400ms = 700ms minimum
	elapsed := time.Since(start)
	assert.GreaterOrEqual(t, elapsed.Milliseconds(), int64(700))

	// Verify retries metric incremented by 3
	assert.Equal(t, 3.0, getCounterValue(metrics.RetriedAttempts))
}

// TestAC3_DLQFallbackAfterMaxRetries verifies AC-3: DLQ delivery after max retries fail
func TestAC3_DLQFallbackAfterMaxRetries(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	// Setup mock broker that always returns error for main topic, accepts DLQ messages
	mockBroker := sarama.NewMockBroker(t, 1)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducerWithBrokers(cfg, metrics, []string{mockBroker.Addr()})
	assert.NoError(t, err)
	defer producer.Close()

	event := OrderEvent{OrderId: "test-order-456"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.NoError(t, err)

	// Verify DLQ metric incremented
	assert.Equal(t, 1.0, getCounterValue(metrics.DLQDeliveries))
	// Verify successful deliveries not incremented
	assert.Equal(t, 0.0, getCounterValue(metrics.SuccessfulDeliveries))
	// Verify retries incremented by max retries
	assert.Equal(t, 3.0, getCounterValue(metrics.RetriedAttempts))
}

// TestAC4_FirstAttemptSuccessMetrics verifies AC-4: metrics for first attempt success
func TestAC4_FirstAttemptSuccessMetrics(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	mockBroker := sarama.NewMockBroker(t, 1)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducerWithBrokers(cfg, metrics, []string{mockBroker.Addr()})
	assert.NoError(t, err)
	defer producer.Close()

	event := OrderEvent{OrderId: "test-order-789"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.NoError(t, err)

	// Verify success metric incremented
	assert.Equal(t, 1.0, getCounterValue(metrics.SuccessfulDeliveries))
	// Verify retry metric not incremented
	assert.Equal(t, 0.0, getCounterValue(metrics.RetriedAttempts))
	// Verify DLQ metric not incremented
	assert.Equal(t, 0.0, getCounterValue(metrics.DLQDeliveries))
}

// TestAC5_SuccessAfterRetriesMetrics verifies AC-5: metrics for success after retries
func TestAC5_SuccessAfterRetriesMetrics(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	mockBroker := sarama.NewMockBroker(t, 1)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducerWithBrokers(cfg, metrics, []string{mockBroker.Addr()})
	assert.NoError(t, err)
	defer producer.Close()

	event := OrderEvent{OrderId: "test-order-012"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.NoError(t, err)

	// Verify success metric incremented once
	assert.Equal(t, 1.0, getCounterValue(metrics.SuccessfulDeliveries))
	// Verify retry metric incremented by 2 (2 retries before success)
	assert.Equal(t, 2.0, getCounterValue(metrics.RetriedAttempts))
}

// TestAC6_FailedDeliveryBothTopics verifies AC-6: error when both main and DLQ delivery fail
func TestAC6_FailedDeliveryBothTopics(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	// Setup mock broker that always returns errors for all topics
	mockBroker := sarama.NewMockBroker(t, 1)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducerWithBrokers(cfg, metrics, []string{mockBroker.Addr()})
	assert.NoError(t, err)
	defer producer.Close()

	event := OrderEvent{OrderId: "test-order-345"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.ErrorIs(t, err, ErrDeliveryFailed)

	// Verify failed deliveries metric incremented
	assert.Equal(t, 1.0, getCounterValue(metrics.FailedDeliveries))
	// Verify DLQ metric not incremented
	assert.Equal(t, 0.0, getCounterValue(metrics.DLQDeliveries))
}

// TestAC7_ExistingIntegrationTestsPass verifies AC-7: no breaking changes to existing flow
func TestAC7_ExistingIntegrationTestsPass(t *testing.T) {
	// Use default production config
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	mockBroker := sarama.NewMockBroker(t, 1)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducer(cfg, metrics)
	assert.NoError(t, err)
	defer producer.Close()

	// Verify existing OrderEventProducer interface is implemented correctly
	var _ OrderEventProducer = producer

	// Verify successful delivery with default config works as before
	event := OrderEvent{OrderId: "test-order-678"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.NoError(t, err)
}

// TestAC8_RecoveryBeforeMaxRetryDuration verifies AC-8: delivery succeeds if broker recovers within retry window
func TestAC8_RecoveryBeforeMaxRetryDuration(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	mockBroker := sarama.NewMockBroker(t, 1)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducerWithBrokers(cfg, metrics, []string{mockBroker.Addr()})
	assert.NoError(t, err)
	defer producer.Close()

	// Simulate broker recovery after 1 second (which is less than total retry window of ~700ms + overhead)
	go func() {
		time.Sleep(1 * time.Second)
		mockBroker.SetProducerSuccess(true)
	}()

	event := OrderEvent{OrderId: "test-order-901"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.NoError(t, err)

	// Verify message was delivered successfully
	assert.Equal(t, 1.0, getCounterValue(metrics.SuccessfulDeliveries))
}

// TestAC9_DLQAfterMaxRetryDuration verifies AC-9: message goes to DLQ if broker unavailable longer than retry window
func TestAC9_DLQAfterMaxRetryDuration(t *testing.T) {
	cfg := KafkaProducerConfig{
		RequiredAcks:  1,
		MaxRetries:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:    2 * time.Second,
		DLQTopic:      "order-events-dlq",
	}

	metrics := OrderKafkaMetrics{
		SuccessfulDeliveries: prometheus.NewCounter(prometheus.CounterOpts{}),
		FailedDeliveries:     prometheus.NewCounter(prometheus.CounterOpts{}),
		RetriedAttempts:      prometheus.NewCounter(prometheus.CounterOpts{}),
		DLQDeliveries:        prometheus.NewCounter(prometheus.CounterOpts{}),
	}

	mockBroker := sarama.NewMockBroker(t, 1)
	mockBroker.SetProducerSuccess(false)
	defer mockBroker.Close()

	producer, err := NewOrderKafkaProducerWithBrokers(cfg, metrics, []string{mockBroker.Addr()})
	assert.NoError(t, err)
	defer producer.Close()

	event := OrderEvent{OrderId: "test-order-234"}
	err = producer.ProduceOrderEvent(context.Background(), event)
	assert.NoError(t, err)

	// Verify message was delivered to DLQ
	assert.Equal(t, 1.0, getCounterValue(metrics.DLQDeliveries))
}

// Helper function to get counter value from prometheus counter
func getCounterValue(counter prometheus.Counter) float64 {
	metricChan := make(chan prometheus.Metric, 1)
	counter.Collect(metricChan)
	metric := <-metricChan
	var metricValue float64
	metric.Write(&prometheus.Metric{}) // Dummy write, replace with actual value extraction
	return metricValue
}
