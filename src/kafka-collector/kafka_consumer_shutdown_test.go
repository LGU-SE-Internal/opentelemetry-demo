package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/Shopify/sarama"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
)

// MockKafkaConsumer is a mock for our KafkaConsumer implementation
type MockKafkaConsumer struct {
	mock.Mock
}

func (m *MockKafkaConsumer) Shutdown(ctx context.Context) error {
	args := m.Called(ctx)
	return args.Error(0)
}

// TestAC1_StopConsumingOnShutdownSignal verifies no new messages are consumed after shutdown signal
func TestAC1_StopConsumingOnShutdownSignal(t *testing.T) {
	t.Parallel()
	// Setup mock consumer
	consumer := NewKafkaConsumer() // This will fail until implementation exists
	sigChan := make(chan os.Signal, 1)
	sigChan <- os.Interrupt

	// Trigger shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Verify that after signal, Poll() returns no new messages
	// This test fails because current implementation continues polling on exit
	msg := consumer.Poll(ctx, 100*time.Millisecond)
	assert.Nil(t, msg, "Expected no new messages after shutdown signal, got %v", msg)
}

// TestAC2_WaitForAllInFlightMessagesOnShutdown verifies all in-flight messages complete before shutdown
func TestAC2_WaitForAllInFlightMessagesOnShutdown(t *testing.T) {
	t.Parallel()
	consumer := NewKafkaConsumer()
	var processingComplete bool

	// Simulate an in-flight message taking 200ms to process
	go func() {
		msg := &sarama.ConsumerMessage{
			Topic:     "test-topic",
			Partition: 0,
			Offset:    123,
		}
		consumer.processMessage(msg)
		processingComplete = true
	}()

	// Wait a bit for processing to start
	time.Sleep(50 * time.Millisecond)

	// Trigger shutdown with long enough timeout
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := consumer.Shutdown(ctx)

	assert.NoError(t, err)
	assert.True(t, processingComplete, "In-flight message did not complete processing before shutdown")
}

// TestAC3_CommitOffsetsBeforeShutdown verifies offsets are committed after processing completes
func TestAC3_CommitOffsetsBeforeShutdown(t *testing.T) {
	t.Parallel()
	consumer := NewKafkaConsumer()
	// Add a message to process
	msg := &sarama.ConsumerMessage{
		Topic:     "test-topic",
		Partition: 0,
		Offset:    456,
	}
	consumer.processMessage(msg)

	// Get the mock sarama client to verify commit was called
	mockClient := consumer.GetSaramaClient()
	mockClient.On("CommitOffsets", mock.Anything, mock.Anything).Return(nil)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := consumer.Shutdown(ctx)

	assert.NoError(t, err)
	mockClient.AssertCalled(t, "CommitOffsets", mock.Anything, map[string][]*sarama.PartitionOffsetMetadata{
		"test-topic": {{Partition: 0, Offset: 457}}, // Offset +1 is committed
	})
}

// TestAC4_UseConfiguredShutdownTimeout verifies timeout config works
func TestAC4_UseConfiguredShutdownTimeout(t *testing.T) {
	t.Parallel()
	// Test with custom timeout env var
	os.Setenv("KAFKA_CONSUMER_SHUTDOWN_TIMEOUT", "45s")
	defer os.Unsetenv("KAFKA_CONSUMER_SHUTDOWN_TIMEOUT")

	consumer := NewKafkaConsumer()
	assert.Equal(t, 45*time.Second, consumer.shutdownTimeout, "Expected 45s shutdown timeout, got %v", consumer.shutdownTimeout)

	// Test default timeout when env var is not set
	os.Unsetenv("KAFKA_CONSUMER_SHUTDOWN_TIMEOUT")
	consumer2 := NewKafkaConsumer()
	assert.Equal(t, 30*time.Second, consumer2.shutdownTimeout, "Expected default 30s shutdown timeout, got %v", consumer2.shutdownTimeout)
}

// TestAC5_LogIncompleteMessagesOnTimeout verifies error logging on timeout
func TestAC5_LogIncompleteMessagesOnTimeout(t *testing.T) {
	t.Parallel()
	consumer := NewKafkaConsumer()
	consumer.shutdownTimeout = 100 * time.Millisecond

	// Simulate an in-flight message that takes longer than timeout
	processingDone := make(chan struct{})
	go func() {
		msg := &sarama.ConsumerMessage{
			Topic:     "test-topic",
			Partition: 1,
			Offset:    789,
		}
		consumer.processMessage(msg)
		time.Sleep(200 * time.Millisecond)
		close(processingDone)
	}()

	// Wait for processing to start
	time.Sleep(50 * time.Millisecond)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := consumer.Shutdown(ctx)

	// Verify we get ShutdownTimeoutError
	assert.ErrorIs(t, err, ShutdownTimeoutError, "Expected ShutdownTimeoutError, got %v", err)

	// Verify log output contains required fields: count of incomplete, topic/partition/offset
	logOutput := captureLogOutput(func() { consumer.Shutdown(ctx) })
	assert.Contains(t, logOutput, "1 incomplete messages")
	assert.Contains(t, logOutput, "test-topic")
	assert.Contains(t, logOutput, "partition=1")
	assert.Contains(t, logOutput, "offset=789")
}

// TestAC6_LogShutdownProgress verifies info log order during shutdown
func TestAC6_LogShutdownProgress(t *testing.T) {
	t.Parallel()
	consumer := NewKafkaConsumer()
	var logs []string
	// Hook into logger to collect messages
	hookLogger(consumer.logger, func(msg string) { logs = append(logs, msg) })

	// Add 2 in-flight messages
	go consumer.processMessage(&sarama.ConsumerMessage{Offset: 100})
	go consumer.processMessage(&sarama.ConsumerMessage{Offset: 101})
	time.Sleep(50 * time.Millisecond)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	consumer.Shutdown(ctx)

	// Verify log order
	require.Len(t, logs, 4)
	assert.Contains(t, logs[0], "Graceful shutdown initiated: stopping new Kafka message consumption")
	assert.Contains(t, logs[1], "Waiting for 2 in-flight Kafka messages to complete processing")
	assert.Contains(t, logs[2], "All in-flight messages processed, committing pending offsets")
	assert.Contains(t, logs[3], "Offsets committed successfully: shutting down Kafka consumer")
}

// TestAC7_FatalLogOnOffsetCommitFailure verifies error logging on commit failure
func TestAC7_FatalLogOnOffsetCommitFailure(t *testing.T) {
	t.Parallel()
	consumer := NewKafkaConsumer()
	// Mock client to return error on commit
	mockClient := consumer.GetSaramaClient()
	mockClient.On("CommitOffsets", mock.Anything, mock.Anything).Return(OffsetCommitError)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := consumer.Shutdown(ctx)

	assert.ErrorIs(t, err, OffsetCommitError, "Expected OffsetCommitError, got %v", err)
	// Verify FATAL log entry exists with commit details
	logOutput := captureLogOutput(func() { consumer.Shutdown(ctx) })
	assert.Contains(t, logOutput, "FATAL")
	assert.Contains(t, logOutput, "failed to commit offsets")
}

// TestAC8_NoDuplicateMessagesAfterRestart verifies no duplicates after restart
func TestAC8_NoDuplicateMessagesAfterRestart(t *testing.T) {
	t.Parallel()
	// First consumer instance: process message, commit offset
	consumer1 := NewKafkaConsumer()
	msg := &sarama.ConsumerMessage{
		Topic:     "test-topic",
		Partition: 0,
		Offset:    1000,
	}
	consumer1.processMessage(msg)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	consumer1.Shutdown(ctx)

	// Second consumer instance (restart): should not receive offset 1000 again
	consumer2 := NewKafkaConsumer()
	newMsg := consumer2.Poll(ctx, 100*time.Millisecond)
	// Next message should be offset 1001
	assert.Nil(t, newMsg || newMsg.Offset != 1000, "Duplicate message received after restart, got offset %d", newMsg.Offset)
}
