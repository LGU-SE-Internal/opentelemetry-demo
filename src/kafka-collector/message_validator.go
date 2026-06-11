// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"errors"
	"fmt"
	"github.com/confluentinc/confluent-kafka-go/v2/kafka"
	logsv1 "go.opentelemetry.io/proto/otlp/logs/v1"
	metricv1 "go.opentelemetry.io/proto/otlp/metrics/v1"
	tracev1 "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
	"go.uber.org/zap"
	"context"
)

// Define error types
var (
	ErrInvalidConfig = errors.New("invalid validation configuration")
	ErrDLQSendFailed = errors.New("failed to send message to dead letter topic")
)

// KafkaMessageValidationConfig holds validation settings for consumed Kafka messages
type KafkaMessageValidationConfig struct {
	MaxMessageSizeBytes    int     `yaml:"max_message_size_bytes" default:"1048576"` // 1MB default
	EnableSchemaValidation bool    `yaml:"enable_schema_validation" default:"true"`
	DeadLetterTopicName    *string `yaml:"dead_letter_topic"` // optional, nil means DLQ disabled
	LogInvalidMessages     bool    `yaml:"log_invalid_messages" default:"true"`
}

// ValidationResult represents the outcome of message validation
type ValidationResult struct {
	Valid           bool
	FailureReason   string
	MessageMetadata map[string]interface{} // includes topic, partition, offset, timestamp
}

// MessageValidator performs validation on consumed Kafka messages
type MessageValidator struct {
	config     KafkaMessageValidationConfig
	dlqProducer *kafka.Producer
	logger     Logger
}

// NewMessageValidator creates a new validator instance with the provided config
func NewMessageValidator(config KafkaMessageValidationConfig, logger Logger) (*MessageValidator, error) {
	if config.MaxMessageSizeBytes <= 0 {
		return nil, fmt.Errorf("%w: max message size must be positive", ErrInvalidConfig)
	}

	v := &MessageValidator{
		config: config,
		logger: logger,
	}

	// Initialize DLQ producer if DLQ is configured
	if config.DeadLetterTopicName != nil {
		// Use default producer config for now - in real implementation this would reuse existing Kafka client config
		p, err := kafka.NewProducer(&kafka.ConfigMap{
			"bootstrap.servers": "localhost:9092", // This would be replaced with actual config from environment
		})
		if err != nil {
			return nil, fmt.Errorf("%w: failed to initialize DLQ producer: %v", ErrInvalidConfig, err)
		}
		v.dlqProducer = p
	}

	return v, nil
}

// Validate performs all validation checks on a consumed Kafka message
// Returns ValidationResult with pass/fail status and failure reason if invalid
// Returns error only for critical internal failures, not for invalid messages
func (v *MessageValidator) Validate(msg *kafka.Message) (ValidationResult, error) {
	metadata := map[string]interface{}{
		"topic":     *msg.TopicPartition.Topic,
		"partition": msg.TopicPartition.Partition,
		"offset":    msg.TopicPartition.Offset,
		"timestamp": msg.Timestamp.Unix(),
	}

	result := ValidationResult{
		MessageMetadata: metadata,
	}

	// Check message size first
	if len(msg.Value) > v.config.MaxMessageSizeBytes {
		result.Valid = false
		result.FailureReason = "message size exceeds maximum allowed limit"
		return result, nil
	}

	// If schema validation is disabled, we're done here
	if !v.config.EnableSchemaValidation {
		result.Valid = true
		return result, nil
	}

	// Try to unmarshal as each of the OTel schema types
	var trace tracev1.TracesData
	if err := proto.Unmarshal(msg.Value, &trace); err == nil {
		if len(trace.ResourceSpans) > 0 {
			result.Valid = true
			return result, nil
		}
	}

	var metric metricv1.MetricsData
	if err := proto.Unmarshal(msg.Value, &metric); err == nil {
		if len(metric.ResourceMetrics) > 0 {
			result.Valid = true
			return result, nil
		}
	}

	var log logsv1.LogsData
	if err := proto.Unmarshal(msg.Value, &log); err == nil {
		if len(log.ResourceLogs) > 0 {
			result.Valid = true
			return result, nil
		}
	}

	// If none of the schemas matched
	result.Valid = false
	result.FailureReason = "schema validation failed: message does not match any OTel trace/metric/log Protobuf schema"
	return result, nil
}

// HandleInvalidMessage processes invalid messages per config: logs, sends to DLQ if configured
// Returns error only if DLQ send fails when DLQ is enabled
func (v *MessageValidator) HandleInvalidMessage(msg *kafka.Message, reason string) error {
	// Log if enabled
	if v.config.LogInvalidMessages && v.logger != nil {
		v.logger.Warn(context.Background(), "Invalid Kafka message received",
			zap.String("topic", *msg.TopicPartition.Topic),
			zap.Int32("partition", msg.TopicPartition.Partition),
			zap.Int64("offset", int64(msg.TopicPartition.Offset)),
			zap.Time("timestamp", msg.Timestamp),
			zap.String("failure_reason", reason),
		)
	}

	// Send to DLQ if configured
	if v.config.DeadLetterTopicName != nil && v.dlqProducer != nil {
		// Add failure reason header
		headers := make([]kafka.Header, len(msg.Headers)+1)
		copy(headers, msg.Headers)
		headers[len(msg.Headers)] = kafka.Header{
			Key:   "x-failure-reason",
			Value: []byte(reason),
		}

		// Produce to DLQ async
		deliveryChan := make(chan kafka.Event, 1)
		err := v.dlqProducer.Produce(&kafka.Message{
			TopicPartition: kafka.TopicPartition{
				Topic:     v.config.DeadLetterTopicName,
				Partition: kafka.PartitionAny,
			},
			Value:   msg.Value,
			Headers: headers,
		}, deliveryChan)

		if err != nil {
			return fmt.Errorf("%w: %v", ErrDLQSendFailed, err)
		}

		// Wait for delivery report (optional, but for test purposes we can wait briefly)
		e := <-deliveryChan
		m := e.(*kafka.Message)
		if m.TopicPartition.Error != nil {
			return fmt.Errorf("%w: %v", ErrDLQSendFailed, m.TopicPartition.Error)
		}
		close(deliveryChan)
	}

	return nil
}

// Close cleans up resources used by the validator
func (v *MessageValidator) Close() {
	if v.dlqProducer != nil {
		v.dlqProducer.Flush(1000)
		v.dlqProducer.Close()
	}
}
