package main

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/cenkalti/backoff/v4"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

// MockPostgresClient is a mock for the PostgreSQL client
type MockPostgresClient struct {
	mock.Mock
}

func (m *MockPostgresClient) GetProduct(ctx context.Context, id string) (*Product, error) {
	args := m.Called(ctx, id)
	if args.Get(0) == nil {
		return nil, args.Error(1)
	}
	return args.Get(0).(*Product), args.Error(1)
}

func (m *MockPostgresClient) ListProducts(ctx context.Context, limit, offset int) ([]Product, error) {
	args := m.Called(ctx, limit, offset)
	if args.Get(0) == nil {
		return nil, args.Error(1)
	}
	return args.Get(0).([]Product), args.Error(1)
}

func (m *MockPostgresClient) SearchProducts(ctx context.Context, query string) ([]Product, error) {
	args := m.Called(ctx, query)
	if args.Get(0) == nil {
		return nil, args.Error(1)
	}
	return args.Get(0).([]Product), args.Error(1)
}

func (m *MockPostgresClient) CreateProduct(ctx context.Context, p *Product) error {
	args := m.Called(ctx, p)
	return args.Error(0)
}

func (m *MockPostgresClient) UpdateProduct(ctx context.Context, p *Product) error {
	args := m.Called(ctx, p)
	return args.Error(0)
}

func (m *MockPostgresClient) DeleteProduct(ctx context.Context, id string) error {
	args := m.Called(ctx, id)
	return args.Error(0)
}

// TestAC1_GetProductRetriesOnTransientError tests AC-1: When a transient PostgreSQL error occurs during a GetProduct call,
// the operation is retried up to 3 total attempts before returning an error.
func TestAC1_GetProductRetriesOnTransientError(t *testing.T) {
	// Setup mock client that returns transient error 3 times
	mockClient := new(MockPostgresClient)
	transientErr := &pgconn.PgError{Code: "40P01"} // Deadlock detected, transient error

	// Expect 3 calls (initial + 2 retries = 3 total attempts)
	mockClient.On("GetProduct", mock.Anything, "test-id").Return(nil, transientErr).Times(3)

	// Create retry middleware with default config
	retryMiddleware := NewPostgresRetryMiddleware(RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 1 * time.Millisecond, // Short backoff for test
		MaxBackoff:     10 * time.Millisecond,
		JitterFactor:   0, // No jitter for predictable test
	})

	// Execute GetProduct via middleware
	ctx := context.Background()
	var product *Product
	err := retryMiddleware.Execute(ctx, "GetProduct", true, func() error {
		var opErr error
		product, opErr = mockClient.GetProduct(ctx, "test-id")
		return opErr
	})

	// Assertions
	assert.Error(t, err)
	assert.Equal(t, transientErr, err)
	mockClient.AssertNumberOfCalls(t, "GetProduct", 3)
}

// TestAC2_ListProductsUsesExponentialBackoffWithJitter tests AC-2: When a transient PostgreSQL error occurs during a ListProducts call,
// the operation uses exponential backoff with jitter between retry attempts, with initial delay of 100ms ± 20% jitter,
// doubling each retry up to a maximum of 2s ± 20% jitter.
func TestAC2_ListProductsUsesExponentialBackoffWithJitter(t *testing.T) {
	// Setup mock client that returns transient error 3 times
	mockClient := new(MockPostgresClient)
	transientErr := &pgconn.PgError{Code: "08006"} // Connection failure, transient error
	mockClient.On("ListProducts", mock.Anything, 10, 0).Return(nil, transientErr).Times(3)

	// Track delays between attempts
	var attemptTimes []time.Time
	retryMiddleware := NewPostgresRetryMiddleware(RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:     2 * time.Second,
		JitterFactor:   0.2,
	})

	// Execute ListProducts via middleware
	ctx := context.Background()
	startTime := time.Now()
	var products []Product
	err := retryMiddleware.Execute(ctx, "ListProducts", true, func() error {
		attemptTimes = append(attemptTimes, time.Now())
		var opErr error
		products, opErr = mockClient.ListProducts(ctx, 10, 0)
		return opErr
	})

	// Assertions
	assert.Error(t, err)
	assert.Len(t, attemptTimes, 3)

	// Check first delay between attempt 0 and 1: ~100ms ±20%
	firstDelay := attemptTimes[1].Sub(attemptTimes[0])
	assert.GreaterOrEqual(t, firstDelay.Milliseconds(), int64(80))  // 100 * 0.8
	assert.LessOrEqual(t, firstDelay.Milliseconds(), int64(120))   // 100 * 1.2

	// Check second delay between attempt 1 and 2: ~200ms ±20%
	secondDelay := attemptTimes[2].Sub(attemptTimes[1])
	assert.GreaterOrEqual(t, secondDelay.Milliseconds(), int64(160)) // 200 * 0.8
	assert.LessOrEqual(t, secondDelay.Milliseconds(), int64(240))    // 200 * 1.2
}

// TestAC3_SearchProductsAddsRetryTraceAttributes tests AC-3: When a transient PostgreSQL error occurs during a SearchProducts call,
// trace attributes for retry attempt count, max attempts, error type, and delay are added to the existing span for the operation.
func TestAC3_SearchProductsAddsRetryTraceAttributes(t *testing.T) {
	// Setup trace exporter to capture spans
	exporter := tracetest.NewInMemoryExporter()
	tp := trace.NewTracerProvider(trace.WithSyncer(exporter))
	defer tp.Shutdown(context.Background())

	// Setup mock client that returns transient error once then succeeds
	mockClient := new(MockPostgresClient)
	transientErr := &pgconn.PgError{Code: "55P03"} // Lock not available, transient error
	mockClient.On("SearchProducts", mock.Anything, "test-query").Return(nil, transientErr).Once()
	mockClient.On("SearchProducts", mock.Anything, "test-query").Return([]Product{}, nil).Once()

	// Create retry middleware
	retryMiddleware := NewPostgresRetryMiddleware(RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 1 * time.Millisecond,
		MaxBackoff:     10 * time.Millisecond,
		JitterFactor:   0,
	})

	// Execute SearchProducts via middleware with tracing
	ctx, span := tp.Tracer("test").Start(context.Background(), "SearchProducts")
	defer span.End()

	var products []Product
	err := retryMiddleware.Execute(ctx, "SearchProducts", true, func() error {
		var opErr error
		products, opErr = mockClient.SearchProducts(ctx, "test-query")
		return opErr
	})

	// Assertions
	assert.NoError(t, err)
	mockClient.AssertNumberOfCalls(t, "SearchProducts", 2)

	// Check span attributes
	spans := exporter.GetSpans()
	assert.GreaterOrEqual(t, len(spans), 1)
	searchSpan := spans[len(spans)-1]

	expectedAttributes := map[string]interface{}{
		"db.retry.attempt":      int64(1),
		"db.retry.max_attempts": int64(3),
		"db.retry.error_type":   "*pgconn.PgError",
	}

	for _, attr := range searchSpan.Attributes {
		if expectedVal, ok := expectedAttributes[string(attr.Key)]; ok {
			switch attr.Value.Type() {
			case attribute.INT64:
				assert.Equal(t, expectedVal, attr.Value.AsInt64())
			case attribute.STRING:
				assert.Equal(t, expectedVal, attr.Value.AsString())
			}
			delete(expectedAttributes, string(attr.Key))
		}
	}

	// Verify we found all expected attributes
	assert.Empty(t, expectedAttributes, "Missing expected trace attributes")
}

// TestAC4_CreateProductDoesNotRetryOnTransientError tests AC-4: When a transient PostgreSQL error occurs during a CreateProduct
// (write operation) call, no retry attempts are made, and the error is returned immediately.
func TestAC4_CreateProductDoesNotRetryOnTransientError(t *testing.T) {
	// Setup mock client that returns transient error
	mockClient := new(MockPostgresClient)
	transientErr := &pgconn.PgError{Code: "40P01"} // Deadlock detected, transient error
	mockClient.On("CreateProduct", mock.Anything, mock.AnythingOfType("*main.Product")).Return(transientErr).Once()

	// Create retry middleware
	retryMiddleware := NewPostgresRetryMiddleware(RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 1 * time.Millisecond,
		MaxBackoff:     10 * time.Millisecond,
		JitterFactor:   0,
	})

	// Execute CreateProduct via middleware (non-idempotent)
	ctx := context.Background()
	testProduct := &Product{ID: "test-id", Name: "Test Product"}
	err := retryMiddleware.Execute(ctx, "CreateProduct", false, func() error {
		return mockClient.CreateProduct(ctx, testProduct)
	})

	// Assertions
	assert.Error(t, err)
	assert.Equal(t, transientErr, err)
	mockClient.AssertNumberOfCalls(t, "CreateProduct", 1) // Only 1 call, no retries
}

// TestAC5_NonTransientErrorDoesNotRetry tests AC-5: When a non-transient PostgreSQL error (e.g. invalid query syntax,
// constraint violation) occurs during any operation, no retry attempts are made, and the error is returned immediately.
func TestAC5_NonTransientErrorDoesNotRetry(t *testing.T) {
	// Setup mock client that returns non-transient error
	mockClient := new(MockPostgresClient)
	nonTransientErr := &pgconn.PgError{Code: "42601"} // Syntax error, non-transient
	mockClient.On("GetProduct", mock.Anything, "test-id").Return(nil, nonTransientErr).Once()

	// Create retry middleware
	retryMiddleware := NewPostgresRetryMiddleware(RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 1 * time.Millisecond,
		MaxBackoff:     10 * time.Millisecond,
		JitterFactor:   0,
	})

	// Execute GetProduct via middleware
	ctx := context.Background()
	var product *Product
	err := retryMiddleware.Execute(ctx, "GetProduct", true, func() error {
		var opErr error
		product, opErr = mockClient.GetProduct(ctx, "test-id")
		return opErr
	})

	// Assertions
	assert.Error(t, err)
	assert.Equal(t, nonTransientErr, err)
	mockClient.AssertNumberOfCalls(t, "GetProduct", 1) // Only 1 call, no retries
}

// TestAC6_SuccessfulRetryLogsDebugEntry tests AC-6: When an operation succeeds on the nth retry attempt (n >= 1),
// a debug log entry is recorded containing the operation name, attempt count, and total time spent retrying.
func TestAC6_SuccessfulRetryLogsDebugEntry(t *testing.T) {
	// Setup mock logger to capture logs
	var logMessages []string
	mockLog := func(level, msg string, keysAndValues ...interface{}) {
		logMessages = append(logMessages, fmt.Sprintf("%s: %s %v", level, msg, keysAndValues))
	}

	// Setup mock client that returns transient error once then succeeds
	mockClient := new(MockPostgresClient)
	transientErr := &pgconn.PgError{Code: "08001"} // Connection failure, transient error
	mockClient.On("GetProduct", mock.Anything, "test-id").Return(nil, transientErr).Once()
	mockClient.On("GetProduct", mock.Anything, "test-id").Return(&Product{ID: "test-id"}, nil).Once()

	// Create retry middleware
	retryMiddleware := NewPostgresRetryMiddleware(RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 1 * time.Millisecond,
		MaxBackoff:     10 * time.Millisecond,
		JitterFactor:   0,
	})

	// Execute GetProduct via middleware
	ctx := context.Background()
	var product *Product
	err := retryMiddleware.Execute(ctx, "GetProduct", true, func() error {
		var opErr error
		product, opErr = mockClient.GetProduct(ctx, "test-id")
		return opErr
	})

	// Assertions
	assert.NoError(t, err)
	mockClient.AssertNumberOfCalls(t, "GetProduct", 2)

	// Check debug log exists
	found := false
	for _, msg := range logMessages {
		if containsSubstring(msg, "debug") && containsSubstring(msg, "operation succeeded after retry") &&
			containsSubstring(msg, "GetProduct") && containsSubstring(msg, "attempt=2") {
			found = true
			break
		}
	}
	assert.True(t, found, "Expected debug log entry for successful retry not found")
}

// TestAC7_FailedAfterAllRetriesLogsErrorEntry tests AC-7: When an operation fails after all 3 retry attempts,
// an error log entry is recorded containing the operation name, total attempts, and the final error message.
func TestAC7_FailedAfterAllRetriesLogsErrorEntry(t *testing.T) {
	// Setup mock logger to capture logs
	var logMessages []string
	mockLog := func(level, msg string, keysAndValues ...interface{}) {
		logMessages = append(logMessages, fmt.Sprintf("%s: %s %v", level, msg, keysAndValues))
	}

	// Setup mock client that returns transient error 3 times
	mockClient := new(MockPostgresClient)
	transientErr := &pgconn.PgError{Code: "57P03"} // Cannot connect now, transient error
	mockClient.On("GetProduct", mock.Anything, "test-id").Return(nil, transientErr).Times(3)

	// Create retry middleware
	retryMiddleware := NewPostgresRetryMiddleware(RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 1 * time.Millisecond,
		MaxBackoff:     10 * time.Millisecond,
		JitterFactor:   0,
	})

	// Execute GetProduct via middleware
	ctx := context.Background()
	var product *Product
	err := retryMiddleware.Execute(ctx, "GetProduct", true, func() error {
		var opErr error
		product, opErr = mockClient.GetProduct(ctx, "test-id")
		return opErr
	})

	// Assertions
	assert.Error(t, err)
	mockClient.AssertNumberOfCalls(t, "GetProduct", 3)

	// Check error log exists
	found := false
	for _, msg := range logMessages {
		if containsSubstring(msg, "error") && containsSubstring(msg, "operation failed after all retries") &&
			containsSubstring(msg, "GetProduct") && containsSubstring(msg, "attempts=3") && containsSubstring(msg, transientErr.Error()) {
			found = true
			break
		}
	}
	assert.True(t, found, "Expected error log entry for failed retries not found")
}

// Helper function to check substring existence
func containsSubstring(s, substr string) bool {
	return len(s) >= len(substr) && (s[:len(substr)] == substr || containsSubstring(s[1:], substr))
}
