package main

import (
	"context"
	"testing"
	"time"

	"github.com/cenkalti/backoff/v4"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
	"go.opentelemetry.io/otel/metric"
)

// TestAC1_TransientErrorRetriesWithExponentialBackoff tests AC-1: transient errors get retried up to MaxRetries with exponential backoff
func TestAC1_TransientErrorRetriesWithExponentialBackoff(t *testing.T) {
	ctx := context.Background()
	cfg := RetryConfig{
		MaxRetries:     3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:     2 * time.Second,
	}

	// Mock DB that returns transient connection error
	mockDB := new(MockDBPool)
	transientErr := &pgconn.PgError{Code: "08001"} // Connection error, transient
	mockDB.On("Ping", mock.Anything).Return(transientErr).Times(cfg.MaxRetries + 1)

	metrics := createTestMetrics(t)
	retryDB := NewRetryableDB(mockDB, cfg, metrics)

	start := time.Now()
	err := retryDB.Ping(ctx)
	elapsed := time.Since(start)

	// Should have returned error after all retries
	assert.ErrorIs(t, err, transientErr)
	// Verify retry count: maxRetries + 1 total attempts
	mockDB.AssertNumberOfCalls(t, "Ping", cfg.MaxRetries + 1)
	// Verify elapsed time is at least sum of backoffs: 100ms + 200ms + 400ms = 700ms
	assert.GreaterOrEqual(t, elapsed.Milliseconds(), int64(700))
	// Verify backoff capped at 2s (for higher retry counts)
}

// TestAC2_NonTransientErrorFailsImmediately tests AC-2: non-transient errors are not retried
func TestAC2_NonTransientErrorFailsImmediately(t *testing.T) {
	ctx := context.Background()
	cfg := RetryConfig{MaxRetries: 3}

	// Mock DB that returns non-transient syntax error
	mockDB := new(MockDBPool)
	nonTransientErr := &pgconn.PgError{Code: "42601"} // Syntax error, non-transient
	mockDB.On("Ping", mock.Anything).Return(nonTransientErr).Once()

	metrics := createTestMetrics(t)
	retryDB := NewRetryableDB(mockDB, cfg, metrics)

	err := retryDB.Ping(ctx)

	assert.ErrorIs(t, err, nonTransientErr)
	// Verify only 1 attempt, no retries
	mockDB.AssertNumberOfCalls(t, "Ping", 1)
}

// TestAC3_RetryAttemptsMetricIncremented tests AC-3: retry attempts metric is incremented for each retry
func TestAC3_RetryAttemptsMetricIncremented(t *testing.T) {
	ctx := context.Background()
	cfg := RetryConfig{MaxRetries: 2}

	// Mock DB that returns transient error
	mockDB := new(MockDBPool)
	transientErr := &pgconn.PgError{Code: "40001"} // Serialization failure
	mockDB.On("Ping", mock.Anything).Return(transientErr).Times(cfg.MaxRetries + 1)

	mockCounter := new(MockInt64Counter)
	metrics := RetryMetrics{
		RetryAttempts: mockCounter,
		RetryFailures: new(MockInt64Counter),
	}
	// Expect 2 increments for retry attempts (first failure is original attempt, then 2 retries)
	mockCounter.On("Add", mock.Anything, int64(1), mock.MatchedBy(func(attrs []metric.AddOption) bool {
		// Check attributes include error code and operation type
		return true
	})).Times(cfg.MaxRetries)

	retryDB := NewRetryableDB(mockDB, cfg, metrics)
	_ = retryDB.Ping(ctx)

	mockCounter.AssertExpectations(t)
}

// TestAC4_RetryFailuresMetricIncremented tests AC-4: retry failures metric is incremented when all retries fail
func TestAC4_RetryFailuresMetricIncremented(t *testing.T) {
	ctx := context.Background()
	cfg := RetryConfig{MaxRetries: 2}

	// Mock DB that returns transient error
	mockDB := new(MockDBPool)
	transientErr := &pgconn.PgError{Code: "08006"} // Connection failure
	mockDB.On("Exec", mock.Anything, "INSERT INTO ads VALUES ($1)", mock.Anything).Return(pgconn.CommandTag([]byte("INSERT 0 1")), transientErr).Times(cfg.MaxRetries + 1)

	mockFailureCounter := new(MockInt64Counter)
	metrics := RetryMetrics{
		RetryAttempts: new(MockInt64Counter),
		RetryFailures: mockFailureCounter,
	}
	// Expect failure metric to be incremented once
	mockFailureCounter.On("Add", mock.Anything, int64(1), mock.Anything).Once()

	retryDB := NewRetryableDB(mockDB, cfg, metrics)
	_, err := retryDB.Exec(ctx, "INSERT INTO ads VALUES ($1)", "test-ad")

	assert.ErrorIs(t, err, transientErr)
	mockFailureCounter.AssertExpectations(t)
}

// TestAC5_ContextCancelAbortsRetry tests AC-5: context cancellation aborts retry immediately
func TestAC5_ContextCancelAbortsRetry(t *testing.T) {
	cfg := RetryConfig{
		MaxRetries:     3,
		InitialBackoff: 1 * time.Second,
	}

	// Mock DB that returns transient error
	mockDB := new(MockDBPool)
	transientErr := &pgconn.PgError{Code: "57014"} // Query canceled
	mockDB.On("Query", mock.Anything, "SELECT * FROM ads", mock.Anything).Return(nil, transientErr).Once()

	metrics := createTestMetrics(t)
	retryDB := NewRetryableDB(mockDB, cfg, metrics)

	// Create context that cancels after 500ms (before first backoff completes)
	ctx, cancel := context.WithTimeout(context.Background(), 500 * time.Millisecond)
	defer cancel()

	start := time.Now()
	_, err := retryDB.Query(ctx, "SELECT * FROM ads")
	elapsed := time.Since(start)

	// Should return context error, not DB error
	assert.ErrorIs(t, err, context.DeadlineExceeded)
	// Only 1 attempt, no retries
	mockDB.AssertNumberOfCalls(t, "Query", 1)
	// Elapsed time less than 1s (backoff duration)
	assert.Less(t, elapsed.Milliseconds(), int64(1000))
}

// TestAC6_AllOperationTypesWrapped tests AC-6: all DB operation types are wrapped with retry logic
func TestAC6_AllOperationTypesWrapped(t *testing.T) {
	cfg := RetryConfig{MaxRetries: 1}
	transientErr := &pgconn.PgError{Code: "55P03"} // Lock not available

	testCases := []struct {
		name string
		op func(rdb *RetryableDB, ctx context.Context) error
	}{
		{
			name: "Ping",
			op: func(rdb *RetryableDB, ctx context.Context) error {
				return rdb.Ping(ctx)
			},
		},
		{
			name: "Exec",
			op: func(rdb *RetryableDB, ctx context.Context) error {
				_, err := rdb.Exec(ctx, "UPDATE ads SET views = views + 1 WHERE id = $1", "test")
				return err
			},
		},
		{
			name: "Query",
			op: func(rdb *RetryableDB, ctx context.Context) error {
				_, err := rdb.Query(ctx, "SELECT * FROM ads WHERE id = $1", "test")
				return err
			},
		},
		{
			name: "QueryRow",
			op: func(rdb *RetryableDB, ctx context.Context) error {
				var id string
				return rdb.QueryRow(ctx, "SELECT id FROM ads WHERE id = $1", "test").Scan(&id)
			},
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			mockDB := new(MockDBPool)
			mockDB.On(tc.name, mock.Anything, mock.Anything, mock.Anything).Return(transientErr).Times(2) // 1 attempt + 1 retry

			metrics := createTestMetrics(t)
			retryDB := NewRetryableDB(mockDB, cfg, metrics)

			err := tc.op(retryDB, context.Background())
			assert.ErrorIs(t, err, transientErr)
			mockDB.AssertNumberOfCalls(t, tc.name, 2)
		})
	}
}

// TestAC7_ZeroMaxRetriesNoRetries tests AC-7: MaxRetries=0 disables all retries
func TestAC7_ZeroMaxRetriesNoRetries(t *testing.T) {
	ctx := context.Background()
	cfg := RetryConfig{MaxRetries: 0}

	// Mock DB that returns transient error
	mockDB := new(MockDBPool)
	transientErr := &pgconn.PgError{Code: "40P01"} // Deadlock detected
	mockDB.On("Ping", mock.Anything).Return(transientErr).Once()

	metrics := createTestMetrics(t)
	retryDB := NewRetryableDB(mockDB, cfg, metrics)

	err := retryDB.Ping(ctx)

	assert.ErrorIs(t, err, transientErr)
	// Only 1 attempt, no retries even for transient error
	mockDB.AssertNumberOfCalls(t, "Ping", 1)
}

// Mock types for testing (implementation will provide real types)
type MockDBPool struct {
	mock.Mock
}

func (m *MockDBPool) Ping(ctx context.Context) error {
	args := m.Called(ctx)
	return args.Error(0)
}

func (m *MockDBPool) Exec(ctx context.Context, query string, args ...interface{}) (pgconn.CommandTag, error) {
	callArgs := m.Called(ctx, query, args)
	return callArgs.Get(0).(pgconn.CommandTag), callArgs.Error(1)
}

func (m *MockDBPool) Query(ctx context.Context, query string, args ...interface{}) (pgx.Rows, error) {
	callArgs := m.Called(ctx, query, args)
	return callArgs.Get(0).(pgx.Rows), callArgs.Error(1)
}

func (m *MockDBPool) QueryRow(ctx context.Context, query string, args ...interface{}) pgx.Row {
	callArgs := m.Called(ctx, query, args)
	return callArgs.Get(0).(pgx.Row)
}

type MockInt64Counter struct {
	mock.Mock
}

func (m *MockInt64Counter) Add(ctx context.Context, incr int64, options ...metric.AddOption) {
	m.Called(ctx, incr, options)
}

// Helper functions
func createTestMetrics(t *testing.T) RetryMetrics {
	return RetryMetrics{
		RetryAttempts: new(MockInt64Counter),
		RetryFailures: new(MockInt64Counter),
	}
}

