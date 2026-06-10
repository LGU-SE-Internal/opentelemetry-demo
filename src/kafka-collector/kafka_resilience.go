package main

import (
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/cenkalti/backoff/v4"
	"github.com/caarlos0/env/v10"
	"github.com/prometheus/client_golang/prometheus"
	"go.uber.org/zap"
)

// KafkaResilienceConfig holds all resilience configuration values loaded from environment variables
type KafkaResilienceConfig struct {
	// Retry configuration
	MaxRetryAttempts    int           `env:"KAFKA_MAX_RETRY_ATTEMPTS" envDefault:"3"`
	InitialRetryBackoff time.Duration `env:"KAFKA_INITIAL_RETRY_BACKOFF" envDefault:"100ms"`
	MaxRetryBackoff     time.Duration `env:"KAFKA_MAX_RETRY_BACKOFF" envDefault:"2s"`

	// Circuit Breaker configuration
	CBFailureThreshold int           `env:"KAFKA_CB_FAILURE_THRESHOLD" envDefault:"5"`
	CBCoolDownPeriod   time.Duration `env:"KAFKA_CB_COOLDOWN_PERIOD" envDefault:"30s"`
}

// Error types
var (
	ErrKafkaOperationFailed = errors.New("kafka operation failed after all retries")
	ErrCircuitBreakerOpen   = errors.New("circuit breaker is open: kafka operations temporarily suspended")
)

// ResilienceMetrics holds exposed Prometheus metrics
type ResilienceMetrics struct {
	RetryAttemptsTotal       prometheus.Counter  `help:"Total number of Kafka operation retry attempts"`
	CircuitBreakerState      prometheus.Gauge    `help:"Circuit breaker state: 0 = closed, 1 = open, 2 = half-open"`
	OperationFailuresTotal   prometheus.Counter  `help:"Total number of failed Kafka operations after all retries"`
}

// NewResilienceMetrics creates a new ResilienceMetrics instance with registered Prometheus metrics
func NewResilienceMetrics() *ResilienceMetrics {
	m := &ResilienceMetrics{
		RetryAttemptsTotal: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "kafka_retry_attempts_total",
			Help: "Total number of Kafka operation retry attempts",
		}),
		CircuitBreakerState: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "kafka_circuit_breaker_state",
			Help: "Circuit breaker state: 0 = closed, 1 = open, 2 = half-open",
		}),
		OperationFailuresTotal: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "kafka_operation_failures_total",
			Help: "Total number of failed Kafka operations after all retries",
		}),
	}

	prometheus.MustRegister(m.RetryAttemptsTotal)
	prometheus.MustRegister(m.CircuitBreakerState)
	prometheus.MustRegister(m.OperationFailuresTotal)

	// Initial state is closed
	m.CircuitBreakerState.Set(float64(cbStateClosed))
	return m
}

type CircuitBreakerState int

const (
	cbStateClosed   CircuitBreakerState = 0
	cbStateOpen     CircuitBreakerState = 1
	cbStateHalfOpen CircuitBreakerState = 2
)

func (s CircuitBreakerState) String() string {
	switch s {
	case cbStateClosed:
		return "closed"
	case cbStateOpen:
		return "open"
	case cbStateHalfOpen:
		return "half-open"
	default:
		return "unknown"
	}
}

// KafkaResilienceWrapper wraps Kafka operations with retry and circuit breaker functionality
type KafkaResilienceWrapper struct {
	cfg                KafkaResilienceConfig
	metrics            *ResilienceMetrics
	logger             *zap.Logger
	mu                 sync.Mutex
	state              CircuitBreakerState
	failureCount       int
	openTime           time.Time
}

// NewKafkaResilienceWrapper creates a new wrapper with configured retry and circuit breaker
func NewKafkaResilienceWrapper(cfg KafkaResilienceConfig, metrics *ResilienceMetrics, logger *zap.Logger) *KafkaResilienceWrapper {
	return &KafkaResilienceWrapper{
		cfg:     cfg,
		metrics: metrics,
		logger:  logger,
		state:   cbStateClosed,
	}
}

// LoadKafkaResilienceConfig loads configuration from environment variables
func LoadKafkaResilienceConfig() (KafkaResilienceConfig, error) {
	var cfg KafkaResilienceConfig
	if err := env.Parse(&cfg); err != nil {
		return KafkaResilienceConfig{}, fmt.Errorf("failed to parse kafka resilience config: %w", err)
	}
	return cfg, nil
}

// CommitOffsetWithRetry wraps Kafka consumer offset commit with exponential backoff retry and circuit breaker
func (w *KafkaResilienceWrapper) CommitOffsetWithRetry(commitFn func() error) error {
	return w.executeWithResilience("commit", commitFn)
}

// SendMessageWithRetry wraps Kafka producer message send with exponential backoff retry and circuit breaker
func (w *KafkaResilienceWrapper) SendMessageWithRetry(sendFn func() error) error {
	return w.executeWithResilience("send", sendFn)
}

func (w *KafkaResilienceWrapper) transitionState(newState CircuitBreakerState) {
	oldState := w.state
	if oldState == newState {
		return
	}
	w.state = newState
	w.metrics.CircuitBreakerState.Set(float64(newState))
	// Log transition
	if newState == cbStateOpen {
		w.logger.Error("circuit breaker state transition",
			zap.String("old_state", oldState.String()),
			zap.String("new_state", newState.String()),
			zap.Int("failure_count", w.failureCount),
			zap.Duration("cooldown_period", w.cfg.CBCoolDownPeriod),
		)
	} else {
		w.logger.Error("circuit breaker state transition",
			zap.String("old_state", oldState.String()),
			zap.String("new_state", newState.String()),
		)
	}
}

func (w *KafkaResilienceWrapper) allowRequest() bool {
	w.mu.Lock()
	defer w.mu.Unlock()

	switch w.state {
	case cbStateClosed:
		return true
	case cbStateOpen:
		if time.Since(w.openTime) >= w.cfg.CBCoolDownPeriod {
			w.transitionState(cbStateHalfOpen)
			return true
		}
		return false
	case cbStateHalfOpen:
		// Allow only one test request
		return true
	default:
		return false
	}
}

func (w *KafkaResilienceWrapper) recordSuccess() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.failureCount = 0
	if w.state == cbStateHalfOpen || w.state == cbStateOpen {
		w.transitionState(cbStateClosed)
	}
}

func (w *KafkaResilienceWrapper) recordFailure() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.failureCount++

	switch w.state {
	case cbStateClosed:
		if w.failureCount >= w.cfg.CBFailureThreshold {
			w.openTime = time.Now()
			w.transitionState(cbStateOpen)
		}
	case cbStateHalfOpen:
		// Test request failed, go back to open
		w.openTime = time.Now()
		w.transitionState(cbStateOpen)
	}
}

func (w *KafkaResilienceWrapper) executeWithResilience(operationType string, opFn func() error) error {
	// Check if operation is allowed
	if !w.allowRequest() {
		return ErrCircuitBreakerOpen
	}

	// Create exponential backoff
	bo := backoff.NewExponentialBackOff()
	bo.InitialInterval = w.cfg.InitialRetryBackoff
	bo.MaxInterval = w.cfg.MaxRetryBackoff
	bo.MaxElapsedTime = 0 // We'll handle max attempts ourselves
	bo.Reset()

	var err error
	attempt := 0
	for attempt <= w.cfg.MaxRetryAttempts {
		err = opFn()
		if err == nil {
			// Success
			w.recordSuccess()
			return nil
		}

		if attempt == w.cfg.MaxRetryAttempts {
			// No more retries
			break
		}

		// Increment retry counter
		w.metrics.RetryAttemptsTotal.Inc()

		// Log retry attempt
		w.logger.Warn("kafka operation retry attempt",
			zap.String("operation_type", operationType),
			zap.Int("attempt_number", attempt+1),
			zap.Int("max_attempts", w.cfg.MaxRetryAttempts),
			zap.String("error_message", err.Error()),
		)

		// Wait for backoff
		nextBackoff := bo.NextBackOff()
		if nextBackoff == backoff.Stop {
			break
		}
		time.Sleep(nextBackoff)

		attempt++
	}

	// All retries failed
	w.metrics.OperationFailuresTotal.Inc()
	w.recordFailure()
	return ErrKafkaOperationFailed
}
