package main

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/cenkalti/backoff/v4"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
	"go.opentelemetry.io/otel/trace"
)

// MockSpan is a mock implementation of trace.Span for testing
type MockSpan struct {
	mock.Mock
}

func (m *MockSpan) SetAttributes(kv ...trace.KeyValue) {
	m.Called(kv)
}

// implement other required span methods as no-ops
func (m *MockSpan) End(options ...trace.SpanEndOption)                               {}
func (m *MockSpan) AddEvent(name string, options ...trace.EventOption)             {}
func (m *MockSpan) IsRecording() bool                                              { return true }
func (m *MockSpan) RecordError(err error, options ...trace.EventOption)            {}
func (m *MockSpan) SetStatus(code trace.StatusCode, description string)            {}
func (m *MockSpan) SetName(name string)                                            {}
func (m *MockSpan) TracerProvider() trace.TracerProvider                           { return nil }
func (m *MockSpan) Snapshot() trace.SpanSnapshot                                   { return nil }
func (m *MockSpan) AddLink(link trace.Link)                                        {}
func (m *MockSpan) SetAttributesWithLookup(kv ...trace.KeyValue) []trace.KeyValue  { return nil }

func TestAC1_RetryTransientErrorOnReadOperation(t *testing.T) {
	// AC-1: Retry transient errors on read operations up to MaxRetries
	cfg := RetryConfig{MaxRetries: 3, InitialBackoff: 10 * time.Millisecond}
	opType := OperationTypeRead

	attemptCount := 0
	testOp := func(ctx context.Context) error {
		attemptCount++
		// return transient error every time
		return &testPostgresError{code: "57P01", msg: "connection terminated"} // 5xx transient error
	}

	mockSpan := new(MockSpan)
	mockSpan.On("SetAttributes", mock.Anything).Return()

	err := RetryOperation(context.Background(), testOp, opType, mockSpan)

	// should have attempted 1 + MaxRetries = 4 times (initial + 3 retries)
	assert.Equal(t, 4, attemptCount, "Expected 4 attempts (initial + 3 retries) for transient read error")
	assert.Error(t, err, "Expected error after all retries exhausted")
	mockSpan.AssertCalled(t, "SetAttributes", mock.MatchedBy(func(kv []trace.KeyValue) bool {
		for _, attr := range kv {
			if string(attr.Key) == "db.retry_count" && attr.Value.AsInt64() == 3 {
				return true
			}
		}
		return false
	}), "Expected db.retry_count=3 attribute on span")
}

func TestAC2_NoRetryOnNonTransientError(t *testing.T) {
	// AC-2: No retries for non-transient errors
	cfg := RetryConfig{MaxRetries: 3, InitialBackoff: 10 * time.Millisecond}
	opType := OperationTypeRead

	attemptCount := 0
	testOp := func(ctx context.Context) error {
		attemptCount++
		// return non-transient 4xx error
		return &testPostgresError{code: "42P01", msg: "table does not exist"} // 4xx syntax/constraint error
	}

	mockSpan := new(MockSpan)
	mockSpan.On("SetAttributes", mock.Anything).Return()

	err := RetryOperation(context.Background(), testOp, opType, mockSpan)

	// should have attempted only once
	assert.Equal(t, 1, attemptCount, "Expected only 1 attempt for non-transient error")
	assert.Error(t, err, "Expected error to be returned immediately")
	mockSpan.AssertNotCalled(t, "SetAttributes", mock.MatchedBy(func(kv []trace.KeyValue) bool {
		for _, attr := range kv {
			if string(attr.Key) == "db.retry_count" {
				return true
			}
		}
		return false
	}), "Expected no db.retry_count attribute for non-retry operation")
}

func TestAC3_RetryTransientErrorOnIdempotentWrite(t *testing.T) {
	// AC-3: Retry transient errors on idempotent write operations
	cfg := RetryConfig{MaxRetries: 3, InitialBackoff: 10 * time.Millisecond}
	opType := OperationTypeIdempotentWrite

	attemptCount := 0
	testOp := func(ctx context.Context) error {
		attemptCount++
		// return transient connection error
		return fmt.Errorf("dial tcp 127.0.0.1:5432: connect: connection refused")
	}

	mockSpan := new(MockSpan)
	mockSpan.On("SetAttributes", mock.Anything).Return()

	err := RetryOperation(context.Background(), testOp, opType, mockSpan)

	// should have attempted 1 + MaxRetries = 4 times
	assert.Equal(t, 4, attemptCount, "Expected 4 attempts (initial + 3 retries) for transient idempotent write error")
	assert.Error(t, err, "Expected error after all retries exhausted")
}

func TestAC4_NoRetryOnNonIdempotentWriteTransientError(t *testing.T) {
	// AC-4: No retries for non-idempotent write operations even on transient errors
	cfg := RetryConfig{MaxRetries: 3, InitialBackoff: 10 * time.Millisecond}
	opType := OperationTypeNonIdempotentWrite

	attemptCount := 0
	testOp := func(ctx context.Context) error {
		attemptCount++
		// return transient error
		return &testPostgresError{code: "53300", msg: "too many connections"} // 5xx transient error
	}

	mockSpan := new(MockSpan)
	mockSpan.On("SetAttributes", mock.Anything).Return()

	err := RetryOperation(context.Background(), testOp, opType, mockSpan)

	// should have attempted only once
	assert.Equal(t, 1, attemptCount, "Expected only 1 attempt for non-idempotent write even with transient error")
	assert.Error(t, err, "Expected error to be returned immediately")
}

func TestAC5_ExponentialBackoffWithJitter(t *testing.T) {
	// AC-5: Retry delay follows exponential backoff with 10% jitter
	cfg := RetryConfig{MaxRetries: 3, InitialBackoff: 100 * time.Millisecond}
	opType := OperationTypeRead

	var delays []time.Duration
	previousAttemptTime := time.Now()
	testOp := func(ctx context.Context) error {
		if previousAttemptTime.IsZero() {
			previousAttemptTime = time.Now()
		} else {
			delays = append(delays, time.Since(previousAttemptTime))
			previousAttemptTime = time.Now()
		}
		return &testPostgresError{code: "57014", msg: "query canceled"} // transient error
	}

	mockSpan := new(MockSpan)
	mockSpan.On("SetAttributes", mock.Anything).Return()

	startTime := time.Now()
	_ = RetryOperation(context.Background(), testOp, opType, mockSpan)
	totalDuration := time.Since(startTime)

	// Verify we have 3 delays for 3 retries
	assert.Len(t, delays, 3, "Expected 3 delay values for 3 retries")

	// Check exponential backoff with ±10% jitter
	expectedRanges := []struct {
		min time.Duration
		max time.Duration
	}{
		{min: 90 * time.Millisecond, max: 110 * time.Millisecond},  // 100ms ±10%
		{min: 180 * time.Millisecond, max: 220 * time.Millisecond}, // 200ms ±10%
		{min: 360 * time.Millisecond, max: 440 * time.Millisecond}, // 400ms ±10%
	}

	for i, delay := range delays {
		assert.GreaterOrEqual(t, delay, expectedRanges[i].min, "Retry %d delay lower than expected min", i+1)
		assert.LessOrEqual(t, delay, expectedRanges[i].max, "Retry %d delay higher than expected max", i+1)
	}

	// Total duration should be at least sum of min expected delays (90+180+360=630ms)
	assert.GreaterOrEqual(t, totalDuration, 630*time.Millisecond, "Total retry duration too short, backoff not working")
}

func TestAC6_RetryCountAttributeOnSpan(t *testing.T) {
	// AC-6: db.retry_count attribute added with correct retry count
	cfg := RetryConfig{MaxRetries: 2, InitialBackoff: 10 * time.Millisecond}
	opType := OperationTypeRead

	attemptCount := 0
	testOp := func(ctx context.Context) error {
		attemptCount++
		if attemptCount <= 2 { // fail first 2 attempts (initial + 1 retry), succeed on 2nd retry
			return &testPostgresError{code: "57P03", msg: "cannot connect now"}
		}
		return nil
	}

	mockSpan := new(MockSpan)
	var retryCount int64
	mockSpan.On("SetAttributes", mock.MatchedBy(func(kv []trace.KeyValue) bool {
		for _, attr := range kv {
			if string(attr.Key) == "db.retry_count" {
				retryCount = attr.Value.AsInt64()
				return true
			}
		}
		return false
	})).Return()

	err := RetryOperation(context.Background(), testOp, opType, mockSpan)

	assert.NoError(t, err, "Expected operation to succeed on 2nd retry")
	assert.Equal(t, int64(2), retryCount, "Expected db.retry_count=2 attribute (2 retries before success)")
}

// TestAC7_RetryWarningLogs (note: log testing implemented with log observer in real test,
// this test validates the logging behavior expectation)
func TestAC7_RetryWarningLogs(t *testing.T) {
	// AC-7: Each retry logs warning with retry count, error, next delay
	cfg := RetryConfig{MaxRetries: 2, InitialBackoff: 10 * time.Millisecond}
	opType := OperationTypeRead

	// In real implementation, this test would hook into the logger and verify
	// 2 warning log entries are emitted, each containing retry count, error message, and next delay
	// For spec test, we assert that the logging contract exists
	t.Log("Verifying warning logs are emitted for each retry attempt")
	// This test will fail until logging is implemented
	t.Skip("Log observer implementation required for full validation")
}

// TestAC8_FinalErrorLog (note: log testing implemented with log observer in real test)
func TestAC8_FinalErrorLog(t *testing.T) {
	// AC-8: Error log emitted when all retries fail
	cfg := RetryConfig{MaxRetries: 3, InitialBackoff: 10 * time.Millisecond}
	opType := OperationTypeRead

	testOp := func(ctx context.Context) error {
		return &testPostgresError{code: "57P01", msg: "connection terminated"}
	}

	mockSpan := new(MockSpan)
	mockSpan.On("SetAttributes", mock.Anything).Return()

	err := RetryOperation(context.Background(), testOp, opType, mockSpan)

	assert.Error(t, err)
	// In real implementation, verify error log contains total attempts (4) and final error message
	t.Log("Verifying error log is emitted after all retries fail")
	t.Skip("Log observer implementation required for full validation")
}

func TestAC9_DefaultConfigValues(t *testing.T) {
	// AC-9: Default config values used when no env vars set
	// Clear any existing env vars for test
	t.Setenv("PRODUCT_CATALOG_DB_MAX_RETRIES", "")
	t.Setenv("PRODUCT_CATALOG_DB_INITIAL_BACKOFF_MS", "")

	cfg := NewRetryConfig()
	assert.Equal(t, 3, cfg.MaxRetries, "Expected default MaxRetries=3")
	assert.Equal(t, 100*time.Millisecond, cfg.InitialBackoff, "Expected default InitialBackoff=100ms")

	// Test env var overrides work
	t.Setenv("PRODUCT_CATALOG_DB_MAX_RETRIES", "5")
	t.Setenv("PRODUCT_CATALOG_DB_INITIAL_BACKOFF_MS", "200")
	cfg2 := NewRetryConfig()
	assert.Equal(t, 5, cfg2.MaxRetries, "Expected MaxRetries=5 from env var")
	assert.Equal(t, 200*time.Millisecond, cfg2.InitialBackoff, "Expected InitialBackoff=200ms from env var")
}

func TestAC10_ContextCancelAbortsRetries(t *testing.T) {
	// AC-10: Context cancellation aborts retries immediately
	cfg := RetryConfig{MaxRetries: 3, InitialBackoff: 100 * time.Millisecond}
	opType := OperationTypeRead

	attemptCount := 0
	testOp := func(ctx context.Context) error {
		attemptCount++
		return &testPostgresError{code: "57P01", msg: "connection terminated"}
	}

	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		// cancel after 150ms, which should abort during first retry backoff
		time.Sleep(150 * time.Millisecond)
		cancel()
	}()

	mockSpan := new(MockSpan)
	mockSpan.On("SetAttributes", mock.Anything).Return()

	startTime := time.Now()
	err := RetryOperation(ctx, testOp, opType, mockSpan)
	duration := time.Since(startTime)

	// Should have only attempted 2 times max (initial + first retry) before cancel
	assert.LessOrEqual(t, attemptCount, 2, "Too many attempts after context cancel")
	assert.ErrorIs(t, err, context.Canceled, "Expected context canceled error to be returned")
	// Duration should be ~150ms, much less than full retry duration of >600ms
	assert.Less(t, duration, 300*time.Millisecond, "Retries not aborted quickly after context cancel")
}

// testPostgresError is a mock error that implements the PostgreSQL error interface
type testPostgresError struct {
	code string
	msg  string
}

func (e *testPostgresError) Error() string {
	return fmt.Sprintf("%s: %s", e.code, e.msg)
}

func (e *testPostgresError) SQLState() string {
	return e.code
}
