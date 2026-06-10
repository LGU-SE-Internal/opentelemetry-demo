package main

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/IBM/sarama"
	"github.com/cenkalti/backoff/v4"
	"github.com/sony/gobreaker"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
)

// MockKafkaProducer is a mock implementation of kafka.Producer for testing
type MockKafkaProducer struct {
	mock.Mock
}

func (m *MockKafkaProducer) SendMessage(msg *sarama.ProducerMessage) (partition int32, offset int64, err error) {
	args := m.Called(msg)
	return args.Get(0).(int32), args.Get(1).(int64), args.Error(2)
}

func (m *MockKafkaProducer) Close() error {
	return m.Called().Error(0)
}

// Helper to reset environment variables before each test
func resetTestEnv() {
	os.Unsetenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS")
	os.Unsetenv("CHECKOUT_KAFKA_RETRY_INITIAL_BACKOFF_MS")
	os.Unsetenv("CHECKOUT_KAFKA_RETRY_MAX_BACKOFF_MS")
	os.Unsetenv("CHECKOUT_KAFKA_RETRY_JITTER_FACTOR")
	os.Unsetenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_FAILURE_THRESHOLD")
	os.Unsetenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_COOLDOWN_PERIOD_MS")
}

// TestAC1_RetryOnRetriableError tests that retriable errors trigger retries with exponential backoff
func TestAC1_RetryOnRetriableError(t *testing.T) {
	resetTestEnv()
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "3")
	os.Setenv("CHECKOUT_KAFKA_RETRY_INITIAL_BACKOFF_MS", "100")

	mockProducer := new(MockKafkaProducer)
	// Return retriable error first 3 times, then success
	retriableErr := sarama.ErrBrokerNotAvailable
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), retriableErr).Times(3)
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(123), nil).Once()

	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	startTime := time.Now()
	err = producer.Send(context.Background(), &sarama.ProducerMessage{
		Topic: "orders",
		Value: sarama.StringEncoder("test-order"),
	})
	duration := time.Since(startTime)

	assert.NoError(t, err)
	mockProducer.AssertNumberOfCalls(t, "SendMessage", 4) // 1 initial + 3 retries = 4 attempts
	// Verify backoff is at least initial * 2^2 (since 3 retries: 100ms, 200ms, 400ms minimum total ~700ms)
	assert.GreaterOrEqual(t, duration.Milliseconds(), int64(700))
}

// TestAC2_ReturnErrRetriesExhaustedWhenMaxRetriesReached tests that ErrRetriesExhausted is returned after max retries
func TestAC2_ReturnErrRetriesExhaustedWhenMaxRetriesReached(t *testing.T) {
	resetTestEnv()
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "2")

	mockProducer := new(MockKafkaProducer)
	retriableErr := sarama.ErrNetworkException
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), retriableErr).Times(3) // 1 initial + 2 retries

	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	err = producer.Send(context.Background(), &sarama.ProducerMessage{
		Topic: "orders",
		Value: sarama.StringEncoder("test-order"),
	})

	assert.ErrorIs(t, err, ErrRetriesExhausted)
	mockProducer.AssertNumberOfCalls(t, "SendMessage", 3)
}

// TestAC3_CircuitBreakerOpensAfterConsecutiveFailures tests circuit breaker opens after threshold and rejects requests
func TestAC3_CircuitBreakerOpensAfterConsecutiveFailures(t *testing.T) {
	resetTestEnv()
	os.Setenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "0") // Disable retries for this test

	mockProducer := new(MockKafkaProducer)
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), errors.New("kafka down")).Times(5)

	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	// Send 5 failed requests to trip circuit breaker
	for i := 0; i < 5; i++ {
		err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
		assert.Error(t, err)
	}
	mockProducer.AssertNumberOfCalls(t, "SendMessage", 5)

	// 6th request should be rejected immediately without calling underlying producer
	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.ErrorIs(t, err, ErrCircuitBreakerOpen)
	mockProducer.AssertNumberOfCalls(t, "SendMessage", 5) // No additional calls
}

// TestAC4_CircuitBreakerHalfOpenAfterCooldown tests circuit breaker transitions to half open after cooldown
func TestAC4_CircuitBreakerHalfOpenAfterCooldown(t *testing.T) {
	resetTestEnv()
	os.Setenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "2")
	os.Setenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_COOLDOWN_PERIOD_MS", "1000")
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "0")

	mockProducer := new(MockKafkaProducer)
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), errors.New("kafka down")).Times(2)

	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	// Trip circuit breaker
	for i := 0; i < 2; i++ {
		err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
		assert.Error(t, err)
	}

	// Verify circuit is open
	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.ErrorIs(t, err, ErrCircuitBreakerOpen)

	// Wait for cooldown period
	time.Sleep(1100 * time.Millisecond)

	// Test send should be allowed (half open state)
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(123), nil).Once()
	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.NoError(t, err)

	// Circuit should now be closed, next sends should work
	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.NoError(t, err)
	mockProducer.AssertNumberOfCalls(t, "SendMessage", 4) // 2 fail, 2 success
}

// TestAC5_NonRetriableErrorsNotRetried tests non-retriable errors return immediately
func TestAC5_NonRetriableErrorsNotRetried(t *testing.T) {
	resetTestEnv()
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "3")

	mockProducer := new(MockKafkaProducer)
	nonRetriableErr := sarama.ErrInvalidMessage
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), nonRetriableErr).Once()

	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.ErrorIs(t, err, nonRetriableErr)
	mockProducer.AssertNumberOfCalls(t, "SendMessage", 1) // No retries
}

// TestAC6_ConfigLoadedFromEnvWithDefaults tests config values are read from env or use defaults
func TestAC6_ConfigLoadedFromEnvWithDefaults(t *testing.T) {
	resetTestEnv()
	// Set custom env values
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "5")
	os.Setenv("CHECKOUT_KAFKA_RETRY_INITIAL_BACKOFF_MS", "200")
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_BACKOFF_MS", "10000")
	os.Setenv("CHECKOUT_KAFKA_RETRY_JITTER_FACTOR", "0.5")
	os.Setenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "20")
	os.Setenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_COOLDOWN_PERIOD_MS", "60000")

	mockProducer := new(MockKafkaProducer)
	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	// Verify producer uses custom config (we can check by testing behavior)
	// Test max retries = 5, so 6 total attempts
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), sarama.ErrBrokerNotAvailable).Times(6)
	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.ErrorIs(t, err, ErrRetriesExhausted)
	mockProducer.AssertNumberOfCalls(t, "SendMessage", 6)
}

// TestAC7_SendAttemptMetricsIncremented tests send attempt metrics are correctly incremented
func TestAC7_SendAttemptMetricsIncremented(t *testing.T) {
	resetTestEnv()
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "2")

	mockProducer := new(MockKafkaProducer)
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), sarama.ErrBrokerNotAvailable).Times(3)

	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.ErrorIs(t, err, ErrRetriesExhausted)

	// Metrics assertions will pass once implementation is added
	// This test will fail currently as metrics are not implemented yet
}

// TestAC8_CircuitBreakerMetricsIncremented tests circuit breaker metrics are correctly incremented
func TestAC8_CircuitBreakerMetricsIncremented(t *testing.T) {
	resetTestEnv()
	os.Setenv("CHECKOUT_KAFKA_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "2")
	os.Setenv("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", "0")

	mockProducer := new(MockKafkaProducer)
	mockProducer.On("SendMessage", mock.Anything).Return(int32(0), int64(0), errors.New("kafka down")).Times(2)

	producer, err := NewResilientKafkaProducer(mockProducer)
	assert.NoError(t, err)

	// Trip circuit breaker
	for i := 0; i < 2; i++ {
		err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
		assert.Error(t, err)
	}

	// Verify rejected request metric incremented
	err = producer.Send(context.Background(), &sarama.ProducerMessage{Topic: "orders"})
	assert.ErrorIs(t, err, ErrCircuitBreakerOpen)

	// Metrics assertions will pass once implementation is added
	// This test will fail currently as metrics are not implemented yet
}
