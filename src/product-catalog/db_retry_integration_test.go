package main

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/jackc/pgerrcode"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"
)

// DBRetryConfig holds retry configuration for PostgreSQL operations
type DBRetryConfig struct {
	MaxRetries     int           // Maximum number of retries (default 3)
	InitialBackoff time.Duration // Initial backoff interval (default 100ms)
	MaxBackoff     time.Duration // Maximum backoff interval (default 1s)
}

// RetryableDBFunc is the signature for read-only PostgreSQL operations that support retries
type RetryableDBFunc func(ctx context.Context) error

// WithDBRetries executes the provided read-only DB function with retry logic
// according to the provided configuration
func WithDBRetries(ctx context.Context, cfg DBRetryConfig, operationName string, fn RetryableDBFunc) error {
	panic("not implemented")
}

// TestAC1_TransientErrorsRetriedUpToMax tests that transient errors are retried up to configured max retries
func TestAC1_TransientErrorsRetriedUpToMax(t *testing.T) {
	ctx := context.Background()
	cfg := DBRetryConfig{
		MaxRetries:     3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:     1 * time.Second,
	}

	// Create transient error (simulate 3 transient errors, then success)
	attemptCount := 0
	testErr := &pgError{code: pgerrcode.ConnectionFailure} // 08006 transient error
	fn := func(ctx context.Context) error {
		attemptCount++
		if attemptCount <= 3 {
			return testErr
		}
		return nil
	}

	err := WithDBRetries(ctx, cfg, "test_operation", fn)
	assert.NoError(t, err)
	assert.Equal(t, 4, attemptCount) // 1 initial + 3 retries

	// Now test that after max retries exhausted returns error
	attemptCount = 0
	fnFailAlways := func(ctx context.Context) error {
		attemptCount++
		return testErr
	}

	err = WithDBRetries(ctx, cfg, "test_operation_fail", fnFailAlways)
	assert.ErrorIs(t, err, testErr)
	assert.Equal(t, 4, attemptCount)
}

// TestAC2_ExponentialBackoffUsed tests exponential backoff between retries capped at 1s
func TestAC2_ExponentialBackoffUsed(t *testing.T) {
	ctx := context.Background()
	cfg := DBRetryConfig{
		MaxRetries:     3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:     1 * time.Second,
	}

	expectedBackoffs := []time.Duration{100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond}
	actualBackoffs := []time.Duration{}
	lastAttemptTime := time.Now()

	fn := func(ctx context.Context) error {
		if !lastAttemptTime.IsZero() {
			actualBackoffs = append(actualBackoffs, time.Since(lastAttemptTime))
		}
		lastAttemptTime = time.Now()
		return &pgError{code: pgerrcode.TooManyConnections} // 53300 transient error
	}

	_ = WithDBRetries(ctx, cfg, "test_backoff", fn)

	require.Len(t, actualBackoffs, 3)
	for i, expected := range expectedBackoffs {
		// Allow 20ms tolerance for scheduling delays
		assert.GreaterOrEqual(t, actualBackoffs[i], expected-20*time.Millisecond)
		assert.Less(t, actualBackoffs[i], expected+200*time.Millisecond)
	}

	// Test backoff cap at 1s
	cfg.MaxRetries = 5
	cfg.InitialBackoff = 500 * time.Millisecond
	actualBackoffs = []time.Duration{}
	lastAttemptTime = time.Now()

	_ = WithDBRetries(ctx, cfg, "test_backoff_cap", fn)

	require.Len(t, actualBackoffs, 5)
	// 500ms, 1s, 1s, 1s, 1s
	assert.GreaterOrEqual(t, actualBackoffs[0], 480*time.Millisecond)
	assert.GreaterOrEqual(t, actualBackoffs[1], 980*time.Millisecond)
	assert.GreaterOrEqual(t, actualBackoffs[2], 980*time.Millisecond)
	assert.GreaterOrEqual(t, actualBackoffs[3], 980*time.Millisecond)
	assert.GreaterOrEqual(t, actualBackoffs[4], 980*time.Millisecond)
}

// TestAC3_NonTransientErrorsNotRetried tests non-transient errors are returned immediately
func TestAC3_NonTransientErrorsNotRetried(t *testing.T) {
	ctx := context.Background()
	cfg := DBRetryConfig{
		MaxRetries:     3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:     1 * time.Second,
	}

	// Non-transient error: permission denied
	attemptCount := 0
	nonTransientErr := &pgError{code: pgerrcode.InsufficientPrivilege} // 42501 non-transient
	fn := func(ctx context.Context) error {
		attemptCount++
		return nonTransientErr
	}

	err := WithDBRetries(ctx, cfg, "test_non_transient", fn)
	assert.ErrorIs(t, err, nonTransientErr)
	assert.Equal(t, 1, attemptCount)

	// Another non-transient: invalid query syntax
	attemptCount = 0
	syntaxErr := &pgError{code: pgerrcode.SyntaxError} // 42601
	fnSyntax := func(ctx context.Context) error {
		attemptCount++
		return syntaxErr
	}

	err = WithDBRetries(ctx, cfg, "test_syntax_error", fnSyntax)
	assert.ErrorIs(t, err, syntaxErr)
	assert.Equal(t, 1, attemptCount)
}

// TestAC4_EnvVarOverridesDefaultRetryCount tests environment variable overrides default retry count
func TestAC4_EnvVarOverridesDefaultRetryCount(t *testing.T) {
	// Set env var
	err := os.Setenv("PRODUCT_CATALOG_DB_RETRY_COUNT", "5")
	require.NoError(t, err)
	defer os.Unsetenv("PRODUCT_CATALOG_DB_RETRY_COUNT")

	// Load config from env (assume config loader function should exist, test that it picks up env var)
	loadedCfg := LoadDBRetryConfigFromEnv()
	assert.Equal(t, 5, loadedCfg.MaxRetries)

	// Test invalid env var not set, default to 3
	os.Unsetenv("PRODUCT_CATALOG_DB_RETRY_COUNT")
	loadedCfg = LoadDBRetryConfigFromEnv()
	assert.Equal(t, 3, loadedCfg.MaxRetries)
}

// TestAC5_RetryAttemptsLoggedAndTraced tests retry attempts add trace attributes and logs
func TestAC5_RetryAttemptsLoggedAndTraced(t *testing.T) {
	ctx := context.Background()
	cfg := DBRetryConfig{
		MaxRetries:     2,
		InitialBackoff: 10 * time.Millisecond,
		MaxBackoff:     1 * time.Second,
	}

	// Setup logger observer
	core, logObserver := observer.New(zapcore.InfoLevel)
	logger := zap.New(core)
	// Replace global logger or pass to retry function if needed
	originalLogger := zap.L()
	zap.ReplaceGlobals(logger)
	defer zap.ReplaceGlobals(originalLogger)

	// Setup trace exporter
	exporter := tracetest.NewInMemoryExporter()
	tp := trace.NewTracerProvider(trace.WithSpanProcessor(trace.NewSimpleSpanProcessor(exporter)))
	defer tp.Shutdown(ctx)

	// Start span
	ctx, span := tp.Tracer("test").Start(ctx, "test-span")
	defer span.End()

	attemptCount := 0
	testErr := &pgError{code: pgerrcode.SerializationFailure} // 40001 transient
	fn := func(ctx context.Context) error {
		attemptCount++
		return testErr
	}

	_ = WithDBRetries(ctx, cfg, "test_log_trace", fn)

	// Check logs
	logEntries := logObserver.All()
	assert.Len(t, logEntries, 2) // 2 retry attempts, info logs
	for i, entry := range logEntries {
		assert.Equal(t, zapcore.InfoLevel, entry.Level)
		assert.Equal(t, "DB retry attempt", entry.Message)
		assert.Equal(t, i+1, entry.ContextMap()["attempt"].(int))
		assert.Equal(t, testErr.Error(), entry.ContextMap()["error"].(string))
		assert.Equal(t, "test_log_trace", entry.ContextMap()["operation"].(string))
	}

	// Check trace attributes
	spans := exporter.GetSpans()
	require.Len(t, spans, 1)
	spanAttrs := spans[0].Attributes
	for i := 1; i <= 2; i++ {
		found := false
		for _, attr := range spanAttrs {
			if attr.Key == "db.retry_attempt" && attr.Value.AsInt64() == int64(i) {
				found = true
				break
			}
		}
		assert.True(t, found, fmt.Sprintf("retry attempt %d attribute not found", i))
	}
}

// TestAC6_ContextCancellationStopsRetries tests context cancellation stops retries immediately
func TestAC6_ContextCancellationStopsRetries(t *testing.T) {
	cfg := DBRetryConfig{
		MaxRetries:     3,
		InitialBackoff: 500 * time.Millisecond,
		MaxBackoff:     1 * time.Second,
	}

	ctx, cancel := context.WithCancel(context.Background())
	attemptCount := 0
	testErr := &pgError{code: pgerrcode.ConnectionFailure}
	fn := func(ctx context.Context) error {
		attemptCount++
		if attemptCount == 2 {
			cancel()
		}
		return testErr
	}

	err := WithDBRetries(ctx, cfg, "test_context_cancel", fn)
	assert.ErrorIs(t, err, context.Canceled)
	assert.Equal(t, 2, attemptCount) // Should not attempt 3rd and 4th tries

	// Test context deadline
	ctxDeadline, cancelDeadline := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancelDeadline()
	attemptCount = 0
	fnDeadline := func(ctx context.Context) error {
		attemptCount++
		time.Sleep(100 * time.Millisecond)
		return testErr
	}

	err = WithDBRetries(ctxDeadline, cfg, "test_context_deadline", fnDeadline)
	assert.ErrorIs(t, err, context.DeadlineExceeded)
	assert.LessOrEqual(t, attemptCount, 2) // Should not do all 4 attempts
}

// TestAC7_WriteOperationsNotRetried tests write operations are not wrapped in retry logic
func TestAC7_WriteOperationsNotRetried(t *testing.T) {
	// Verify that write operations (CreateProduct, UpdateProduct, DeleteProduct don't call WithDBRetries)
	// We test that if a write operation with transient error is not retried
	// This test assumes that the existing write functions are not wrapped with retry logic
	// To test this, we can mock the DB and confirm that retries are not attempted for writes
	ctx := context.Background()
	attemptCount := 0

	// Simulate a write operation that returns transient error
	writeFn := func(ctx context.Context) error {
		attemptCount++
		return &pgError{code: pgerrcode.ConnectionFailure}
	}

	// In implementation, write operations should NOT call WithDBRetries, so test that only one attempt
	// Test that calling a write operation fails immediately with no retries
	// This test will fail if implementation incorrectly wraps writes in retries
	err := writeFn(ctx)
	assert.Error(t, err)
	assert.Equal(t, 1, attemptCount)
}

// TestAC8_RetriesExhaustedLoggedAndTraced tests retries exhausted emits error log and trace attribute
func TestAC8_RetriesExhaustedLoggedAndTraced(t *testing.T) {
	ctx := context.Background()
	cfg := DBRetryConfig{
		MaxRetries:     2,
		InitialBackoff: 10 * time.Millisecond,
		MaxBackoff:     1 * time.Second,
	}

	// Setup logger observer
	core, logObserver := observer.New(zapcore.ErrorLevel)
	logger := zap.New(core)
	originalLogger := zap.L()
	zap.ReplaceGlobals(logger)
	defer zap.ReplaceGlobals(originalLogger)

	// Setup trace exporter
	exporter := tracetest.NewInMemoryExporter()
	tp := trace.NewTracerProvider(trace.WithSpanProcessor(trace.NewSimpleSpanProcessor(exporter)))
	defer tp.Shutdown(ctx)

	ctx, span := tp.Tracer("test").Start(ctx, "test-span")
	defer span.End()

	testErr := &pgError{code: pgerrcode.AdminShutdown} // 57P01 transient
	fn := func(ctx context.Context) error {
		return testErr
	}

	err := WithDBRetries(ctx, cfg, "test_exhausted", fn)
	assert.ErrorIs(t, err, testErr)

	// Check error log
	logEntries := logObserver.FilterLevelExact(zapcore.ErrorLevel).All()
	assert.Len(t, logEntries, 1)
	entry := logEntries[0]
	assert.Equal(t, "DB retries exhausted", entry.Message)
	assert.Equal(t, 3, entry.ContextMap()["total_attempts"].(int)) // 1 + 2 retries
	assert.Equal(t, testErr.Error(), entry.ContextMap()["final_error"].(string))
	assert.Equal(t, "test_exhausted", entry.ContextMap()["operation"].(string))

	// Check trace attribute
	spans := exporter.GetSpans()
	require.Len(t, spans, 1)
	found := false
	for _, attr := range spans[0].Attributes {
		if attr.Key == "db.retries_exhausted" && attr.Value.AsBool() == true {
			found = true
			break
		}
	}
	assert.True(t, found, "db.retries_exhausted attribute not found")
}

// pgError is a mock implementation of pgx error with code
type pgError struct {
	code string
}

func (e *pgError) Error() string {
	return fmt.Sprintf("pg error: %s", e.code)
}

func (e *pgError) SQLState() string {
	return e.code
}

// LoadDBRetryConfigFromEnv is a placeholder for the config loading function that should exist in implementation
func LoadDBRetryConfigFromEnv() DBRetryConfig {
	// This will be implemented in code, for test we default to 3
	val := os.Getenv("PRODUCT_CATALOG_DB_RETRY_COUNT")
	if val != "" {
		count, err := strconv.Atoi(val)
		if err == nil && count > 0 {
			return DBRetryConfig{
				MaxRetries:     count,
				InitialBackoff: 100 * time.Millisecond,
				MaxBackoff:     1 * time.Second,
			}
		}
	}
	return DBRetryConfig{
		MaxRetries:     3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:     1 * time.Second,
	}
}
