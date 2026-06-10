// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"errors"
	"os"
	"strconv"
	"time"

	"github.com/IBM/sarama"
	"github.com/cenkalti/backoff/v4"
	"github.com/sony/gobreaker"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	ErrCircuitBreakerOpen = errors.New("kafka producer circuit breaker is open")
	ErrRetriesExhausted = errors.New("kafka send retries exhausted")
)

// ResilientKafkaProducer wraps a kafka.Producer with retry and circuit breaker logic
type ResilientKafkaProducer interface {
	// Send sends a message to Kafka with resilience patterns applied
	Send(ctx context.Context, msg *sarama.ProducerMessage) error
}

type resilientKafkaProducer struct {
	producer sarama.SyncProducer
	backoffConfig backoffConfig
	circuitBreaker *gobreaker.CircuitBreaker
}

type backoffConfig struct {
	maxAttempts int
	initialBackoff time.Duration
	maxBackoff time.Duration
	jitterFactor float64
}

// Metrics
var (
	sendAttemptsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "checkout_kafka_producer_send_attempts_total",
		Help: "Total number of Kafka send attempts",
	}, []string{"status"})
	circuitBreakerEventsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "checkout_kafka_producer_circuit_breaker_events_total",
		Help: "Total number of circuit breaker state changes and rejected requests",
	}, []string{"event"})
)

// NewResilientKafkaProducer creates a new ResilientKafkaProducer with configuration loaded from environment variables
func NewResilientKafkaProducer(underlyingProducer sarama.SyncProducer) (ResilientKafkaProducer, error) {
	// Load configuration from environment variables
	cfg := loadConfig()

	// Setup circuit breaker
	cb := gobreaker.NewCircuitBreaker(gobreaker.Settings{
		Name: "kafka-producer",
		MaxRequests: 1, // Allow 1 test request in half-open state
		Interval: 0,
		Timeout: time.Duration(cfg.circuitBreakerCooldownMs) * time.Millisecond,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			return counts.ConsecutiveFailures >= uint32(cfg.circuitBreakerFailureThreshold)
		},
		OnStateChange: func(name string, from gobreaker.State, to gobreaker.State) {
			var event string
			switch to {
			case gobreaker.StateOpen:
				event = "open"
			case gobreaker.StateClosed:
				event = "close"
			case gobreaker.StateHalfOpen:
				event = "half_open"
			}
			circuitBreakerEventsTotal.WithLabelValues(event).Inc()
		},
	})

	return &resilientKafkaProducer{
		producer: underlyingProducer,
		backoffConfig: backoffConfig{
			maxAttempts: cfg.retryMaxAttempts,
			initialBackoff: time.Duration(cfg.retryInitialBackoffMs) * time.Millisecond,
			maxBackoff: time.Duration(cfg.retryMaxBackoffMs) * time.Millisecond,
			jitterFactor: cfg.retryJitterFactor,
		},
		circuitBreaker: cb,
	}, nil
}

type config struct {
	retryMaxAttempts int
	retryInitialBackoffMs int
	retryMaxBackoffMs int
	retryJitterFactor float64
	circuitBreakerFailureThreshold int
	circuitBreakerCooldownMs int
}

func loadConfig() config {
	cfg := config{
		retryMaxAttempts: getEnvInt("CHECKOUT_KAFKA_RETRY_MAX_ATTEMPTS", 3),
		retryInitialBackoffMs: getEnvInt("CHECKOUT_KAFKA_RETRY_INITIAL_BACKOFF_MS", 100),
		retryMaxBackoffMs: getEnvInt("CHECKOUT_KAFKA_RETRY_MAX_BACKOFF_MS", 5000),
		retryJitterFactor: getEnvFloat("CHECKOUT_KAFKA_RETRY_JITTER_FACTOR", 0.2),
		circuitBreakerFailureThreshold: getEnvInt("CHECKOUT_KAFKA_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 10),
		circuitBreakerCooldownMs: getEnvInt("CHECKOUT_KAFKA_CIRCUIT_BREAKER_COOLDOWN_PERIOD_MS", 30000),
	}
	return cfg
}

func getEnvInt(key string, defaultVal int) int {
	valStr := os.Getenv(key)
	if valStr == "" {
		return defaultVal
	}
	val, err := strconv.Atoi(valStr)
	if err != nil {
		return defaultVal
	}
	return val
}

func getEnvFloat(key string, defaultVal float64) float64 {
	valStr := os.Getenv(key)
	if valStr == "" {
		return defaultVal
	}
	val, err := strconv.ParseFloat(valStr, 64)
	if err != nil {
		return defaultVal
	}
	return val
}

// Send sends a message to Kafka with resilience patterns applied
func (p *resilientKafkaProducer) Send(ctx context.Context, msg *sarama.ProducerMessage) error {
	// Check if circuit breaker is open
	if p.circuitBreaker.State() == gobreaker.StateOpen {
		circuitBreakerEventsTotal.WithLabelValues("rejected").Inc()
		sendAttemptsTotal.WithLabelValues("failed").Inc()
		return ErrCircuitBreakerOpen
	}

	// Execute send through circuit breaker
	_, err := p.circuitBreaker.Execute(func() (interface{}, error) {
		// Setup exponential backoff with jitter
		bo := backoff.NewExponentialBackOff()
		bo.InitialInterval = p.backoffConfig.initialBackoff
		bo.MaxInterval = p.backoffConfig.maxBackoff
		bo.MaxElapsedTime = 0 // We control max attempts manually
		bo.RandomizationFactor = p.backoffConfig.jitterFactor

		var lastErr error
		for attempt := 0; attempt <= p.backoffConfig.maxAttempts; attempt++ {
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			default:
			}

			_, _, err := p.producer.SendMessage(msg)
			if err == nil {
				if attempt > 0 {
					sendAttemptsTotal.WithLabelValues("retried").Add(float64(attempt))
				}
				sendAttemptsTotal.WithLabelValues("success").Inc()
				return nil, nil
			}

			lastErr = err

			// Check if error is retriable
			if !isRetriableError(err) {
				sendAttemptsTotal.WithLabelValues("failed").Inc()
				return nil, err
			}

			// If this was the last attempt, break
			if attempt == p.backoffConfig.maxAttempts {
				break
			}

			// Wait for backoff
			wait := bo.NextBackOff()
			if wait == backoff.Stop {
				break
			}

			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(wait):
			}
		}

		// All retries exhausted
		sendAttemptsTotal.WithLabelValues("failed").Inc()
		return nil, ErrRetriesExhausted
	})

	return err
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
	// Check for common Sarama temporary errors
	saramaErr, ok := err.(sarama.KError)
	if ok {
		return saramaErr.Temporary()
	}
	return false
}
