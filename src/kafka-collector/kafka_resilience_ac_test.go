package main

import (
	"errors"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
	"go.uber.org/zap"
	"go.uber.org/zap/zaptest/observer"
)

// AC-1 Test: Max retries executed, returns ErrKafkaOperationFailed after all attempts
func TestAC1_MaxRetryAttemptsReturnError(t *testing.T) {
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    3,
		InitialRetryBackoff: 10 * time.Millisecond,
		MaxRetryBackoff:     100 * time.Millisecond,
		CBFailureThreshold:  10,
		CBCoolDownPeriod:    10 * time.Second,
	}
	metrics := NewResilienceMetrics()
	logger, _ := zap.NewProduction()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	attemptCount := 0
	failFn := func() error {
		attemptCount++
		return errors.New("kafka failure")
	}

	err := wrapper.CommitOffsetWithRetry(failFn)
	assert.ErrorIs(t, err, ErrKafkaOperationFailed)
	assert.Equal(t, 4, attemptCount) // 1 initial + 3 retries
}

// AC-2 Test: Exponential backoff follows doubling pattern capped at max
func TestAC2_ExponentialBackoffPattern(t *testing.T) {
	initialBackoff := 100 * time.Millisecond
	maxBackoff := 2 * time.Second
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    4,
		InitialRetryBackoff: initialBackoff,
		MaxRetryBackoff:     maxBackoff,
		CBFailureThreshold:  10,
		CBCoolDownPeriod:    10 * time.Second,
	}
	metrics := NewResilienceMetrics()
	logger, _ := zap.NewProduction()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	lastAttemptTime := time.Now()
	expectedDelays := []time.Duration{0, initialBackoff, 200 * time.Millisecond, 400 * time.Millisecond, 800 * time.Millisecond}
	actualDelays := make([]time.Duration, 0, 5)

	failFn := func() error {
		now := time.Now()
		actualDelays = append(actualDelays, now.Sub(lastAttemptTime).Round(10*time.Millisecond))
		lastAttemptTime = now
		return errors.New("kafka failure")
	}

	_ = wrapper.SendMessageWithRetry(failFn)

	// Check delays are approximately doubling, within tolerance
	for i := 1; i < len(expectedDelays); i++ {
		assert.GreaterOrEqual(t, actualDelays[i], expectedDelays[i]*9/10, "Delay at attempt %d too short", i)
		assert.LessOrEqual(t, actualDelays[i], expectedDelays[i]*11/10, "Delay at attempt %d too long", i)
	}

	// Check next retry would be capped at maxBackoff
	cfg.MaxRetryAttempts = 5
	wrapper2 := NewKafkaResilienceWrapper(cfg, metrics, logger)
	lastAttemptTime = time.Now()
	actualDelays2 := make([]time.Duration, 0, 6)
	failFn2 := func() error {
		now := time.Now()
		actualDelays2 = append(actualDelays2, now.Sub(lastAttemptTime).Round(10*time.Millisecond))
		lastAttemptTime = now
		return errors.New("kafka failure")
	}
	_ = wrapper2.SendMessageWithRetry(failFn2)
	assert.GreaterOrEqual(t, actualDelays2[5], maxBackoff*9/10)
	assert.LessOrEqual(t, actualDelays2[5], maxBackoff*11/10)
}

// AC-3 Test: Circuit breaker opens after threshold failures, rejects operations
func TestAC3_CircuitBreakerOpensAfterThreshold(t *testing.T) {
	failureThreshold := 5
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    0,
		CBFailureThreshold:  failureThreshold,
		CBCoolDownPeriod:    30 * time.Second,
	}
	metrics := NewResilienceMetrics()
	logger, _ := zap.NewProduction()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	failFn := func() error { return errors.New("failure") }

	// First N failures should return operation error
	for i := 0; i < failureThreshold; i++ {
		err := wrapper.CommitOffsetWithRetry(failFn)
		assert.ErrorIs(t, err, ErrKafkaOperationFailed)
	}

	// Next call should return circuit open error immediately
	err := wrapper.CommitOffsetWithRetry(failFn)
	assert.ErrorIs(t, err, ErrCircuitBreakerOpen)
}

// AC-4 Test: Circuit breaker transitions to half-open after cooldown, then closes on success
func TestAC4_CircuitBreakerHalfOpenTransition(t *testing.T) {
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    0,
		CBFailureThreshold:  2,
		CBCoolDownPeriod:    100 * time.Millisecond,
	}
	metrics := NewResilienceMetrics()
	logger, _ := zap.NewProduction()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	failFn := func() error { return errors.New("failure") }
	successFn := func() error { return nil }

	// Trigger circuit open
	_ = wrapper.CommitOffsetWithRetry(failFn)
	_ = wrapper.CommitOffsetWithRetry(failFn)
	err := wrapper.CommitOffsetWithRetry(failFn)
	assert.ErrorIs(t, err, ErrCircuitBreakerOpen)
	assert.Equal(t, float64(1), testutil.ToFloat64(metrics.CircuitBreakerState))

	// Wait for cooldown period
	time.Sleep(150 * time.Millisecond)

	// Test half-open: first operation allowed
	err = wrapper.CommitOffsetWithRetry(successFn)
	assert.NoError(t, err)
	assert.Equal(t, float64(0), testutil.ToFloat64(metrics.CircuitBreakerState)) // Closed after success

	// Test failure in half-open returns to open
	_ = wrapper.CommitOffsetWithRetry(failFn)
	_ = wrapper.CommitOffsetWithRetry(failFn)
	err = wrapper.CommitOffsetWithRetry(failFn)
	assert.ErrorIs(t, err, ErrCircuitBreakerOpen)
	time.Sleep(150 * time.Millisecond)
	err = wrapper.CommitOffsetWithRetry(failFn)
	assert.ErrorIs(t, err, ErrKafkaOperationFailed)
	assert.Equal(t, float64(1), testutil.ToFloat64(metrics.CircuitBreakerState))
}

// AC-5 Test: Configuration values loaded from environment variables with defaults
func TestAC5_EnvVarConfigLoading(t *testing.T) {
	// Test defaults when no env vars set
	t.Setenv("KAFKA_MAX_RETRY_ATTEMPTS", "")
	t.Setenv("KAFKA_INITIAL_RETRY_BACKOFF", "")
	t.Setenv("KAFKA_MAX_RETRY_BACKOFF", "")
	t.Setenv("KAFKA_CB_FAILURE_THRESHOLD", "")
	t.Setenv("KAFKA_CB_COOLDOWN_PERIOD", "")

	cfg, err := LoadKafkaResilienceConfig()
	assert.NoError(t, err)
	assert.Equal(t, 3, cfg.MaxRetryAttempts)
	assert.Equal(t, 100*time.Millisecond, cfg.InitialRetryBackoff)
	assert.Equal(t, 2*time.Second, cfg.MaxRetryBackoff)
	assert.Equal(t, 5, cfg.CBFailureThreshold)
	assert.Equal(t, 30*time.Second, cfg.CBCoolDownPeriod)

	// Test custom env vars applied
	t.Setenv("KAFKA_MAX_RETRY_ATTEMPTS", "5")
	t.Setenv("KAFKA_INITIAL_RETRY_BACKOFF", "200ms")
	t.Setenv("KAFKA_MAX_RETRY_BACKOFF", "5s")
	t.Setenv("KAFKA_CB_FAILURE_THRESHOLD", "10")
	t.Setenv("KAFKA_CB_COOLDOWN_PERIOD", "1m")

	cfg2, err := LoadKafkaResilienceConfig()
	assert.NoError(t, err)
	assert.Equal(t, 5, cfg2.MaxRetryAttempts)
	assert.Equal(t, 200*time.Millisecond, cfg2.InitialRetryBackoff)
	assert.Equal(t, 5*time.Second, cfg2.MaxRetryBackoff)
	assert.Equal(t, 10, cfg2.CBFailureThreshold)
	assert.Equal(t, 1*time.Minute, cfg2.CBCoolDownPeriod)
}

// AC-6 Test: Retry counter increments for every retry attempt
func TestAC6_RetryAttemptsMetricIncrements(t *testing.T) {
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    2,
		InitialRetryBackoff: 10 * time.Millisecond,
		CBFailureThreshold:  10,
	}
	metrics := NewResilienceMetrics()
	logger, _ := zap.NewProduction()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	failFn := func() error { return errors.New("fail") }

	// 2 retries expected
	before := testutil.ToFloat64(metrics.RetryAttemptsTotal)
	_ = wrapper.CommitOffsetWithRetry(failFn)
	after := testutil.ToFloat64(metrics.RetryAttemptsTotal)
	assert.Equal(t, float64(2), after-before)

	// Test send operation also increments
	beforeSend := testutil.ToFloat64(metrics.RetryAttemptsTotal)
	_ = wrapper.SendMessageWithRetry(failFn)
	afterSend := testutil.ToFloat64(metrics.RetryAttemptsTotal)
	assert.Equal(t, float64(2), afterSend-beforeSend)
}

// AC-7 Test: Circuit breaker state metric updates correctly
func TestAC7_CircuitBreakerStateMetricUpdates(t *testing.T) {
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    0,
		CBFailureThreshold:  2,
		CBCoolDownPeriod:    100 * time.Millisecond,
	}
	metrics := NewResilienceMetrics()
	logger, _ := zap.NewProduction()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	failFn := func() error { return errors.New("fail") }
	successFn := func() error { return nil }

	// Initial state: closed (0)
	assert.Equal(t, float64(0), testutil.ToFloat64(metrics.CircuitBreakerState))

	// After threshold failures: open (1)
	_ = wrapper.CommitOffsetWithRetry(failFn)
	_ = wrapper.CommitOffsetWithRetry(failFn)
	_ = wrapper.CommitOffsetWithRetry(failFn)
	assert.Equal(t, float64(1), testutil.ToFloat64(metrics.CircuitBreakerState))

	// After cooldown, test operation: half-open (2)
	time.Sleep(150 * time.Millisecond)
	// The state becomes half-open when we attempt the next operation
	var stateDuringOp float64
	halfOpenFn := func() error {
		stateDuringOp = testutil.ToFloat64(metrics.CircuitBreakerState)
		return nil
	}
	_ = wrapper.CommitOffsetWithRetry(halfOpenFn)
	assert.Equal(t, float64(2), stateDuringOp)
	assert.Equal(t, float64(0), testutil.ToFloat64(metrics.CircuitBreakerState)) // Back to closed after success
}

// AC-8 Test: Retry attempts emit warn level logs
func TestAC8_RetryAttemptStructuredLogs(t *testing.T) {
	core, logs := observer.New(zap.WarnLevel)
	logger := zap.New(core)
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    2,
		InitialRetryBackoff: 10 * time.Millisecond,
		CBFailureThreshold:  10,
	}
	metrics := NewResilienceMetrics()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	failFn := func() error { return errors.New("test error") }
	_ = wrapper.CommitOffsetWithRetry(failFn)

	retryLogs := logs.FilterMessage("kafka operation retry attempt").All()
	assert.Len(t, retryLogs, 2)

	for i, log := range retryLogs {
		assert.Equal(t, zap.WarnLevel, log.Level)
		fields := log.ContextMap()
		assert.Equal(t, "commit", fields["operation_type"])
		assert.Equal(t, i+1, int(fields["attempt_number"].(float64)))
		assert.Equal(t, 2, int(fields["max_attempts"].(float64)))
		assert.Contains(t, fields["error_message"], "test error")
	}

	// Test send operation logs
	logs.TakeAll()
	_ = wrapper.SendMessageWithRetry(failFn)
	retryLogs = logs.FilterMessage("kafka operation retry attempt").All()
	assert.Len(t, retryLogs, 2)
	assert.Equal(t, "send", retryLogs[0].ContextMap()["operation_type"])
}

// AC-9 Test: Circuit breaker state transitions emit error level logs
func TestAC9_CircuitBreakerStateTransitionLogs(t *testing.T) {
	core, logs := observer.New(zap.ErrorLevel)
	logger := zap.New(core)
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    0,
		CBFailureThreshold:  2,
		CBCoolDownPeriod:    100 * time.Millisecond,
	}
	metrics := NewResilienceMetrics()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	failFn := func() error { return errors.New("fail") }
	successFn := func() error { return nil }

	// Trigger open transition
	_ = wrapper.CommitOffsetWithRetry(failFn)
	_ = wrapper.CommitOffsetWithRetry(failFn)
	_ = wrapper.CommitOffsetWithRetry(failFn)

	openLogs := logs.FilterMessage("circuit breaker state transition").All()
	assert.Len(t, openLogs, 1)
	openFields := openLogs[0].ContextMap()
	assert.Equal(t, "closed", openFields["old_state"])
	assert.Equal(t, "open", openFields["new_state"])
	assert.Equal(t, 2, int(openFields["failure_count"].(float64)))
	assert.Equal(t, "100ms", openFields["cooldown_period"])

	// Wait for cooldown, trigger half-open then closed
	logs.TakeAll()
	time.Sleep(150 * time.Millisecond)
	_ = wrapper.CommitOffsetWithRetry(successFn)

	transitionLogs := logs.FilterMessage("circuit breaker state transition").All()
	assert.GreaterOrEqual(t, len(transitionLogs), 2) // Open -> HalfOpen, HalfOpen -> Closed
}

// AC-10 Test: No retry logs or counter increments on first success, failure count reset
func TestAC10_NoRetriesOnFirstSuccess(t *testing.T) {
	core, logs := observer.New(zap.WarnLevel)
	logger := zap.New(core)
	cfg := KafkaResilienceConfig{
		MaxRetryAttempts:    3,
		CBFailureThreshold:  2,
	}
	metrics := NewResilienceMetrics()
	wrapper := NewKafkaResilienceWrapper(cfg, metrics, logger)

	// First successful operation
	beforeRetries := testutil.ToFloat64(metrics.RetryAttemptsTotal)
	err := wrapper.CommitOffsetWithRetry(func() error { return nil })
	assert.NoError(t, err)
	afterRetries := testutil.ToFloat64(metrics.RetryAttemptsTotal)
	assert.Equal(t, beforeRetries, afterRetries)
	assert.Len(t, logs.All(), 0)

	// After a failure, success resets failure count
	_ = wrapper.CommitOffsetWithRetry(func() error { return errors.New("fail") })
	err = wrapper.CommitOffsetWithRetry(func() error { return nil })
	assert.NoError(t, err)
	// Next failure should not open circuit immediately (failure count reset)
	_ = wrapper.CommitOffsetWithRetry(func() error { return errors.New("fail") })
	err = wrapper.CommitOffsetWithRetry(func() error { return errors.New("fail") })
	assert.ErrorIs(t, err, ErrKafkaOperationFailed) // Not circuit open yet
}
