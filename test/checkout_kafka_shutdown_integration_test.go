package test

import (
	"context"
	"os"
	"os/exec"
	"syscall"
	"testing"
	"time"

	"github.com/Shopify/sarama"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestAC2_ZeroMessageLossOnShutdownWithHealthyNetwork verifies all in-flight messages are delivered when shutdown occurs with healthy network
func TestAC2_ZeroMessageLossOnShutdownWithHealthyNetwork(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping integration test in short mode")
	}

	// Setup test Kafka topic
	topic := "test-checkout-orders-ac2"
	setupKafkaTopic(t, topic)
	defer deleteKafkaTopic(t, topic)

	// Start checkout service with Kafka configured
	cmd := exec.Command("go", "run", "../src/checkout/main.go")
	cmd.Env = append(os.Environ(),
		"KAFKA_BROKERS=kafka:9092",
		"KAFKA_PRODUCER_SHUTDOWN_TIMEOUT=5s",
		"KAFKA_TOPIC="+topic,
	)
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
	}()

	// Wait for service to start
	time.Sleep(2 * time.Second)

	// Send 100 test orders (in-flight messages)
	sendTestOrders(t, 100)

	// Send SIGTERM to service immediately after sending messages
	err = cmd.Process.Signal(syscall.SIGTERM)
	require.NoError(t, err)

	// Wait for process to exit
	err = cmd.Wait()
	assert.NoError(t, err, "Service should exit cleanly on SIGTERM")

	// Consume all messages from Kafka topic
	consumer, err := sarama.NewConsumer([]string{"kafka:9092"}, nil)
	require.NoError(t, err)
	defer consumer.Close()

	partitionConsumer, err := consumer.ConsumePartition(topic, 0, sarama.OffsetOldest)
	require.NoError(t, err)
	defer partitionConsumer.Close()

	messageCount := 0
	timeout := time.After(10 * time.Second)
	for {
		select {
		case msg := <-partitionConsumer.Messages():
			assert.NotNil(t, msg)
			messageCount++
		case <-timeout:
			goto countDone
		}
	}
countDone:
	assert.Equal(t, 100, messageCount, "All 100 in-flight messages should be delivered to Kafka, 0 lost")
}

// TestAC3_ShutdownTimeoutEnforcedWhenFlushDelayed verifies service force exits after timeout when flush takes too long
func TestAC3_ShutdownTimeoutEnforcedWhenFlushDelayed(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping integration test in short mode")
	}

	// Setup Kafka with network delay to simulate slow broker
	setupKafkaNetworkDelay(t, 10*time.Second)
	defer resetKafkaNetworkDelay(t)

	topic := "test-checkout-orders-ac3"
	setupKafkaTopic(t, topic)
	defer deleteKafkaTopic(t, topic)

	// Start service with short shutdown timeout
	cmd := exec.Command("go", "run", "../src/checkout/main.go")
	cmd.Env = append(os.Environ(),
		"KAFKA_BROKERS=kafka:9092",
		"KAFKA_PRODUCER_SHUTDOWN_TIMEOUT=2s",
		"KAFKA_TOPIC="+topic,
	)
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
	}()

	time.Sleep(2 * time.Second)

	// Send 10 test orders
	sendTestOrders(t, 10)

	// Start timer, send SIGTERM
	startTime := time.Now()
	err = cmd.Process.Signal(syscall.SIGTERM)
	require.NoError(t, err)

	// Wait for process exit
	err = cmd.Wait()

	// Verify exit happened within timeout + small buffer
	elapsed := time.Since(startTime)
	assert.Less(t, elapsed, 3*time.Second, "Service should exit within ~2s timeout, not wait for broker delay")

	// Verify error log exists about flush failure
	logOutput, err := os.ReadFile("checkout-service.log")
	require.NoError(t, err)
	assert.Contains(t, string(logOutput), "ERROR", "Should log error level message on flush timeout")
	assert.Contains(t, string(logOutput), "unflushed messages", "Should mention count of unflushed messages in error log")
}

// TestAC4_NormalProducerOperationUnchanged verifies non-shutdown producer behavior remains same
func TestAC4_NormalProducerOperationUnchanged(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping integration test in short mode")
	}

	topic := "test-checkout-orders-ac4"
	setupKafkaTopic(t, topic)
	defer deleteKafkaTopic(t, topic)

	cmd := exec.Command("go", "run", "../src/checkout/main.go")
	cmd.Env = append(os.Environ(),
		"KAFKA_BROKERS=kafka:9092",
		"KAFKA_TOPIC="+topic,
	)
	err := cmd.Start()
	require.NoError(t, err)
	defer cmd.Process.Kill()

	time.Sleep(2 * time.Second)

	// Send 50 orders during normal operation
	sendTestOrders(t, 50)

	// Verify messages are delivered without shutdown
	time.Sleep(2 * time.Second)
	consumer, err := sarama.NewConsumer([]string{"kafka:9092"}, nil)
	require.NoError(t, err)
	defer consumer.Close()

	partitionConsumer, err := consumer.ConsumePartition(topic, 0, sarama.OffsetOldest)
	require.NoError(t, err)
	defer partitionConsumer.Close()

	count := 0
	timeout := time.After(5 * time.Second)
	for {
		select {
		case <-partitionConsumer.Messages():
			count++
		case <-timeout:
			goto done
		}
	}
done:
	assert.Equal(t, 50, count, "Normal producer operation should work as before, no changes to message delivery")
}

// TestAC6_ZeroMessageLossMidOperation verifies 0 message loss when terminated mid-operation with in-flight messages
func TestAC6_ZeroMessageLossMidOperation(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping integration test in short mode")
	}

	topic := "test-checkout-orders-ac6"
	setupKafkaTopic(t, topic)
	defer deleteKafkaTopic(t, topic)

	// Start service
	cmd := exec.Command("go", "run", "../src/checkout/main.go")
	cmd.Env = append(os.Environ(),
		"KAFKA_BROKERS=kafka:9092",
		"KAFKA_PRODUCER_SHUTDOWN_TIMEOUT=10s",
		"KAFKA_TOPIC="+topic,
	)
	err := cmd.Start()
	require.NoError(t, err)
	defer cmd.Process.Kill()

	time.Sleep(2 * time.Second)

	// Simulate steady state operation: send messages continuously
	sendCtx, cancel := context.WithCancel(context.Background())
	go func() {
		ticker := time.NewTicker(10 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-sendCtx.Done():
				return
			case <-ticker.C:
				sendTestOrders(t, 1)
			}
		}
	}()

	// Let operation run for 2 seconds to get in-flight messages
	time.Sleep(2 * time.Second)

	// Cancel senders and send SIGTERM immediately
	cancel()
	err = cmd.Process.Signal(syscall.SIGTERM)
	require.NoError(t, err)

	// Wait for exit
	err = cmd.Wait()
	assert.NoError(t, err)

	// Count all delivered messages
	consumer, err := sarama.NewConsumer([]string{"kafka:9092"}, nil)
	require.NoError(t, err)
	defer consumer.Close()

	partitionConsumer, err := consumer.ConsumePartition(topic, 0, sarama.OffsetOldest)
	require.NoError(t, err)
	defer partitionConsumer.Close()

	totalMessages := 0
	timeout := time.After(15 * time.Second)
	for {
		select {
		case <-partitionConsumer.Messages():
			totalMessages++
		case <-timeout:
			goto countDone
		}
	}
countDone:
	// Get total sent messages count from test sender metric
	sentCount := getTestSentOrderCount()
	assert.Equal(t, sentCount, totalMessages, "All messages sent during operation should be delivered, 0 loss when terminated mid-operation")
}

// Helper functions (stubs, implement as needed for test environment)
func setupKafkaTopic(t *testing.T, topic string)     {}
func deleteKafkaTopic(t *testing.T, topic string)    {}
func sendTestOrders(t *testing.T, count int)         {}
func setupKafkaNetworkDelay(t *testing.T, d time.Duration) {}
func resetKafkaNetworkDelay(t *testing.T)            {}
func getTestSentOrderCount() int                     { return 0 }
