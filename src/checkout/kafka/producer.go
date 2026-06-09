// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package kafka

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/IBM/sarama"
	"github.com/prometheus/client_golang/prometheus"
)

var (
	Topic           = "orders"
	ProtocolVersion = sarama.V3_0_0_0

	ErrInvalidConfig  = errors.New("invalid kafka producer config")
	ErrDeliveryFailed = errors.New("failed to deliver message to both main topic and DLQ")
)

// KafkaProducerConfig extends existing producer config with retry and DLQ settings
type KafkaProducerConfig struct {
	RequiredAcks   int           `env:"KAFKA_REQUIRED_ACKS" envDefault:"1"` // 1 = wait for leader ack, -1 = all in-sync replicas
	MaxRetries     int           `env:"KAFKA_PRODUCER_MAX_RETRIES" envDefault:"3"`
	InitialBackoff time.Duration `env:"KAFKA_PRODUCER_INITIAL_BACKOFF" envDefault:"100ms"`
	MaxBackoff     time.Duration `env:"KAFKA_PRODUCER_MAX_BACKOFF" envDefault:"2s"`
	DLQTopic       string        `env:"KAFKA_ORDER_DLQ_TOPIC" envDefault:"order-events-dlq"`
	Brokers        []string      `env:"KAFKA_BROKERS" envDefault:"kafka:9092"`
}

// OrderKafkaMetrics exposes prometheus metrics for producer operations
type OrderKafkaMetrics struct {
	SuccessfulDeliveries prometheus.Counter
	FailedDeliveries     prometheus.Counter
	RetriedAttempts      prometheus.Counter
	DLQDeliveries        prometheus.Counter
}

// OrderEventProducer interface for producing order events
type OrderEventProducer interface {
	ProduceOrderEvent(ctx context.Context, event OrderEvent) error
	GetSaramaConfig() *sarama.Config
	Close() error
}

// OrderEvent represents an order event to be sent to Kafka
type OrderEvent struct {
	OrderId string `json:"order_id"`
	// Additional fields as per existing schema
}

type orderKafkaProducer struct {
	cfg          KafkaProducerConfig
	metrics      OrderKafkaMetrics
	saramaConfig *sarama.Config
	producer     sarama.SyncProducer
	logger       *slog.Logger
}

type saramaLogger struct {
	logger *slog.Logger
}

func (l *saramaLogger) Printf(format string, v ...interface{}) {
	l.logger.Info(fmt.Sprintf(format, v...))
}
func (l *saramaLogger) Println(v ...interface{}) {
	l.logger.Info(fmt.Sprint(v...))
}
func (l *saramaLogger) Print(v ...interface{}) {
	l.logger.Info(fmt.Sprint(v...))
}

// NewOrderKafkaProducer creates a configured Kafka producer with retry, DLQ and metrics
// Returns error if config is invalid or broker connection fails
func NewOrderKafkaProducer(cfg KafkaProducerConfig, metrics OrderKafkaMetrics) (OrderEventProducer, error) {
	return NewOrderKafkaProducerWithBrokers(cfg, metrics, cfg.Brokers)
}

// NewOrderKafkaProducerWithBrokers creates a producer with custom brokers (for testing)
func NewOrderKafkaProducerWithBrokers(cfg KafkaProducerConfig, metrics OrderKafkaMetrics, brokers []string) (OrderEventProducer, error) {
	// Validate config
	if cfg.MaxRetries < 0 {
		return nil, fmt.Errorf("%w: max retries cannot be negative", ErrInvalidConfig)
	}
	if cfg.DLQTopic == "" {
		return nil, fmt.Errorf("%w: DLQ topic cannot be empty", ErrInvalidConfig)
	}
	if cfg.RequiredAcks < -1 || cfg.RequiredAcks > 1 {
		return nil, fmt.Errorf("%w: required acks must be -1, 0, or 1", ErrInvalidConfig)
	}
	if cfg.InitialBackoff <= 0 {
		return nil, fmt.Errorf("%w: initial backoff must be positive", ErrInvalidConfig)
	}
	if cfg.MaxBackoff < cfg.InitialBackoff {
		return nil, fmt.Errorf("%w: max backoff cannot be less than initial backoff", ErrInvalidConfig)
	}

	// Set Sarama logger
	logger := slog.Default()
	sarama.Logger = &saramaLogger{logger: logger}

	saramaConfig := sarama.NewConfig()
	saramaConfig.Producer.Return.Successes = true
	saramaConfig.Producer.Return.Errors = true
	saramaConfig.Producer.RequiredAcks = sarama.RequiredAcks(cfg.RequiredAcks)
	saramaConfig.Version = ProtocolVersion
	// Disable sarama built-in retries since we implement custom retry logic
	saramaConfig.Producer.Retry.Max = 0

	producer, err := sarama.NewSyncProducer(brokers, saramaConfig)
	if err != nil {
		return nil, err
	}

	return &orderKafkaProducer{
		cfg:          cfg,
		metrics:      metrics,
		saramaConfig: saramaConfig,
		producer:     producer,
		logger:       logger,
	}, nil
}

// ProduceOrderEvent sends an order event to Kafka with retry logic, falls back to DLQ after max retries
// Returns nil only if message is successfully delivered to main topic or DLQ
// Returns non-nil error only if both main topic delivery and DLQ delivery fail
func (p *orderKafkaProducer) ProduceOrderEvent(ctx context.Context, event OrderEvent) error {
	// Create message for main topic
	msg := &sarama.ProducerMessage{
		Topic: Topic,
		Value: sarama.StringEncoder(event.OrderId), // In real implementation, use proper JSON encoding
	}

	// Attempt delivery to main topic with retries
	backoff := p.cfg.InitialBackoff
	var err error
	for attempt := 0; attempt <= p.cfg.MaxRetries; attempt++ {
		partition, offset, err := p.producer.SendMessage(msg)
		if err == nil {
			if attempt == 0 {
				p.metrics.SuccessfulDeliveries.Inc()
			} else {
				p.metrics.SuccessfulDeliveries.Inc()
				p.metrics.RetriedAttempts.Add(float64(attempt))
			}
			p.logger.Debug("Message delivered successfully",
				"topic", Topic,
				"partition", partition,
				"offset", offset,
				"attempt", attempt+1,
			)
			return nil
		}

		// Check if error is retriable
		if !isRetriableError(err) {
			p.logger.Error("Non-retriable error delivering message", "error", err)
			break
		}

		// If this was the last attempt, break to go to DLQ
		if attempt == p.cfg.MaxRetries {
			break
		}

		// Wait for backoff or context cancellation
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}

		// Exponential backoff, cap at MaxBackoff
		backoff *= 2
		if backoff > p.cfg.MaxBackoff {
			backoff = p.cfg.MaxBackoff
		}
	}

	// If we got here, main topic delivery failed, try DLQ
	dlqMsg := &sarama.ProducerMessage{
		Topic: p.cfg.DLQTopic,
		Value: msg.Value,
		Headers: []sarama.RecordHeader{
			{
				Key:   []byte("original_topic"),
				Value: []byte(Topic),
			},
			{
				Key:   []byte("retry_count"),
				Value: []byte(fmt.Sprintf("%d", p.cfg.MaxRetries)),
			},
			{
				Key:   []byte("first_failure_timestamp"),
				Value: []byte(time.Now().Format(time.RFC3339)),
			},
			{
				Key:   []byte("error_message"),
				Value: []byte(err.Error()),
			},
		},
	}

	_, _, dlqErr := p.producer.SendMessage(dlqMsg)
	if dlqErr == nil {
		p.metrics.DLQDeliveries.Inc()
		p.logger.Info("Message delivered to DLQ after failed retries",
			"dlq_topic", p.cfg.DLQTopic,
			"original_error", err,
		)
		return nil
	}

	// Both main and DLQ delivery failed
	p.metrics.FailedDeliveries.Inc()
	p.logger.Error("Failed to deliver message to both main topic and DLQ",
		"main_error", err,
		"dlq_error", dlqErr,
	)
	return fmt.Errorf("%w: %v (DLQ error: %v)", ErrDeliveryFailed, err, dlqErr)
}

// GetSaramaConfig returns the underlying sarama config for testing
func (p *orderKafkaProducer) GetSaramaConfig() *sarama.Config {
	return p.saramaConfig
}

// Close shuts down the producer
func (p *orderKafkaProducer) Close() error {
	return p.producer.Close()
}

// isRetriableError checks if a Kafka producer error is retriable
func isRetriableError(err error) bool {
	var producerErr *sarama.ProducerError
	if errors.As(err, &producerErr) {
		return producerErr.Err.Temporary()
	}
	// Also check for other temporary errors
	var tempErr interface{ Temporary() bool }
	if errors.As(err, &tempErr) {
		return tempErr.Temporary()
	}
	return false
}

