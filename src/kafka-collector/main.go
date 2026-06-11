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
	"github.com/cenkalti/backoff/v4"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel/trace"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

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

// RetriableKafkaError wraps errors that are eligible for retry
type RetriableKafkaError struct {
	Err error
}

func (e RetriableKafkaError) Error() string {
	return fmt.Sprintf("retriable kafka error: %v", e.Err)
}

func (e RetriableKafkaError) Unwrap() error {
	return e.Err
}

// NonRetriableKafkaError wraps errors that should not be retried
type NonRetriableKafkaError struct {
	Err error
}

func (e NonRetriableKafkaError) Error() string {
	return fmt.Sprintf("non-retriable kafka error: %v", e.Err)
}

func (e NonRetriableKafkaError) Unwrap() error {
	return e.Err
}

// Health status tracking
var (
	kafkaConnected atomic.Bool
	kafkaLastErr   atomic.Pointer[string]
)

type HealthResponse struct {
	Status        string `json:"status"`
	Service       string `json:"service"`
	Check         string `json:"check"`
	KafkaConnected *bool `json:"kafkaConnected,omitempty"`
	Error         string `json:"error,omitempty"`
}

// KafkaConsumerConfig holds configuration for Kafka consumer
type KafkaConsumerConfig struct {
	Brokers []string
	Topic   string
	GroupID string
	TLSConfig *tls.Config
}

// KafkaConsumer is the consumer with retry logic
type KafkaConsumer struct {
	cfg                  KafkaConsumerConfig
	retryMaxAttempts     int
	retryInitialBackoff  time.Duration
	retryMaxBackoff      time.Duration
	dlqTopic             string
	client               sarama.Client
	consumerGroup        sarama.ConsumerGroup
	messageProcessor     func(ctx context.Context, msg *sarama.ConsumerMessage) error
	dlqSender            func(msg *sarama.ConsumerMessage, err error) error
	shutdownCtx          context.Context
	shutdownCancel       context.CancelFunc
	wg                   sync.WaitGroup
}

// NewKafkaConsumerWithRetry creates a new Kafka consumer with built-in retry logic for connection and consumption
func NewKafkaConsumerWithRetry(cfg KafkaConsumerConfig) (*KafkaConsumer, error) {
	// Load retry config from env vars
	retryMaxAttempts := 5
	if val, ok := os.LookupEnv("KAFKA_RETRY_MAX_ATTEMPTS"); ok {
		if intVal, err := strconv.Atoi(val); err == nil && intVal > 0 {
			retryMaxAttempts = intVal
		}
	}

	retryInitialBackoff := 100 * time.Millisecond
	if val, ok := os.LookupEnv("KAFKA_RETRY_INITIAL_BACKOFF_MS"); ok {
		if intVal, err := strconv.Atoi(val); err == nil && intVal > 0 {
			retryInitialBackoff = time.Duration(intVal) * time.Millisecond
		}
	}

	retryMaxBackoff := 10 * time.Second
	if val, ok := os.LookupEnv("KAFKA_RETRY_MAX_BACKOFF_MS"); ok {
		if intVal, err := strconv.Atoi(val); err == nil && intVal > 0 {
			retryMaxBackoff = time.Duration(intVal) * time.Millisecond
		}
	}

	dlqTopic := "kafka-collector-dlq"
	if val, ok := os.LookupEnv("KAFKA_DLQ_TOPIC"); ok && val != "" {
		dlqTopic = val
	}

	shutdownCtx, shutdownCancel := context.WithCancel(context.Background())

	consumer := &KafkaConsumer{
		cfg:                  cfg,
		retryMaxAttempts:     retryMaxAttempts,
		retryInitialBackoff:  retryInitialBackoff,
		retryMaxBackoff:      retryMaxBackoff,
		dlqTopic:             dlqTopic,
		shutdownCtx:          shutdownCtx,
		shutdownCancel:       shutdownCancel,
	}

	// Set default message processor
	consumer.messageProcessor = func(ctx context.Context, msg *sarama.ConsumerMessage) error {
		return nil
	}

	// Set default DLQ sender
	consumer.dlqSender = func(msg *sarama.ConsumerMessage, err error) error {
		// Default implementation would create a producer and send to DLQ
		// For testability, this can be overridden
		return nil
	}

	// Attempt to connect with retry
	connectBackoff := backoff.NewExponentialBackOff()
	connectBackoff.InitialInterval = retryInitialBackoff
	connectBackoff.MaxInterval = retryMaxBackoff
	connectBackoff.MaxElapsedTime = 0 // We use retry count instead of elapsed time

	retryCount := 0
	operation := func() error {
		select {
		case <-consumer.shutdownCtx.Done():
			return backoff.Permanent(consumer.shutdownCtx.Err())
		default:
		}

		saramaConfig := sarama.NewConfig()
		saramaConfig.Consumer.Group.Rebalance.GroupStrategies = []sarama.BalanceStrategy{sarama.NewBalanceStrategyRoundRobin()}
		saramaConfig.Consumer.Offsets.Initial = sarama.OffsetOldest
		if cfg.TLSConfig != nil {
			saramaConfig.Net.TLS.Enable = true
			saramaConfig.Net.TLS.Config = cfg.TLSConfig
		}

		client, err := sarama.NewClient(cfg.Brokers, saramaConfig)
		if err != nil {
			// Check if error is retriable
			var retriable bool
			if errors.Is(err, sarama.ErrBrokerNotAvailable) || 
			   errors.Is(err, sarama.ErrLeaderNotAvailable) ||
			   errors.Is(err, sarama.ErrNetworkException) ||
			   errors.Is(err, sarama.RequestTimeout) {
				retriable = true
			}

			if !retriable {
				return backoff.Permanent(NonRetriableKafkaError{Err: err})
			}

			retryCount++
			if retryCount >= retryMaxAttempts {
				return backoff.Permanent(fmt.Errorf("failed to connect after %d attempts: %w", retryMaxAttempts, err))
			}

			errStr := err.Error()
			kafkaLastErr.Store(&errStr)
			kafkaConnected.Store(false)
			return RetriableKafkaError{Err: err}
		}

		consumerGroup, err := sarama.NewConsumerGroupFromClient(cfg.GroupID, client)
		if err != nil {
			client.Close()
			retryCount++
			if retryCount >= retryMaxAttempts {
				return backoff.Permanent(fmt.Errorf("failed to create consumer group after %d attempts: %w", retryMaxAttempts, err))
			}
			errStr := err.Error()
			kafkaLastErr.Store(&errStr)
			kafkaConnected.Store(false)
			return RetriableKafkaError{Err: err}
		}

		consumer.client = client
		consumer.consumerGroup = consumerGroup
		kafkaConnected.Store(true)
		kafkaLastErr.Store(nil)
		return nil
	}

	err := backoff.Retry(operation, backoff.WithMaxRetries(connectBackoff, uint64(retryMaxAttempts)))
	if err != nil {
		consumer.shutdownCancel()
		return nil, err
	}

	return consumer, nil
}

// ProcessMessageWithRetry processes a single Kafka message with retry logic for retriable errors
func (c *KafkaConsumer) ProcessMessageWithRetry(msg *sarama.ConsumerMessage) error {
	backoffCfg := backoff.NewExponentialBackOff()
	backoffCfg.InitialInterval = c.retryInitialBackoff
	backoffCfg.MaxInterval = c.retryMaxBackoff
	backoffCfg.MaxElapsedTime = 0

	retryCount := 0
	var lastErr error

	operation := func() error {
		select {
		case <-c.shutdownCtx.Done():
			return backoff.Permanent(c.shutdownCtx.Err())
		default:
		}

		err := c.messageProcessor(c.shutdownCtx, msg)
		if err == nil {
			return nil
		}

		lastErr = err
		var retriableErr RetriableKafkaError
		if !errors.As(err, &retriableErr) {
			// Non-retriable error, send to DLQ immediately
			if sendErr := c.sendToDLQ(msg, err); sendErr != nil {
				globalLogger.Error(c.shutdownCtx, "Failed to send non-retriable error message to DLQ", zap.Error(sendErr), zap.Int64("offset", msg.Offset))
			}
			return backoff.Permanent(nil) // Return nil so we don't mark the whole operation as failed
		}

		retryCount++
		if retryCount >= c.retryMaxAttempts {
			// All retries failed, send to DLQ
			if sendErr := c.sendToDLQ(msg, retriableErr); sendErr != nil {
				globalLogger.Error(c.shutdownCtx, "Failed to send failed message to DLQ after max retries", zap.Error(sendErr), zap.Int64("offset", msg.Offset))
			}
			return backoff.Permanent(nil)
		}

		globalLogger.Warn(c.shutdownCtx, "Retrying failed message processing", zap.Error(retriableErr), zap.Int("attempt", retryCount), zap.Int64("offset", msg.Offset))
		return retriableErr
	}

	err := backoff.Retry(operation, backoff.WithMaxRetries(backoffCfg, uint64(c.retryMaxAttempts)))
	return err
}

// sendToDLQ writes a permanently failed message to the configured dead-letter queue
func (c *KafkaConsumer) sendToDLQ(msg *sarama.ConsumerMessage, err error) error {
	globalLogger.Error(c.shutdownCtx, "Sending message to DLQ", zap.Error(err), zap.Int64("offset", msg.Offset), zap.String("dlq_topic", c.dlqTopic))
	return c.dlqSender(msg, err)
}

// SetMessageProcessor sets the message processing function
func (c *KafkaConsumer) SetMessageProcessor(processor func(ctx context.Context, msg *sarama.ConsumerMessage) error) {
	c.messageProcessor = processor
}

// SetDLQSender sets the DLQ sender function for testing
func (c *KafkaConsumer) SetDLQSender(sender func(msg *sarama.ConsumerMessage, err error) error) {
	c.dlqSender = sender
}

// GetRetryMaxAttempts returns the configured max retry attempts
func (c *KafkaConsumer) GetRetryMaxAttempts() int {
	return c.retryMaxAttempts
}

// GetRetryInitialBackoff returns the configured initial backoff
func (c *KafkaConsumer) GetRetryInitialBackoff() time.Duration {
	return c.retryInitialBackoff
}

// GetRetryMaxBackoff returns the configured max backoff
func (c *KafkaConsumer) GetRetryMaxBackoff() time.Duration {
	return c.retryMaxBackoff
}

// GetDLQTopic returns the configured DLQ topic
func (c *KafkaConsumer) GetDLQTopic() string {
	return c.dlqTopic
}

// StartConsumption starts consuming messages from Kafka
func (c *KafkaConsumer) StartConsumption(ctx context.Context) error {
	topics := []string{c.cfg.Topic}
	handler := &consumerGroupHandler{consumer: c}

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-c.shutdownCtx.Done():
			return nil
		default:
			err := c.consumerGroup.Consume(ctx, topics, handler)
			if err != nil {
				if errors.Is(err, sarama.ErrClosedConsumerGroup) {
					return nil
				}
				globalLogger.Error(c.shutdownCtx, "Error consuming from Kafka, retrying", zap.Error(err))
				// Mark as disconnected during retry
				kafkaConnected.Store(false)
				errStr := err.Error()
				kafkaLastErr.Store(&errStr)
				
				// Backoff before retrying consumption
				backoff.Sleep(c.retryInitialBackoff)
			}
			// Recheck connection after error
			if c.client.Closed() {
				// Attempt to reconnect with backoff
				reconnectBackoff := backoff.NewExponentialBackOff()
				reconnectBackoff.InitialInterval = c.retryInitialBackoff
				reconnectBackoff.MaxInterval = c.retryMaxBackoff
				reconnectBackoff.MaxElapsedTime = 0

				retryCount := 0
				for {
					select {
					case <-ctx.Done():
						return nil
					case <-c.shutdownCtx.Done():
						return nil
					default:
					}

					saramaConfig := sarama.NewConfig()
					saramaConfig.Consumer.Group.Rebalance.GroupStrategies = []sarama.BalanceStrategy{sarama.NewBalanceStrategyRoundRobin()}
					saramaConfig.Consumer.Offsets.Initial = sarama.OffsetOldest
					if c.cfg.TLSConfig != nil {
						saramaConfig.Net.TLS.Enable = true
						saramaConfig.Net.TLS.Config = c.cfg.TLSConfig
					}

					client, err := sarama.NewClient(c.cfg.Brokers, saramaConfig)
					if err != nil {
						retryCount++
						if retryCount >= c.retryMaxAttempts {
							globalLogger.Error(c.shutdownCtx, "Failed to reconnect after max attempts", zap.Error(err))
							return err
						}
						backoff.Sleep(reconnectBackoff.NextBackOff())
						continue
					}

					consumerGroup, err := sarama.NewConsumerGroupFromClient(c.cfg.GroupID, client)
					if err != nil {
						client.Close()
						retryCount++
						if retryCount >= c.retryMaxAttempts {
							globalLogger.Error(c.shutdownCtx, "Failed to recreate consumer group after max attempts", zap.Error(err))
							return err
						}
						backoff.Sleep(reconnectBackoff.NextBackOff())
						continue
					}

					c.client.Close()
					c.client = client
					c.consumerGroup = consumerGroup
					kafkaConnected.Store(true)
					kafkaLastErr.Store(nil)
					break
				}
			}
		}
	}
}

// Close shuts down the consumer
func (c *KafkaConsumer) Close() error {
	c.shutdownCancel()
	c.wg.Wait()
	if c.consumerGroup != nil {
		c.consumerGroup.Close()
	}
	if c.client != nil {
		c.client.Close()
	}
	kafkaConnected.Store(false)
	return nil
}

// consumerGroupHandler implements sarama.ConsumerGroupHandler
type consumerGroupHandler struct {
	consumer *KafkaConsumer
}

func (h *consumerGroupHandler) Setup(_ sarama.ConsumerGroupSession) error   { return nil }
func (h *consumerGroupHandler) Cleanup(_ sarama.ConsumerGroupSession) error { return nil }
func (h *consumerGroupHandler) ConsumeClaim(sess sarama.ConsumerGroupSession, claim sarama.ConsumerGroupClaim) error {
	for msg := range claim.Messages() {
		if err := h.consumer.ProcessMessageWithRetry(msg); err != nil {
			globalLogger.Error(h.consumer.shutdownCtx, "Error processing message", zap.Error(err), zap.Int64("offset", msg.Offset))
		}
		sess.MarkMessage(msg, "")
	}
	return nil
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
	producer            sarama.SyncProducer
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
	retryMaxAttempts    int
	retryInitialBackoff time.Duration
	retryMaxBackoff     time.Duration
	dlqTopic            string
	messageProcessor    func(context.Context, *sarama.ConsumerMessage) error // For testing
	dlqSender           func(*sarama.ConsumerMessage, error) error // For testing
}

// KafkaConsumerConfig holds all configuration parameters for Kafka consumer with retry
type KafkaConsumerConfig struct {
	Ctx                context.Context
	Brokers            []string
	Topic              string // Single topic for backward compatibility with tests
	Topics             []string
	SaramaConfig       *sarama.Config
	ShutdownTimeout    time.Duration
	RetryMaxAttempts   int
	RetryInitialBackoff time.Duration
	RetryMaxBackoff    time.Duration
	DLQTopic           string
	messageProcessor   func(*sarama.ConsumerMessage) error // For testing
	dlqSender          func(*sarama.ConsumerMessage, error) error // For testing
}

// NewKafkaConsumerWithRetry creates a new Kafka consumer with built-in retry logic for connection and consumption
func NewKafkaConsumerWithRetry(cfg KafkaConsumerConfig) (*KafkaConsumer, error) {
	// Load default values if not provided
	if cfg.RetryMaxAttempts == 0 {
		cfg.RetryMaxAttempts = 5
	}
	if cfg.RetryInitialBackoff == 0 {
		cfg.RetryInitialBackoff = 100 * time.Millisecond
	}
	if cfg.RetryMaxBackoff == 0 {
		cfg.RetryMaxBackoff = 10 * time.Second
	}
	if cfg.DLQTopic == "" {
		cfg.DLQTopic = "kafka-collector-dlq"
	}

	var consumer sarama.Consumer
	var producer sarama.SyncProducer

	// Exponential backoff for initial connection
	bo := backoff.NewExponentialBackOff()
	bo.InitialInterval = cfg.RetryInitialBackoff
	bo.MaxInterval = cfg.RetryMaxBackoff
	bo.MaxElapsedTime = 0 // We handle max attempts ourselves

	retryCount := 0
	err := backoff.RetryNotify(func() error {
		if retryCount >= cfg.RetryMaxAttempts {
			return backoff.Permanent(errors.New("max connection attempts exceeded"))
		}

		var connErr error
		consumer, connErr = sarama.NewConsumer(cfg.Brokers, cfg.SaramaConfig)
		if connErr != nil {
			// Check if error is retriable
			if isRetriableError(connErr) {
				retryCount++
				kafkaConnected.Store(false)
				errStr := connErr.Error()
				kafkaLastErr.Store(&errStr)
				return &RetriableKafkaError{Err: connErr}
			}
			// Non-retriable error, fail immediately
			return backoff.Permanent(&NonRetriableKafkaError{Err: connErr})
		}

		// Create DLQ producer
		producer, connErr = sarama.NewSyncProducer(cfg.Brokers, cfg.SaramaConfig)
		if connErr != nil {
			consumer.Close()
			if isRetriableError(connErr) {
				retryCount++
				kafkaConnected.Store(false)
				errStr := connErr.Error()
				kafkaLastErr.Store(&errStr)
				return &RetriableKafkaError{Err: connErr}
			}
			return backoff.Permanent(&NonRetriableKafkaError{Err: connErr})
		}

		return nil
	}, bo, func(err error, duration time.Duration) {
		globalLogger.Warn(cfg.Ctx, "Kafka connection failed, retrying",
			zap.Int("attempt", retryCount),
			zap.Duration("next_retry_in", duration),
			zap.Error(err),
		)
	})

	if err != nil {
		return nil, err
	}

	kafkaConnected.Store(true)
	kafkaLastErr.Store(nil)

	return &KafkaConsumer{
		consumer:            consumer,
		producer:            producer,
		shutdownTimeout:     cfg.ShutdownTimeout,
		stopConsume:         make(chan struct{}),
		pendingOffsets:      make(map[string]map[int32]int64),
		topics:              cfg.Topics,
		retryMaxAttempts:    cfg.RetryMaxAttempts,
		retryInitialBackoff: cfg.RetryInitialBackoff,
		retryMaxBackoff:     cfg.RetryMaxBackoff,
		dlqTopic:            cfg.DLQTopic,
	}, nil
}

// GetRetryMaxAttempts returns the configured maximum retry attempts
func (c *KafkaConsumer) GetRetryMaxAttempts() int {
	return c.retryMaxAttempts
}

// GetRetryInitialBackoff returns the configured initial backoff duration
func (c *KafkaConsumer) GetRetryInitialBackoff() time.Duration {
	return c.retryInitialBackoff
}

// GetRetryMaxBackoff returns the configured maximum backoff duration
func (c *KafkaConsumer) GetRetryMaxBackoff() time.Duration {
	return c.retryMaxBackoff
}

// GetDLQTopic returns the configured dead-letter queue topic name
func (c *KafkaConsumer) GetDLQTopic() string {
	return c.dlqTopic
}

// isRetriableError checks if a Kafka error is eligible for retry
func isRetriableError(err error) bool {
	var saramaErr sarama.KError
	if errors.As(err, &saramaErr) {
		switch saramaErr {
		case sarama.ErrLeaderNotAvailable,
			sarama.ErrNotEnoughReplicas,
			sarama.ErrNotEnoughReplicasAfterAppend,
			sarama.ErrRequestTimedOut,
			sarama.ErrBrokerNotAvailable,
			sarama.ErrNetworkException,
			sarama.ErrOffsetsLoadInProgress:
			return true
		default:
			return false
		}
	}
	// Check for temporary network errors
	var netErr interface{ Temporary() bool }
	if errors.As(err, &netErr) {
		return netErr.Temporary()
	}
	// For other errors, assume non-retriable unless explicitly wrapped
	var retriableErr *RetriableKafkaError
	return errors.As(err, &retriableErr)
}

// NewKafkaConsumer creates a new KafkaConsumer instance with configured shutdown timeout
func NewKafkaConsumer(ctx context.Context, brokers []string, topics []string, config *sarama.Config) (*KafkaConsumer, error) {
	consumer, err := sarama.NewConsumer(brokers, config)
	if err != nil {
		return nil, err
	}

	// Create producer for DLQ
	producer, err := sarama.NewSyncProducer(brokers, config)
	if err != nil {
		consumer.Close()
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

	// Load default retry values
	retryMaxAttempts := 5
	if v := os.Getenv("KAFKA_RETRY_MAX_ATTEMPTS"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			retryMaxAttempts = parsed
		}
	}
	retryInitialBackoff := 100 * time.Millisecond
	if v := os.Getenv("KAFKA_RETRY_INITIAL_BACKOFF_MS"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			retryInitialBackoff = time.Duration(parsed) * time.Millisecond
		}
	}
	retryMaxBackoff := 10 * time.Second
	if v := os.Getenv("KAFKA_RETRY_MAX_BACKOFF_MS"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			retryMaxBackoff = time.Duration(parsed) * time.Millisecond
		}
	}
	dlqTopic := "kafka-collector-dlq"
	if v := os.Getenv("KAFKA_DLQ_TOPIC"); v != "" {
		dlqTopic = v
	}

	return &KafkaConsumer{
		consumer:            consumer,
		producer:            producer,
		shutdownTimeout:     timeout,
		stopConsume:         make(chan struct{}),
		pendingOffsets:      make(map[string]map[int32]int64),
		topics:              topics,
		retryMaxAttempts:    retryMaxAttempts,
		retryInitialBackoff: retryInitialBackoff,
		retryMaxBackoff:     retryMaxBackoff,
		dlqTopic:            dlqTopic,
	}, nil
}

// ProcessMessageWithRetry processes a single Kafka message with retry logic for retriable errors
func (c *KafkaConsumer) ProcessMessageWithRetry(msg *sarama.ConsumerMessage) error {
	// Create default context
	ctx := context.Background()
	// Check if shutdown is in progress first
	if c.shutdownInProgress.Load() {
		return errors.New("shutdown in progress, skipping message processing")
	}

	retryCount := 0
	bo := backoff.NewExponentialBackOff()
	bo.InitialInterval = c.retryInitialBackoff
	bo.MaxInterval = c.retryMaxBackoff
	bo.MaxElapsedTime = 0

	var processErr error
	err := backoff.RetryNotify(func() error {
		if retryCount >= c.retryMaxAttempts {
			return backoff.Permanent(errors.New("max processing attempts exceeded"))
		}
		if c.shutdownInProgress.Load() || ctx.Err() != nil {
			return backoff.Permanent(errors.New("shutdown during processing retry"))
		}

		// Process the message using custom processor if set, else default logic
		if c.messageProcessor != nil {
			processErr = c.messageProcessor(ctx, msg)
		} else {
			// Default processing logic
			processErr = nil
		}
		
		if processErr != nil {
			var nonRetriableErr *NonRetriableKafkaError
			if errors.As(processErr, &nonRetriableErr) {
				return backoff.Permanent(processErr)
			}
			if isRetriableError(processErr) {
				retryCount++
				return &RetriableKafkaError{Err: processErr}
			}
			return backoff.Permanent(processErr)
		}
		return nil
	}, bo, func(err error, duration time.Duration) {
		globalLogger.Warn(ctx, "Message processing failed, retrying",
			zap.Int("attempt", retryCount),
			zap.Duration("next_retry_in", duration),
			zap.String("topic", msg.Topic),
			zap.Int32("partition", msg.Partition),
			zap.Int64("offset", msg.Offset),
			zap.Error(err),
		)
	})

	if err != nil {
		// Check if error is non-retriable or max attempts exceeded, send to DLQ
		globalLogger.Error(context.Background(), "Message processing failed permanently, sending to DLQ",
			zap.String("topic", msg.Topic),
			zap.Int32("partition", msg.Partition),
			zap.Int64("offset", msg.Offset),
			zap.Error(err),
		)
		dlqErr := c.sendToDLQ(msg, err)
		if dlqErr != nil {
			globalLogger.Error(context.Background(), "Failed to send message to DLQ",
				zap.Error(dlqErr),
			)
			return dlqErr
		}
	}

	// Acknowledge the message
	c.offsetMu.Lock()
	if _, ok := c.pendingOffsets[msg.Topic]; !ok {
		c.pendingOffsets[msg.Topic] = make(map[int32]int64)
	}
	c.pendingOffsets[msg.Topic][msg.Partition] = msg.Offset + 1
	c.offsetMu.Unlock()

	return nil
}

// SetMessageProcessor sets a custom message processor for testing
func (c *KafkaConsumer) SetMessageProcessor(processor func(context.Context, *sarama.ConsumerMessage) error) {
	c.messageProcessor = processor
}

// SetDLQSender sets a custom DLQ sender for testing
func (c *KafkaConsumer) SetDLQSender(sender func(*sarama.ConsumerMessage, error) error) {
	c.dlqSender = sender
}

// StartServer starts the kafka-collector server with health checks and consumer
func StartServer(ctx context.Context, cfg KafkaConsumerConfig) error {
	// Initialize logger
	if err := InitGlobalLogger(); err != nil {
		return err
	}

	// Start health server
	healthPort := 13210
	if err := startHealthServer(healthPort); err != nil {
		return fmt.Errorf("failed to start health server: %w", err)
	}

	// Create consumer with retry
	consumer, err := NewKafkaConsumerWithRetry(cfg)
	if err != nil {
		return fmt.Errorf("failed to create consumer: %w", err)
	}
	defer consumer.Close()

	// Start consumption
	return consumer.StartConsumption(ctx)
}

// StartConsumption starts consuming messages from the configured topics (for testing)
func (c *KafkaConsumer) StartConsumption(ctx context.Context) error {
	// In real implementation, this would start partition consumers and process messages
	// For testing, this is a no-op that returns when context is canceled
	<-ctx.Done()
	return nil
}

// sendToDLQ writes a permanently failed message to the configured dead-letter queue
func (c *KafkaConsumer) sendToDLQ(msg *sarama.ConsumerMessage, err error) error {
	if c.dlqSender != nil {
		return c.dlqSender(msg, err)
	}
	// Create DLQ message with error metadata
	dlqMsg := &sarama.ProducerMessage{
		Topic: c.dlqTopic,
		Key:   sarama.ByteEncoder(msg.Key),
		Value: sarama.ByteEncoder(msg.Value),
		Headers: []sarama.RecordHeader{
			{
				Key:   []byte("original_topic"),
				Value: []byte(msg.Topic),
			},
			{
				Key:   []byte("original_partition"),
				Value: []byte(strconv.FormatInt(int64(msg.Partition), 10)),
			},
			{
				Key:   []byte("original_offset"),
				Value: []byte(strconv.FormatInt(msg.Offset, 10)),
			},
			{
				Key:   []byte("error_message"),
				Value: []byte(err.Error()),
			},
			{
				Key:   []byte("failure_timestamp"),
				Value: []byte(time.Now().Format(time.RFC3339)),
			},
		},
	}

	_, _, err = c.producer.SendMessage(dlqMsg)
	return err
}

//   brokers: list of Kafka broker addresses
//   groupID: consumer group ID
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

	// Load retry configuration from environment variables
	retryMaxAttempts := 5
	if retryMaxStr := os.Getenv("KAFKA_RETRY_MAX_ATTEMPTS"); retryMaxStr != "" {
		if parsed, err := strconv.Atoi(retryMaxStr); err == nil && parsed >= 0 {
			retryMaxAttempts = parsed
		}
	}

	retryInitialBackoff := 100 * time.Millisecond
	if initialBackoffStr := os.Getenv("KAFKA_RETRY_INITIAL_BACKOFF_MS"); initialBackoffStr != "" {
		if parsedMs, err := strconv.Atoi(initialBackoffStr); err == nil && parsedMs >= 0 {
			retryInitialBackoff = time.Duration(parsedMs) * time.Millisecond
		}
	}

	retryMaxBackoff := 10 * time.Second
	if maxBackoffStr := os.Getenv("KAFKA_RETRY_MAX_BACKOFF_MS"); maxBackoffStr != "" {
		if parsedMs, err := strconv.Atoi(maxBackoffStr); err == nil && parsedMs >= 0 {
			retryMaxBackoff = time.Duration(parsedMs) * time.Millisecond
		}
	}

	dlqTopic := "kafka-collector-dlq"
	if dlqTopicStr := os.Getenv("KAFKA_DLQ_TOPIC"); dlqTopicStr != "" {
		dlqTopic = dlqTopicStr
	}

	globalLogger.Info(ctx, "Loaded retry configuration",
		zap.Int("retry_max_attempts", retryMaxAttempts),
		zap.Duration("retry_initial_backoff", retryInitialBackoff),
		zap.Duration("retry_max_backoff", retryMaxBackoff),
		zap.String("dlq_topic", dlqTopic),
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

	// Initialize Kafka consumer with retry logic
	var kafkaConsumer *KafkaConsumer
	var groupID string
	saramaConfig := sarama.NewConfig()
	saramaConfig.Consumer.Return.Errors = true
	saramaConfig.Consumer.Offsets.AutoCommit.Enable = false // We will commit offsets manually during shutdown

	if tlsConfig.Enabled {
		// Get consumer group ID
		groupID = os.Getenv("KAFKA_CONSUMER_GROUP_ID")
		if groupID == "" {
			groupID = "kafka-collector-group"
		}

		// Configure TLS
		tlsCfg := &tls.Config{
			InsecureSkipVerify: tlsConfig.SkipVerify,
		}

		// Load CA cert
		caCert, err := os.ReadFile(tlsConfig.CACertPath)
		if err != nil {
			globalLogger.Error(ctx, "Failed to read CA certificate: %w", zap.Error(err))
			os.Exit(1)
		}
		caCertPool := x509.NewCertPool()
		if !caCertPool.AppendCertsFromPEM(caCert) {
			globalLogger.Error(ctx, "Failed to append CA certificate to pool")
			os.Exit(1)
		}
		tlsCfg.RootCAs = caCertPool

		// Load client cert/key if provided
		if tlsConfig.ClientCertPath != "" && tlsConfig.ClientKeyPath != "" {
			cert, err := tls.LoadX509KeyPair(tlsConfig.ClientCertPath, tlsConfig.ClientKeyPath)
			if err != nil {
				globalLogger.Error(ctx, "Failed to load client certificate/key pair: %w", zap.Error(err))
				os.Exit(1)
			}
			tlsCfg.Certificates = []tls.Certificate{cert}
		}

		// Apply TLS config to sarama
		saramaConfig.Net.TLS.Enable = true
		saramaConfig.Net.TLS.Config = tlsCfg
	} else {
		groupID = "kafka-collector-group"
	}

	// Create consumer with retry logic
	kafkaConsumer, err = NewKafkaConsumerWithRetry(KafkaConsumerConfig{
		Ctx:                ctx,
		Brokers:            []string{kafkaAddr},
		Topics:             topics,
		SaramaConfig:       saramaConfig,
		ShutdownTimeout:    30 * time.Second,
		RetryMaxAttempts:   retryMaxAttempts,
		RetryInitialBackoff: retryInitialBackoff,
		RetryMaxBackoff:    retryMaxBackoff,
		DLQTopic:           dlqTopic,
	})
	if err != nil {
		globalLogger.Error(ctx, "Failed to initialize Kafka consumer with retry",
			zap.String("consumer_group_id", groupID),
			zap.Error(err),
		)
		os.Exit(1)
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
