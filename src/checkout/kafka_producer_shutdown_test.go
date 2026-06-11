package main

import (
	"os"
	"syscall"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
)

// MockKafkaProducer mocks the resilient Kafka producer for unit testing
type MockKafkaProducer struct {
	mock.Mock
	CloseCalled  bool
	CloseTimeout time.Duration
}

func (m *MockKafkaProducer) Close(timeout time.Duration) error {
	m.CloseCalled = true
	m.CloseTimeout = timeout
	args := m.Called(timeout)
	return args.Error(0)
}

func (m *MockKafkaProducer) SendOrderEvent(order interface{}) error {
	args := m.Called(order)
	return args.Error(0)
}

// TestAC1_ProducerCloseInvokedOnShutdownSignal verifies Kafka producer Close() is called when SIGINT/SIGTERM received
func TestAC1_ProducerCloseInvokedOnShutdownSignal(t *testing.T) {
	mockProducer := &MockKafkaProducer{}
	originalProducer := kafkaProducer
	defer func() { kafkaProducer = originalProducer }()
	kafkaProducer = mockProducer

	// Set test timeout
	testTimeout := time.After(2 * time.Second)

	// Start service in goroutine
	go func() {
		main()
	}()

	// Give service time to start
	time.Sleep(500 * time.Millisecond)

	// Send SIGTERM signal to process
	proc, err := os.FindProcess(os.Getpid())
	assert.NoError(t, err)
	err = proc.Signal(syscall.SIGTERM)
	assert.NoError(t, err)

	select {
	case <-testTimeout:
		t.Fatal("Test timed out waiting for shutdown")
	case <-time.After(1 * time.Second):
		assert.True(t, mockProducer.CloseCalled, "Kafka producer Close() should be called on shutdown signal")
	}
}

// TestAC5_ProducerCloseCalledWithConfiguredTimeout verifies Close() is called with the configured KAFKA_PRODUCER_SHUTDOWN_TIMEOUT value
func TestAC5_ProducerCloseCalledWithConfiguredTimeout(t *testing.T) {
	// Set custom timeout env var
	customTimeout := "10s"
	originalEnv := os.Getenv("KAFKA_PRODUCER_SHUTDOWN_TIMEOUT")
	defer os.Setenv("KAFKA_PRODUCER_SHUTDOWN_TIMEOUT", originalEnv)
	os.Setenv("KAFKA_PRODUCER_SHUTDOWN_TIMEOUT", customTimeout)

	expectedTimeout, err := time.ParseDuration(customTimeout)
	assert.NoError(t, err)

	mockProducer := &MockKafkaProducer{}
	originalProducer := kafkaProducer
	defer func() { kafkaProducer = originalProducer }()
	kafkaProducer = mockProducer

	// Start service
	go main()
	time.Sleep(500 * time.Millisecond)

	// Trigger shutdown
	proc, _ := os.FindProcess(os.Getpid())
	proc.Signal(syscall.SIGINT)

	time.Sleep(1 * time.Second)
	assert.Equal(t, expectedTimeout, mockProducer.CloseTimeout, "Close() should be called with configured KAFKA_PRODUCER_SHUTDOWN_TIMEOUT value")
	assert.True(t, mockProducer.CloseCalled)
}
