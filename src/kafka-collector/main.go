// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/IBM/sarama"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Define error types
var (
	ShutdownTimeoutError      = errors.New("shutdown timeout exceeded before all in-flight messages completed processing")
	OffsetCommitError         = errors.New("failed to commit pending offsets to Kafka during shutdown")
	TransientProcessingError = errors.New("transient processing error")
	PermanentProcessingError = errors.New("permanent processing error")
	DLQProduceError          = errors.New("DLQ produce error")
)

// Config holds retry and DLQ configuration
type Config struct {
	RetryMaxAttempts int
	RetryDelayMs     int
	DLQTopicName     string
	DLQEnabled       bool
}

func loadConfig() Config {
	cfg := Config{
		RetryMaxAttempts: 3,
		RetryDelayMs:     1000,
		DLQTopicName:     "kafka-collector-dlq",
		DLQEnabled:       true,
	}

	if val := os.Getenv("KAFKA_COLLECTOR_RETRY_MAX_ATTEMPTS"); val != "" {
		if intVal, err := strconv.Atoi(val); err == nil && intVal >= 0 {
			cfg.RetryMaxAttempts = intVal
		}
	}

	if val := os.Getenv("KAFKA_COLLECTOR_RETRY_DELAY_MS"); val != "" {
		if intVal, err := strconv.Atoi(val); err == nil && intVal >= 0 {
			cfg.RetryDelayMs = intVal
		}
	}

	if val := os.Getenv("KAFKA_COLLECTOR_DLQ_TOPIC_NAME"); val != "" {
		cfg.DLQTopicName = val
	}

	if val := os.Getenv("KAFKA_COLLECTOR_DLQ_ENABLED"); val != "" {
		if boolVal, err := strconv.ParseBool(val); err == nil {
			cfg.DLQEnabled = boolVal
		}
	}

	return cfg
}

// DLQProducer handles production of failed messages to dead-letter queue
type DLQProducer interface {
	// Produce sends a failed message to DLQ with original metadata
	// Parameters:
	//   originalMsg: *sarama.ConsumerMessage - Original failed Kafka message
	//   failureReason: string - Reason for message failure (error message)
	//   retryCount: int - Number of retries attempted before DLQ production
	// Returns:
	//   error: nil if production succeeded, non-nil otherwise
	Produce(originalMsg *sarama.ConsumerMessage, failureReason string, retryCount int) error
}

// SaramaDLQProducer implements DLQProducer using sarama SyncProducer
type SaramaDLQProducer struct {
	producer sarama.SyncProducer
	topic    string
}

func NewSaramaDLQProducer(brokers []string, config *sarama.Config, topic string) (*SaramaDLQProducer, error) {
	producer, err := sarama.NewSyncProducer(brokers, config)
	if err != nil {
		return nil, err
	}
	return &SaramaDLQProducer{
		producer: producer,
		topic:    topic,
	}, nil
}

func (p *SaramaDLQProducer) Produce(originalMsg *sarama.ConsumerMessage, failureReason string, retryCount int) error {
	// Copy original headers
	headers := make([]*sarama.RecordHeader, len(originalMsg.Headers))
	copy(headers, originalMsg.Headers)

	// Add new headers
	headers = append(headers,
		&sarama.RecordHeader{Key: []byte("x-failure-reason"), Value: []byte(failureReason)},
		&sarama.RecordHeader{Key: []byte("x-retry-count"), Value: []byte(strconv.Itoa(retryCount))},
		&sarama.RecordHeader{Key: []byte("x-original-topic"), Value: []byte(originalMsg.Topic)},
	)

	// Create producer message
	producerMsg := &sarama.ProducerMessage{
		Topic:     p.topic,
		Partition: originalMsg.Partition,
		Value:     sarama.ByteEncoder(originalMsg.Value),
		Headers:   headers,
	}

	// Preserve original key if exists
	if originalMsg.Key != nil {
		producerMsg.Key = sarama.ByteEncoder(originalMsg.Key)
	}

	_, _, err := p.producer.SendMessage(producerMsg)
	if err != nil {
		return DLQProduceError
	}
	return nil
}

// Global instances
var (
	cfg         = loadConfig()
	dlqProducer DLQProducer
)

// Prometheus metrics
var (
	kafkaCollectorMessagesProcessedTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "kafka_collector_messages_processed_total",
			Help: "Total count of messages processed by status",
		},
		[]string{"status"},
	)
	kafkaCollectorRetryAttemptsTotal = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "kafka_collector_retry_attempts_total",
			Help: "Total number of retry attempts made for transient failures",
		},
	)
	kafkaCollectorDLQProduceErrorsTotal = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "kafka_collector_dlq_produce_errors_total",
			Help: "Total number of errors encountered when producing to DLQ",
		},
	)
)

func init() {
	// Register metrics
	prometheus.MustRegister(
		kafkaCollectorMessagesProcessedTotal,
		kafkaCollectorRetryAttemptsTotal,
		kafkaCollectorDLQProduceErrorsTotal,
	)
}

// ProcessMessageWithRetry attempts to process a Kafka message with retries for transient errors
// Parameters:
//   msg: *sarama.ConsumerMessage - Original Kafka message to process
//   processFn: func(*sarama.ConsumerMessage) error - Processing function to execute
// Returns:
//   error: nil if processing succeeded even after retries, non-nil if all retries failed
func ProcessMessageWithRetry(msg *sarama.ConsumerMessage, processFn func(*sarama.ConsumerMessage) error) error {
	// First attempt
	err := processFn(msg)
	if err == nil {
		kafkaCollectorMessagesProcessedTotal.WithLabelValues("success").Inc()
		return nil
	}

	// If permanent error, return immediately
	if errors.Is(err, PermanentProcessingError) {
		return err
	}

	// If not transient error, treat as permanent
	if !errors.Is(err, TransientProcessingError) {
		return err
	}

	// Do retries
	for attempt := 0; attempt < cfg.RetryMaxAttempts; attempt++ {
		// Increment retry metrics
		kafkaCollectorRetryAttemptsTotal.Inc()
		kafkaCollectorMessagesProcessedTotal.WithLabelValues("retried").Inc()

		// Wait delay
		time.Sleep(time.Duration(cfg.RetryDelayMs) * time.Millisecond)

		// Retry processing
		err = processFn(msg)
		if err == nil {
			kafkaCollectorMessagesProcessedTotal.WithLabelValues("success").Inc()
			return nil
		}

		// If not transient error anymore, break
		if !errors.Is(err, TransientProcessingError) {
			break
		}
	}

	return err
}

func processSingleMessage(msg *sarama.ConsumerMessage, processFn func(*sarama.ConsumerMessage) error) {
	retryCount := 0
	err := ProcessMessageWithRetry(msg, func(m *sarama.ConsumerMessage) error {
		return processFn(m)
	})

	if err == nil {
		return
	}

	// Count retries: if it was transient error, retry count is cfg.RetryMaxAttempts, else 0
	if errors.Is(err, TransientProcessingError) {
		retryCount = cfg.RetryMaxAttempts
	}

	// Handle DLQ
	if !cfg.DLQEnabled {
		kafkaCollectorMessagesProcessedTotal.WithLabelValues("failed").Inc()
		return
	}

	err = dlqProducer.Produce(msg, err.Error(), retryCount)
	if err != nil {
		kafkaCollectorDLQProduceErrorsTotal.Inc()
		kafkaCollectorMessagesProcessedTotal.WithLabelValues("failed").Inc()
		log.Printf("Failed to produce message to DLQ: %v", err)
		return
	}

	kafkaCollectorMessagesProcessedTotal.WithLabelValues("dlq_produced").Inc()
}

// KafkaConsumer wraps sarama.Consumer with graceful shutdown capabilities
type KafkaConsumer struct {
	consumer            sarama.Consumer
	shutdownTimeout     time.Duration
	wg                  sync.WaitGroup
	stopConsume         chan struct{}
	shutdownInProgress  atomic.Bool
	pendingOffsets      map[string]map[int32]int64 // topic -> partition -> next offset to commit
	offsetMu            sync.Mutex
	inFlightMessages    []*sarama.ConsumerMessage
	inFlightMu          sync.Mutex
	topics              []string
}

// NewKafkaConsumer creates a new KafkaConsumer instance with configured shutdown timeout
func NewKafkaConsumer(brokers []string, topics []string, config *sarama.Config) (*KafkaConsumer, error) {
	consumer, err := sarama.NewConsumer(brokers, config)
	if err != nil {
		return nil, err
	}

	// Parse shutdown timeout from environment variable
	timeoutStr := os.Getenv("KAFKA_CONSUMER_SHUTDOWN_TIMEOUT")
	timeout := 30 * time.Second
	if timeoutStr != "" {
		parsed, err := time.ParseDuration(timeoutStr)
		if err == nil {
			timeout = parsed
		} else {
			log.Printf("Warning: Invalid KAFKA_CONSUMER_SHUTDOWN_TIMEOUT value '%s', using default 30s: %v", timeoutStr, err)
		}
	}

	return &KafkaConsumer{
		consumer:         consumer,
		shutdownTimeout:  timeout,
		stopConsume:      make(chan struct{}),
		pendingOffsets:   make(map[string]map[int32]int64),
		topics:           topics,
	}, nil
}

// Poll fetches the next available message, returns nil if shutdown is in progress
func (c *KafkaConsumer) Poll(ctx context.Context, timeout time.Duration) *sarama.ConsumerMessage {
	if c.shutdownInProgress.Load() {
		return nil
	}

	select {
	case <-c.stopConsume:
		return nil
	case <-ctx.Done():
		return nil
	default:
		// In real implementation, this would properly consume partitions and return messages
		// For this demo, we simulate message consumption
		// (Actual partition consumption logic would be here)
		return nil
	}
}

// processMessage processes a Kafka message, tracks it as in-flight until complete
func (c *KafkaConsumer) processMessage(msg *sarama.ConsumerMessage) {
	if msg == nil {
		return
	}

	// Track in-flight message
	c.inFlightMu.Lock()
	c.inFlightMessages = append(c.inFlightMessages, msg)
	c.inFlightMu.Unlock()

	c.wg.Add(1)
	defer func() {
		// Remove from in-flight when done
		c.inFlightMu.Lock()
		for i, m := range c.inFlightMessages {
			if m == msg {
				c.inFlightMessages = append(c.inFlightMessages[:i], c.inFlightMessages[i+1:]...)
				break
			}
		}
		c.inFlightMu.Unlock()

		// Update pending offset: commit offset+1 for next consumption
		c.offsetMu.Lock()
		if _, ok := c.pendingOffsets[msg.Topic]; !ok {
			c.pendingOffsets[msg.Topic] = make(map[int32]int64)
		}
		if current, ok := c.pendingOffsets[msg.Topic][msg.Partition]; !ok || msg.Offset >= current {
			c.pendingOffsets[msg.Topic][msg.Partition] = msg.Offset + 1
		}
		c.offsetMu.Unlock()

		c.wg.Done()
	}()

	// Actual message processing logic would go here
	// For demo purposes, we just simulate processing time
	time.Sleep(10 * time.Millisecond)
}

// Shutdown triggers graceful shutdown of the Kafka consumer
func (c *KafkaConsumer) Shutdown(ctx context.Context) error {
	if !c.shutdownInProgress.CompareAndSwap(false, true) {
		// Shutdown already in progress
		return nil
	}

	log.Println("Graceful shutdown initiated: stopping new Kafka message consumption")
	close(c.stopConsume)

	// Count in-flight messages
	c.inFlightMu.Lock()
	inFlightCount := len(c.inFlightMessages)
	c.inFlightMu.Unlock()
	log.Printf("Waiting for %d in-flight Kafka messages to complete processing", inFlightCount)

	// Wait for in-flight messages to complete, or timeout
	waitDone := make(chan struct{})
	go func() {
		c.wg.Wait()
		close(waitDone)
	}()

	select {
	case <-waitDone:
		// All in-flight messages processed
	case <-ctx.Done():
		// Timeout occurred
		c.inFlightMu.Lock()
		defer c.inFlightMu.Unlock()
		log.Printf("Error: Shutdown timeout reached with %d incomplete messages:", len(c.inFlightMessages))
		for _, msg := range c.inFlightMessages {
			log.Printf("  Topic: %s, Partition: %d, Offset: %d", msg.Topic, msg.Partition, msg.Offset)
		}
		return ShutdownTimeoutError
	}

	log.Println("All in-flight messages processed, committing pending offsets")

	// Commit pending offsets
	if offsetManager, ok := c.consumer.(sarama.OffsetManager); ok {
		c.offsetMu.Lock()
		for topic, partitions := range c.pendingOffsets {
			for partition, offset := range partitions {
				pom, err := offsetManager.ManagePartition(topic, partition)
				if err != nil {
					log.Printf("Warning: Failed to get partition offset manager for %s/%d: %v", topic, partition, err)
					continue
				}
				pom.MarkOffset(offset, "")
			}
		}
		c.offsetMu.Unlock()

		// Commit all marked offsets
		err := offsetManager.Commit()
		if err != nil {
			log.Fatalf("FATAL: Failed to commit pending offsets: %v", err)
			return OffsetCommitError
		}
	} else {
		log.Println("Warning: Consumer does not support offset management, skipping offset commit")
	}

	log.Println("Offsets committed successfully: shutting down Kafka consumer")

	// Close the consumer
	err := c.consumer.Close()
	if err != nil {
		log.Printf("Warning: Error closing Kafka consumer: %v", err)
	}

	return nil
}

// GetSaramaClient returns the underlying sarama consumer for testing
func (c *KafkaConsumer) GetSaramaClient() sarama.Consumer {
	return c.consumer
}

// HealthChecker interface defines runtime health checks
type HealthChecker interface {
	Check() error
}

// ReadinessChecker interface defines Kafka consumer readiness checks
type ReadinessChecker interface {
	Check() error
}

// DefaultHealthChecker implements basic runtime health checks
type DefaultHealthChecker struct{}

// Check returns nil if runtime is operating normally
func (h DefaultHealthChecker) Check() error {
	// Basic runtime check - always healthy unless critical failure
	// In a real implementation, add checks for goroutine leaks, memory usage, etc.
	return nil
}

// KafkaReadinessChecker implements Kafka consumer connection checks
type KafkaReadinessChecker struct {
	consumer sarama.Consumer
	topics   []string
}

// Check returns nil if Kafka consumer is connected and subscribed to topics
func (k KafkaReadinessChecker) Check() error {
	if k.consumer == nil {
		return syscall.ENOTCONN
	}

	// Check if we can list topics (verifies connection is active)
	_, err := k.consumer.Topics()
	if err != nil {
		return err
	}

	// Check if we are subscribed to all required topics
	// In a real implementation, add checks for partition assignments, etc.
	return nil
}

// HealthHandler returns HTTP handler for /health endpoint
func HealthHandler(checker HealthChecker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		if err := checker.Check(); err != nil {
			w.Header().Set("Content-Type", "text/plain")
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte("Service Unhealthy"))
			return
		}

		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	}
}

// ReadyHandler returns HTTP handler for /ready endpoint
func ReadyHandler(checker ReadinessChecker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		if err := checker.Check(); err != nil {
			w.Header().Set("Content-Type", "text/plain")
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte("Kafka Consumer Not Ready"))
			return
		}

		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	}
}

func main() {
	// Configuration
	kafkaAddr := os.Getenv("KAFKA_ADDR")
	if kafkaAddr == "" {
		kafkaAddr = "kafka:9092"
	}

	// Get and validate HTTP port from environment variable
	httpPortStr := os.Getenv("KAFKA_COLLECTOR_HTTP_PORT")
	if httpPortStr == "" {
		httpPortStr = "8080"
	}
	httpPortInt, err := strconv.Atoi(httpPortStr)
	if err != nil {
		log.Fatalf("Invalid port value '%s': must be numeric", httpPortStr)
	}
	if httpPortInt < 1 || httpPortInt > 65535 {
		log.Fatalf("Invalid port value '%d': must be between 1 and 65535", httpPortInt)
	}
	httpPort := strconv.Itoa(httpPortInt)

	// Get and process Kafka topics from environment variable
	topicsStr := os.Getenv("KAFKA_COLLECTOR_KAFKA_TOPICS")
	if topicsStr == "" {
		topicsStr = "checkout-events"
	}
	topicsParts := strings.Split(topicsStr, ",")
	var topics []string
	for _, part := range topicsParts {
		trimmed := strings.TrimSpace(part)
		if trimmed != "" {
			topics = append(topics, trimmed)
		}
	}
	if len(topics) == 0 {
		log.Fatal("Kafka topics list cannot be empty")
	}
	log.Printf("Consuming from Kafka topics: %s", strings.Join(topics, ", "))

	// Initialize health checker
	healthChecker := DefaultHealthChecker{}

	// Initialize Kafka consumer
	config := sarama.NewConfig()
	config.Consumer.Return.Errors = true
	config.Consumer.Offsets.AutoCommit.Enable = false // We will commit offsets manually during shutdown
	kafkaConsumer, err := NewKafkaConsumer([]string{kafkaAddr}, topics, config)
	if err != nil {
		log.Printf("Warning: Failed to initialize Kafka consumer: %v", err)
	}
	readinessChecker := KafkaReadinessChecker{
		consumer: kafkaConsumer.consumer,
		topics:   topics,
	}
	defer func() {
		if kafkaConsumer != nil {
			ctx, cancel := context.WithTimeout(context.Background(), kafkaConsumer.shutdownTimeout)
			defer cancel()
			if err := kafkaConsumer.Shutdown(ctx); err != nil {
				log.Fatalf("Kafka consumer shutdown failed: %v", err)
			}
		}
	}()

	// Set up HTTP mux with all endpoints
	mux := http.NewServeMux()
	mux.HandleFunc("/health", HealthHandler(healthChecker))
	mux.HandleFunc("/ready", ReadyHandler(readinessChecker))
	mux.Handle("/metrics", promhttp.Handler()) // Existing metrics endpoint unchanged

	// Start HTTP server
	server := &http.Server{
		Addr:    ":" + httpPort,
		Handler: mux,
	}

	// Graceful shutdown setup
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("Kafka collector server starting on port %s", httpPort)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Failed to start server: %v", err)
		}
	}()

	<-sigChan
	log.Println("Shutting down server...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		log.Fatalf("Server shutdown failed: %v", err)
	}
	log.Println("Server gracefully stopped")
}
