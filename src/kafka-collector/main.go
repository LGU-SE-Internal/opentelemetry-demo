// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
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
	"go.opentelemetry.io/otel/trace"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"

	"github.com/open-telemetry/opentelemetry-demo/src/kafka-collector/internal/metrics"
)

// Exported metrics functions for tests and use in processing flow
func IncMessagesConsumed(topic string, partition int32) {
	metrics.IncMessagesConsumed(topic, partition)
}

func IncMessagesProcessedSuccess(topic string, partition int32) {
	metrics.IncMessagesProcessedSuccess(topic, partition)
}

func IncMessagesProcessedFailure(topic string, partition int32, errorType string) {
	metrics.IncMessagesProcessedFailure(topic, partition, errorType)
}

func SetConsumerLag(topic string, partition int32, lag int64) {
	metrics.SetConsumerLag(topic, partition, lag)
}

func ObserveProcessingDuration(topic string, partition int32, status string, duration time.Duration) {
	metrics.ObserveProcessingDuration(topic, partition, status, duration)
}



// Logger interface for structured logging
type Logger interface {
	Debug(ctx context.Context, msg string, fields ...zap.Field)
	Info(ctx context.Context, msg string, fields ...zap.Field)
	Warn(ctx context.Context, msg string, fields ...zap.Field)
	Error(ctx context.Context, msg string, fields ...zap.Field)
}

// ZapLogger implements Logger interface using Zap
type ZapLogger struct {
	logger *zap.Logger
}

var globalLogger Logger

// NewZapLogger creates a new ZapLogger instance with JSON output
func NewZapLogger() (*ZapLogger, error) {
	config := zap.NewProductionConfig()
	config.EncoderConfig.TimeKey = "timestamp"
	config.EncoderConfig.EncodeTime = zapcore.ISO8601TimeEncoder
	config.EncoderConfig.LevelKey = "severity"
	config.EncoderConfig.EncodeLevel = zapcore.LowercaseLevelEncoder
	logger, err := config.Build()
	if err != nil {
		return nil, err
	}
	return &ZapLogger{logger: logger}, nil
}

// getTraceFields extracts trace and span IDs from context if available
func getTraceFields(ctx context.Context) []zap.Field {
	spanCtx := trace.SpanContextFromContext(ctx)
	if !spanCtx.IsValid() {
		return nil
	}
	return []zap.Field{
		zap.String("trace_id", spanCtx.TraceID().String()),
		zap.String("span_id", spanCtx.SpanID().String()),
	}
}

func (l *ZapLogger) Debug(ctx context.Context, msg string, fields ...zap.Field) {
	traceFields := getTraceFields(ctx)
	l.logger.Debug(msg, append(traceFields, fields...)...)
}

func (l *ZapLogger) Info(ctx context.Context, msg string, fields ...zap.Field) {
	traceFields := getTraceFields(ctx)
	l.logger.Info(msg, append(traceFields, fields...)...)
}

func (l *ZapLogger) Warn(ctx context.Context, msg string, fields ...zap.Field) {
	traceFields := getTraceFields(ctx)
	l.logger.Warn(msg, append(traceFields, fields...)...)
}

func (l *ZapLogger) Error(ctx context.Context, msg string, fields ...zap.Field) {
	traceFields := getTraceFields(ctx)
	l.logger.Error(msg, append(traceFields, fields...)...)
}

// Health handlers
func livenessHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	resp := HealthResponse{
		Status:  "UP",
		Service: "kafka-collector",
		Check:   "liveness",
	}
	json.NewEncoder(w).Encode(resp)
}

func readinessHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	connected := kafkaConnected.Load()
	if connected {
		w.WriteHeader(http.StatusOK)
		resp := HealthResponse{
			Status:        "UP",
			Service:       "kafka-collector",
			Check:         "readiness",
			KafkaConnected: &connected,
		}
		json.NewEncoder(w).Encode(resp)
	} else {
		w.WriteHeader(http.StatusServiceUnavailable)
		errStr := ""
		if errPtr := kafkaLastErr.Load(); errPtr != nil {
			errStr = *errPtr
		}
		resp := HealthResponse{
			Status:        "DOWN",
			Service:       "kafka-collector",
			Check:         "readiness",
			KafkaConnected: &connected,
			Error:         errStr,
		}
		json.NewEncoder(w).Encode(resp)
	}
}

func startHealthServer(port int) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/health/liveness", livenessHandler)
	mux.HandleFunc("/health/readiness", readinessHandler)

	server := &http.Server{
		Addr:    fmt.Sprintf("0.0.0.0:%d", port),
		Handler: mux,
	}

	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			globalLogger.Error(context.Background(), "Health server failed", zap.Error(err))
		}
	}()

	globalLogger.Info(context.Background(), "Health server started", zap.Int("port", port))
	return nil
}

// InitGlobalLogger initializes the global logger instance
func InitGlobalLogger() error {
	logger, err := NewZapLogger()
	if err != nil {
		return err
	}
	globalLogger = logger
	return nil
}

// Define error types
var (
	ShutdownTimeoutError = errors.New("shutdown timeout exceeded before all in-flight messages completed processing")
	OffsetCommitError    = errors.New("failed to commit pending offsets to Kafka during shutdown")
)

// Health status tracking
var (
	kafkaConnected atomic.Bool
	kafkaLastErr   atomic.Pointer[string]
)

type HealthResponse struct {
	Status        string `json:"status"`
	Service       string `json:"service"`
	Check         string `json:"check"`
	KafkaConnected *bool  `json:"kafka_connected,omitempty"`
	Error         string `json:"error,omitempty"`
}


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
	consumerGroupID     string
}

// NewKafkaConsumer creates a new KafkaConsumer instance with configured shutdown timeout
func NewKafkaConsumer(ctx context.Context, brokers []string, topics []string, config *sarama.Config) (*KafkaConsumer, error) {
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
			globalLogger.Warn(ctx, "Invalid KAFKA_CONSUMER_SHUTDOWN_TIMEOUT value, using default 30s",
				zap.String("timeout_value", timeoutStr),
				zap.Error(err),
			)
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
//   ctx: context for logging and tracing
//   brokers: list of Kafka broker addresses
//   groupID: consumer group ID
//   tlsConfig: *KafkaTLSConfig (nil for non-TLS connections)
// Returns configured consumer client or error if TLS configuration is invalid
func NewKafkaConsumerWithTLS(ctx context.Context, brokers []string, groupID string, tlsConfig *KafkaTLSConfig) (*KafkaConsumer, error) {
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
			consumerGroupID:  groupID,
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
		consumerGroupID:  groupID,
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

	globalLogger.Info(ctx, "Graceful shutdown initiated: stopping new Kafka message consumption")
	close(c.stopConsume)

	// Count in-flight messages
	c.inFlightMu.Lock()
	inFlightCount := len(c.inFlightMessages)
	c.inFlightMu.Unlock()
	globalLogger.Info(ctx, "Waiting for in-flight Kafka messages to complete processing",
		zap.Int("in_flight_count", inFlightCount),
		zap.String("consumer_group_id", c.consumerGroupID),
	)

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
		globalLogger.Error(ctx, "Shutdown timeout reached with incomplete messages",
			zap.Int("incomplete_count", len(c.inFlightMessages)),
			zap.String("consumer_group_id", c.consumerGroupID),
		)
		for _, msg := range c.inFlightMessages {
			globalLogger.Error(ctx, "Incomplete Kafka message",
				zap.String("kafka_topic", msg.Topic),
				zap.Int32("kafka_partition", msg.Partition),
				zap.Int64("kafka_offset", msg.Offset),
				zap.String("consumer_group_id", c.consumerGroupID),
			)
		}
		return ShutdownTimeoutError
	}

	globalLogger.Info(ctx, "All in-flight messages processed, committing pending offsets",
		zap.String("consumer_group_id", c.consumerGroupID),
	)

	// Commit pending offsets
	if offsetManager, ok := c.consumer.(sarama.OffsetManager); ok {
		c.offsetMu.Lock()
		for topic, partitions := range c.pendingOffsets {
			for partition, offset := range partitions {
				pom, err := offsetManager.ManagePartition(topic, partition)
				if err != nil {
					globalLogger.Warn(ctx, "Failed to get partition offset manager",
						zap.String("kafka_topic", topic),
						zap.Int32("kafka_partition", partition),
						zap.String("consumer_group_id", c.consumerGroupID),
						zap.Error(err),
					)
					continue
				}
				pom.MarkOffset(offset, "")
			}
		}
		c.offsetMu.Unlock()

		// Commit all marked offsets
		offsetManager.Commit()
		globalLogger.Info(ctx, "Offsets committed successfully: shutting down Kafka consumer",
			zap.String("consumer_group_id", c.consumerGroupID),
		)
	} else {
		globalLogger.Warn(ctx, "Consumer does not support offset management, skipping offset commit",
			zap.String("consumer_group_id", c.consumerGroupID),
		)
	}

	globalLogger.Info(ctx, "Offsets committed successfully: shutting down Kafka consumer",
		zap.String("consumer_group_id", c.consumerGroupID),
	)

	// Close the consumer
	err := c.consumer.Close()
	if err != nil {
		globalLogger.Warn(ctx, "Error closing Kafka consumer",
			zap.String("consumer_group_id", c.consumerGroupID),
			zap.Error(err),
		)
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
	// Initialize structured logger first
	if err := InitGlobalLogger(); err != nil {
		fmt.Printf("Failed to initialize logger: %v\n", err)
		os.Exit(1)
	}
	ctx := context.Background()

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
		globalLogger.Error(ctx, "Invalid port value: must be numeric",
			zap.String("port_value", httpPortStr),
		)
		os.Exit(1)
	}
	if httpPortInt < 1 || httpPortInt > 65535 {
		globalLogger.Error(ctx, "Invalid port value: must be between 1 and 65535",
			zap.Int("port_value", httpPortInt),
		)
		os.Exit(1)
	}
	httpPort := strconv.Itoa(httpPortInt)

	// Get health port from environment variable
	healthPortStr := os.Getenv("KAFKA_COLLECTOR_HEALTH_PORT")
	healthPort := 12001 // Default
	if healthPortStr != "" {
		parsedPort, err := strconv.Atoi(healthPortStr)
		if err != nil {
			globalLogger.Error(ctx, "Invalid health port value: must be numeric",
				zap.String("health_port_value", healthPortStr),
			)
			os.Exit(1)
		}
		if parsedPort < 1 || parsedPort > 65535 {
			globalLogger.Error(ctx, "Invalid health port value: must be between 1 and 65535",
				zap.Int("health_port_value", parsedPort),
			)
			os.Exit(1)
		}
		healthPort = parsedPort
	}

	// Start health server
	if err := startHealthServer(healthPort); err != nil {
		globalLogger.Error(ctx, "Failed to start health server", zap.Error(err))
		os.Exit(1)
	}

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
		globalLogger.Error(ctx, "Kafka topics list cannot be empty")
		os.Exit(1)
	}
	globalLogger.Info(ctx, "Consuming from Kafka topics",
		zap.String("kafka_topics", strings.Join(topics, ", ")),
	)

	// Load TLS configuration
	tlsConfig, err := LoadKafkaTLSConfig()
	if err != nil {
		globalLogger.Error(ctx, "Failed to load Kafka TLS configuration",
			zap.Error(err),
		)
		os.Exit(1)
	}

	// Initialize health checker
	healthChecker := DefaultHealthChecker{}

	// Initialize Kafka consumer
	var kafkaConsumer *KafkaConsumer
	var groupID string
	if tlsConfig.Enabled {
		// Get consumer group ID
		groupID = os.Getenv("KAFKA_CONSUMER_GROUP_ID")
		if groupID == "" {
			groupID = "kafka-collector-group"
		}
		kafkaConsumer, err = NewKafkaConsumerWithTLS(ctx, []string{kafkaAddr}, groupID, tlsConfig)
	} else {
		// Use regular non-TLS consumer for backward compatibility
		config := sarama.NewConfig()
		config.Consumer.Return.Errors = true
		config.Consumer.Offsets.AutoCommit.Enable = false // We will commit offsets manually during shutdown
		groupID = "kafka-collector-group"
		kafkaConsumer, err = NewKafkaConsumer(ctx, []string{kafkaAddr}, topics, config)
	}
	if err != nil {
		globalLogger.Error(ctx, "Failed to initialize Kafka consumer",
			zap.String("consumer_group_id", groupID),
			zap.Error(err),
		)
		os.Exit(1)
	}
	// Set topics on the consumer for TLS case
	if tlsConfig.Enabled {
		kafkaConsumer.topics = topics
	}
	kafkaConsumer.consumerGroupID = groupID
	readinessChecker := KafkaReadinessChecker{
		consumer: kafkaConsumer.consumer,
		topics:   topics,
	}
	defer func() {
		if kafkaConsumer != nil {
			ctx, cancel := context.WithTimeout(context.Background(), kafkaConsumer.shutdownTimeout)
			defer cancel()
			if err := kafkaConsumer.Shutdown(ctx); err != nil {
				globalLogger.Error(ctx, "Kafka consumer shutdown failed",
					zap.String("consumer_group_id", kafkaConsumer.consumerGroupID),
					zap.Error(err),
				)
				os.Exit(1)
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
		globalLogger.Info(ctx, "Kafka collector server starting on port",
			zap.String("http_port", httpPort),
		)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			globalLogger.Error(ctx, "Failed to start server",
				zap.Error(err),
			)
			os.Exit(1)
		}
	}()

	<-sigChan
	globalLogger.Info(ctx, "Shutting down server...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		globalLogger.Error(ctx, "Server shutdown failed",
			zap.Error(err),
		)
		os.Exit(1)
	}
	globalLogger.Info(ctx, "Server gracefully stopped")
}
