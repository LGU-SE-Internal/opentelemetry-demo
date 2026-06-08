// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
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
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Define error types
var (
	ShutdownTimeoutError = errors.New("shutdown timeout exceeded before all in-flight messages completed processing")
	OffsetCommitError    = errors.New("failed to commit pending offsets to Kafka during shutdown")
)

// KafkaTLSConfig holds TLS configuration for Kafka client connections
type KafkaTLSConfig struct {
	Enabled        bool
	CACertPath     string
	ClientCertPath string
	ClientKeyPath  string
	SkipVerify     bool
}

// LoadKafkaTLSConfig reads and validates TLS configuration from environment variables
// Returns error if:
// 1. TLS is enabled but CA cert path is empty or file does not exist
// 2. Client cert path is provided but client key path is missing (or vice versa)
// 3. Any provided certificate/key file cannot be read or is invalid PEM format
func LoadKafkaTLSConfig() (*KafkaTLSConfig, error) {
	cfg := &KafkaTLSConfig{}

	// Parse enabled flag
	enabledStr := os.Getenv("KAFKA_TLS_ENABLED")
	if enabledStr != "" {
		var err error
		cfg.Enabled, err = strconv.ParseBool(enabledStr)
		if err != nil {
			return nil, fmt.Errorf("invalid KAFKA_TLS_ENABLED value: %w", err)
		}
	}

	// If TLS is not enabled, return early
	if !cfg.Enabled {
		return cfg, nil
	}

	// Get CA cert path
	cfg.CACertPath = os.Getenv("KAFKA_TLS_CA_CERT_PATH")
	if cfg.CACertPath == "" {
		return nil, fmt.Errorf("CA certificate path is required when TLS is enabled")
	}

	// Check if CA cert file exists and is readable
	if _, err := os.Stat(cfg.CACertPath); err != nil {
		return nil, fmt.Errorf("failed to access CA certificate file: %w", err)
	}

	// Read CA cert to verify it's valid PEM
	caCert, err := os.ReadFile(cfg.CACertPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read CA certificate file: %w", err)
	}
	caCertPool := x509.NewCertPool()
	if !caCertPool.AppendCertsFromPEM(caCert) {
		return nil, fmt.Errorf("invalid PEM format in CA certificate file")
	}

	// Get client cert and key paths
	cfg.ClientCertPath = os.Getenv("KAFKA_TLS_CLIENT_CERT_PATH")
	cfg.ClientKeyPath = os.Getenv("KAFKA_TLS_CLIENT_KEY_PATH")

	// Validate client cert and key are both provided or both omitted
	if (cfg.ClientCertPath != "" && cfg.ClientKeyPath == "") || (cfg.ClientCertPath == "" && cfg.ClientKeyPath != "") {
		if cfg.ClientCertPath == "" {
			return nil, fmt.Errorf("client certificate path is required when client key path is provided")
		}
		return nil, fmt.Errorf("client key path is required when client certificate path is provided")
	}

	// If client cert and key are provided, validate them
	if cfg.ClientCertPath != "" {
		// Check client cert file exists
		if _, err := os.Stat(cfg.ClientCertPath); err != nil {
			return nil, fmt.Errorf("failed to access client certificate file: %w", err)
		}
		// Check client key file exists
		if _, err := os.Stat(cfg.ClientKeyPath); err != nil {
			return nil, fmt.Errorf("failed to access client key file: %w", err)
		}

		// Verify client cert and key are valid PEM and form a valid pair
		_, err := tls.LoadX509KeyPair(cfg.ClientCertPath, cfg.ClientKeyPath)
		if err != nil {
			return nil, fmt.Errorf("invalid client certificate/key pair: %w", err)
		}
	}

	// Parse skip verify flag
	skipVerifyStr := os.Getenv("KAFKA_TLS_SKIP_VERIFY")
	if skipVerifyStr != "" {
		var err error
		cfg.SkipVerify, err = strconv.ParseBool(skipVerifyStr)
		if err != nil {
			return nil, fmt.Errorf("invalid KAFKA_TLS_SKIP_VERIFY value: %w", err)
		}
	}

	return cfg, nil
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

// NewKafkaConsumerWithTLS creates a Kafka consumer client configured with TLS settings
// Parameters:
//   brokers: list of Kafka broker addresses
//   groupID: consumer group ID (unused for now, kept for interface compatibility)
//   tlsConfig: *KafkaTLSConfig (nil for non-TLS connections)
// Returns configured consumer client or error if TLS configuration is invalid
func NewKafkaConsumerWithTLS(brokers []string, groupID string, tlsConfig *KafkaTLSConfig) (*KafkaConsumer, error) {
	config := sarama.NewConfig()
	config.Consumer.Return.Errors = true
	config.Consumer.Offsets.AutoCommit.Enable = false // We will commit offsets manually during shutdown

	// If TLS config is nil or not enabled, return regular consumer
	if tlsConfig == nil || !tlsConfig.Enabled {
		consumer, err := sarama.NewConsumer(brokers, config)
		if err != nil {
			return nil, err
		}
		return &KafkaConsumer{
			consumer:         consumer,
			shutdownTimeout:  30 * time.Second, // Default timeout
			stopConsume:      make(chan struct{}),
			pendingOffsets:   make(map[string]map[int32]int64),
			topics:           []string{}, // Topics will be set later by caller
		}, nil
	}

	// Configure TLS
	tlsCfg := &tls.Config{
		InsecureSkipVerify: tlsConfig.SkipVerify,
	}

	// Load CA cert
	caCert, err := os.ReadFile(tlsConfig.CACertPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read CA certificate: %w", err)
	}
	caCertPool := x509.NewCertPool()
	if !caCertPool.AppendCertsFromPEM(caCert) {
		return nil, fmt.Errorf("failed to append CA certificate to pool")
	}
	tlsCfg.RootCAs = caCertPool

	// Load client cert/key if provided
	if tlsConfig.ClientCertPath != "" && tlsConfig.ClientKeyPath != "" {
		cert, err := tls.LoadX509KeyPair(tlsConfig.ClientCertPath, tlsConfig.ClientKeyPath)
		if err != nil {
			return nil, fmt.Errorf("failed to load client certificate/key pair: %w", err)
		}
		tlsCfg.Certificates = []tls.Certificate{cert}
	}

	// Apply TLS config to sarama
	config.Net.TLS.Enable = true
	config.Net.TLS.Config = tlsCfg

	// Create consumer with TLS config
	consumer, err := sarama.NewConsumer(brokers, config)
	if err != nil {
		return nil, fmt.Errorf("failed to create TLS-enabled Kafka consumer: %w", err)
	}

	return &KafkaConsumer{
		consumer:         consumer,
		shutdownTimeout:  30 * time.Second, // Default timeout
		stopConsume:      make(chan struct{}),
		pendingOffsets:   make(map[string]map[int32]int64),
		topics:           []string{}, // Topics will be set later by caller
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
	offsetManager.Commit()
	log.Println("Offsets committed successfully: shutting down Kafka consumer")
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

// Close implements the io.Closer interface for backward compatibility with tests
func (c *KafkaConsumer) Close() error {
	ctx, cancel := context.WithTimeout(context.Background(), c.shutdownTimeout)
	defer cancel()
	return c.Shutdown(ctx)
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

	// Load TLS configuration
	tlsConfig, err := LoadKafkaTLSConfig()
	if err != nil {
		log.Fatalf("Failed to load Kafka TLS configuration: %v", err)
	}

	// Initialize health checker
	healthChecker := DefaultHealthChecker{}

	// Initialize Kafka consumer
	var kafkaConsumer *KafkaConsumer
	if tlsConfig.Enabled {
		// Get consumer group ID
		groupID := os.Getenv("KAFKA_CONSUMER_GROUP_ID")
		if groupID == "" {
			groupID = "kafka-collector-group"
		}
		kafkaConsumer, err = NewKafkaConsumerWithTLS([]string{kafkaAddr}, groupID, tlsConfig)
	} else {
		// Use regular non-TLS consumer for backward compatibility
		config := sarama.NewConfig()
		config.Consumer.Return.Errors = true
		config.Consumer.Offsets.AutoCommit.Enable = false // We will commit offsets manually during shutdown
		kafkaConsumer, err = NewKafkaConsumer([]string{kafkaAddr}, topics, config)
	}
	if err != nil {
		log.Fatalf("Failed to initialize Kafka consumer: %v", err)
	}
	// Set topics on the consumer for TLS case
	if tlsConfig.Enabled {
		kafkaConsumer.topics = topics
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
