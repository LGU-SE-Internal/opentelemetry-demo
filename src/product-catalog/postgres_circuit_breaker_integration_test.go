package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/sony/gobreaker"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/metric/embedded"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// MockMeter is a test mock for OpenTelemetry Meter to capture metric increments
type MockMeter struct {
	embedded.Meter
	stateTransitions map[string]int
	operations       map[string]map[string]int
}

func NewMockMeter() *MockMeter {
	return &MockMeter{
		stateTransitions: make(map[string]int),
		operations: map[string]map[string]int{
			"closed":   {"success": 0, "failure": 0},
			"open":     {"success": 0, "failure": 0},
			"half-open": {"success": 0, "failure": 0},
		},
	}
}

func (m *MockMeter) Int64Counter(name string, options ...metric.Int64CounterOption) (metric.Int64Counter, error) {
	if name == "productcatalog.circuit_breaker.state_transitions_total" {
		return &mockStateTransitionCounter{m: m}, nil
	}
	if name == "productcatalog.circuit_breaker.operations_total" {
		return &mockOperationsCounter{m: m}, nil
	}
	return nil, nil
}

type mockStateTransitionCounter struct {
	embedded.Int64Counter
	m *MockMeter
}

func (c *mockStateTransitionCounter) Add(ctx context.Context, incr int64, options ...metric.AddOption) {
	cfg := metric.NewAddConfig(options)
	attrs := cfg.Attributes()
	for _, attr := range attrs {
		if string(attr.Key) == "state" {
			state := attr.Value.AsString()
			c.m.stateTransitions[state] += int(incr)
		}
	}
}

type mockOperationsCounter struct {
	embedded.Int64Counter
	m *MockMeter
}

func (c *mockOperationsCounter) Add(ctx context.Context, incr int64, options ...metric.AddOption) {
	cfg := metric.NewAddConfig(options)
	attrs := cfg.Attributes()
	var status, state string
	for _, attr := range attrs {
		if string(attr.Key) == "status" {
			status = attr.Value.AsString()
		}
		if string(attr.Key) == "state" {
			state = attr.Value.AsString()
		}
	}
	if status != "" && state != "" {
		c.m.operations[state][status] += int(incr)
	}
}

// Test AC-1: Circuit transitions to open when failure threshold hit in interval
func Test_ac1_circuit_opens_on_failure_threshold(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Setup mock DB that always fails
	mockDB := &mockSQLDB{alwaysFail: true}
	mockMeter := NewMockMeter()

	// Initialize CB with low threshold for test
	os.Setenv("PRODUCT_CATALOG_CB_FAILURE_THRESHOLD", "0.5")
	os.Setenv("PRODUCT_CATALOG_CB_INTERVAL", "10s")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_FAILURE_THRESHOLD")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_INTERVAL")

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Make 5 failed requests (50% failure rate threshold hit)
	for i := 0; i < 5; i++ {
		_, err := cb.GetProduct(ctx, "test-id")
		assert.Error(t, err)
	}

	// Verify circuit is open
	assert.Equal(t, gobreaker.StateOpen, cb.cb.State())
	// Verify open state transition metric incremented
	assert.Equal(t, 1, mockMeter.stateTransitions["open"])
}

// Test AC-2: Circuit transitions to half-open after timeout
func Test_ac2_circuit_goes_half_open_after_timeout(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	mockDB := &mockSQLDB{alwaysFail: true}
	mockMeter := NewMockMeter()

	// Set short timeout for test
	os.Setenv("PRODUCT_CATALOG_CB_TIMEOUT", "1s")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_TIMEOUT")

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Trip circuit open first
	for i := 0; i < 5; i++ {
		_, err := cb.GetProduct(ctx, "test-id")
		assert.Error(t, err)
	}
	require.Equal(t, gobreaker.StateOpen, cb.cb.State())

	// Wait for timeout
	time.Sleep(1100 * time.Millisecond)

	// Verify circuit is half-open when next request comes in
	// Note: gobreaker lazy transitions to half-open on first request after timeout
	_, err = cb.GetProduct(ctx, "test-id")
	assert.Error(t, err)
	assert.Equal(t, gobreaker.StateHalfOpen, cb.cb.State())
	// Verify half-open state transition metric incremented
	assert.Equal(t, 1, mockMeter.stateTransitions["half-open"])
}

// Test AC-3: Half-open transitions to closed if all test requests succeed
func Test_ac3_half_open_closes_on_successful_test_requests(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	mockDB := &mockSQLDB{alwaysFail: false}
	mockMeter := NewMockMeter()

	os.Setenv("PRODUCT_CATALOG_CB_TIMEOUT", "1s")
	os.Setenv("PRODUCT_CATALOG_CB_HALF_OPEN_MAX_REQUESTS", "1")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_TIMEOUT")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_HALF_OPEN_MAX_REQUESTS")

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Trip circuit first (force fail temporarily)
	mockDB.alwaysFail = true
	for i := 0; i < 5; i++ {
		_, err := cb.GetProduct(ctx, "test-id")
		assert.Error(t, err)
	}
	require.Equal(t, gobreaker.StateOpen, cb.cb.State())
	mockDB.alwaysFail = false

	// Wait for timeout
	time.Sleep(1100 * time.Millisecond)

	// Make successful test request
	_, err = cb.GetProduct(ctx, "test-id")
	assert.NoError(t, err)

	// Verify circuit is closed
	assert.Equal(t, gobreaker.StateClosed, cb.cb.State())
	// Verify closed state transition metric incremented
	assert.Equal(t, 1, mockMeter.stateTransitions["closed"])
}

// Test AC-4: Half-open transitions back to open on failed test request
func Test_ac4_half_open_opens_on_failed_test_request(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	mockDB := &mockSQLDB{alwaysFail: true}
	mockMeter := NewMockMeter()

	os.Setenv("PRODUCT_CATALOG_CB_TIMEOUT", "1s")
	os.Setenv("PRODUCT_CATALOG_CB_HALF_OPEN_MAX_REQUESTS", "1")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_TIMEOUT")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_HALF_OPEN_MAX_REQUESTS")

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Trip circuit open
	for i := 0; i < 5; i++ {
		_, err := cb.GetProduct(ctx, "test-id")
		assert.Error(t, err)
	}
	require.Equal(t, gobreaker.StateOpen, cb.cb.State())

	// Wait for timeout
	time.Sleep(1100 * time.Millisecond)

	// Make failed test request
	_, err = cb.GetProduct(ctx, "test-id")
	assert.Error(t, err)

	// Verify circuit is back to open
	assert.Equal(t, gobreaker.StateOpen, cb.cb.State())
	// Verify open state transition incremented again
	assert.Equal(t, 2, mockMeter.stateTransitions["open"])
}

// Test AC-5: Open circuit returns Unavailable error immediately, no DB calls
func Test_ac5_open_circuit_returns_unavailable(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	mockDB := &mockSQLDB{alwaysFail: true}
	mockMeter := NewMockMeter()

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Trip circuit open
	for i := 0; i < 5; i++ {
		_, err := cb.GetProduct(ctx, "test-id")
		assert.Error(t, err)
	}
	require.Equal(t, gobreaker.StateOpen, cb.cb.State())

	// Reset DB call count
	mockDB.callCount = 0

	// Make request while open
	_, err = cb.GetProduct(ctx, "test-id")
	assert.Error(t, err)
	assert.Equal(t, codes.Unavailable, status.Code(err))
	// Verify no DB call was made
	assert.Equal(t, 0, mockDB.callCount)
	// Verify operation counted as failure in open state
	assert.Equal(t, 1, mockMeter.operations["open"]["failure"])
}

// Test AC-6: Custom env vars override defaults
func Test_ac6_custom_env_vars_override_defaults(t *testing.T) {
	t.Parallel()

	// Set custom values
	os.Setenv("PRODUCT_CATALOG_CB_FAILURE_THRESHOLD", "0.75")
	os.Setenv("PRODUCT_CATALOG_CB_INTERVAL", "30s")
	os.Setenv("PRODUCT_CATALOG_CB_TIMEOUT", "60s")
	os.Setenv("PRODUCT_CATALOG_CB_HALF_OPEN_MAX_REQUESTS", "3")
	defer func() {
		os.Unsetenv("PRODUCT_CATALOG_CB_FAILURE_THRESHOLD")
		os.Unsetenv("PRODUCT_CATALOG_CB_INTERVAL")
		os.Unsetenv("PRODUCT_CATALOG_CB_TIMEOUT")
		os.Unsetenv("PRODUCT_CATALOG_CB_HALF_OPEN_MAX_REQUESTS")
	}()

	mockDB := &mockSQLDB{}
	mockMeter := NewMockMeter()

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Verify settings match custom values
	settings := cb.cb.Settings()
	assert.Equal(t, 0.75, settings.FailureRatio)
	assert.Equal(t, 30*time.Second, settings.Interval)
	assert.Equal(t, 60*time.Second, settings.Timeout)
	assert.Equal(t, uint32(3), settings.MaxRequests)
}

// Test AC-7: State transition metrics emitted correctly
func Test_ac7_state_transition_metrics(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	mockDB := &mockSQLDB{}
	mockMeter := NewMockMeter()
	os.Setenv("PRODUCT_CATALOG_CB_TIMEOUT", "1s")
	defer os.Unsetenv("PRODUCT_CATALOG_CB_TIMEOUT")

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Transition 1: closed -> open
	mockDB.alwaysFail = true
	for i := 0; i < 5; i++ {
		_, err := cb.GetProduct(ctx, "test-id")
		assert.Error(t, err)
	}
	assert.Equal(t, 1, mockMeter.stateTransitions["open"])

	// Transition 2: open -> half-open
	mockDB.alwaysFail = false
	time.Sleep(1100 * time.Millisecond)
	_, err = cb.GetProduct(ctx, "test-id")
	assert.NoError(t, err)
	assert.Equal(t, 1, mockMeter.stateTransitions["half-open"])

	// Transition 3: half-open -> closed
	assert.Equal(t, 1, mockMeter.stateTransitions["closed"])
}

// Test AC-8: Operation metrics emitted with correct attributes
func Test_ac8_operation_metrics(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	mockDB := &mockSQLDB{}
	mockMeter := NewMockMeter()

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Successful operation in closed state
	mockDB.alwaysFail = false
	_, err = cb.GetProduct(ctx, "test-id")
	assert.NoError(t, err)
	assert.Equal(t, 1, mockMeter.operations["closed"]["success"])

	// Failed operation in closed state
	mockDB.alwaysFail = true
	_, err = cb.GetProduct(ctx, "test-id")
	assert.Error(t, err)
	assert.Equal(t, 1, mockMeter.operations["closed"]["failure"])
}

// Test AC-9: Retries work for transient errors when circuit closed, each counted as separate operation
func Test_ac9_transient_error_retries_counted_separately(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Mock DB that fails first 2 times (transient errors) then succeeds
	mockDB := &mockSQLDB{failNTimes: 2}
	mockMeter := NewMockMeter()

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Execute operation that will retry 2 times then succeed
	_, err = cb.GetProduct(ctx, "test-id")
	assert.NoError(t, err)

	// Verify 3 operations total (2 failed, 1 succeeded)
	assert.Equal(t, 2, mockMeter.operations["closed"]["failure"])
	assert.Equal(t, 1, mockMeter.operations["closed"]["success"])
}

// Test AC-10: No retries for non-transient errors, counted as failure
func Test_ac10_non_transient_errors_not_retried(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Mock DB that returns non-transient error
	mockDB := &mockSQLDB{returnNonTransientError: true}
	mockMeter := NewMockMeter()

	cb, err := NewDBCircuitBreaker(mockDB, mockMeter)
	require.NoError(t, err)

	// Execute operation
	_, err = cb.GetProduct(ctx, "test-id")
	assert.Error(t, err)

	// Verify only 1 operation counted (no retries)
	assert.Equal(t, 1, mockMeter.operations["closed"]["failure"])
	assert.Equal(t, 0, mockMeter.operations["closed"]["success"])
	assert.Equal(t, 1, mockDB.callCount)
}

// mockSQLDB is a mock implementation of sql.DB for testing
type mockSQLDB struct {
	alwaysFail             bool
	failNTimes             int
	callCount              int
	returnNonTransientError bool
}

// We don't need to implement all methods, just enough to test circuit breaker logic
// These are dummy implementations that will be replaced when real implementation exists
func (m *mockSQLDB) QueryContext(ctx context.Context, query string, args ...interface{}) (*sqlRows, error) {
	m.callCount++
	if m.returnNonTransientError {
		return nil, nonTransientErrorMock{}
	}
	if m.alwaysFail || (m.failNTimes > 0 && m.callCount <= m.failNTimes) {
		return nil, transientErrorMock{}
	}
	return nil, nil
}

func (m *mockSQLDB) QueryRowContext(ctx context.Context, query string, args ...interface{}) *sqlRow {
	m.callCount++
	return nil
}

// transientErrorMock simulates a retryable Postgres error
type transientErrorMock struct{}

func (e transientErrorMock) Error() string { return "transient error" }
func (e transientErrorMock) Temporary() bool { return true }

// nonTransientErrorMock simulates a non-retryable Postgres error
type nonTransientErrorMock struct{}

func (e nonTransientErrorMock) Error() string { return "non-transient error" }
func (e nonTransientErrorMock) Temporary() bool { return false }

// dummy type declarations to make test compile before implementation exists
type sqlRows struct{}
type sqlRow struct{}
