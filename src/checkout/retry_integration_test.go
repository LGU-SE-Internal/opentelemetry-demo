package main

import (
	"context"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Mock gRPC invoker for testing
type mockInvoker struct {
	mock.Mock
}

func (m *mockInvoker) Invoke(ctx context.Context, method string, req, reply interface{}, cc *grpc.ClientConn, opts ...grpc.CallOption) error {
	args := m.Called(ctx, method, req, reply, cc, opts)
	return args.Error(0)
}

// TestAC1_AllDownstreamUnaryCallsWrappedWithRetry verifies that all unary gRPC calls to the 4 target services have retry logic
func TestAC1_AllDownstreamUnaryCallsWrappedWithRetry(t *testing.T) {
	interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable, codes.ResourceExhausted, codes.Aborted})
	
	invoker := new(mockInvoker)
	invoker.On("Invoke", mock.Anything, mock.MatchedBy(func(method string) bool {
		// Match any of the 4 target services method paths
		return method == "/oteldemo.PaymentService/Charge" ||
			method == "/oteldemo.ShippingService/ShipOrder" ||
			method == "/oteldemo.ProductCatalogService/GetProduct" ||
			method == "/oteldemo.CurrencyService/Convert"
	}), mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(status.Error(codes.Unavailable, "transient error")).Times(3)

	err := interceptor(context.Background(), "/oteldemo.PaymentService/Charge", nil, nil, nil, invoker.Invoke)
	assert.Error(t, err)
	invoker.AssertExpectations(t)
}

// TestAC2_OnlyRetriableCodesTriggerRetry verifies retries only happen for UNAVAILABLE, RESOURCE_EXHAUSTED, ABORTED status codes
func TestAC2_OnlyRetriableCodesTriggerRetry(t *testing.T) {
	testCases := []struct {
		name       string
		code       codes.Code
		shouldRetry bool
	}{
		{"UNAVAILABLE is retriable", codes.Unavailable, true},
		{"RESOURCE_EXHAUSTED is retriable", codes.ResourceExhausted, true},
		{"ABORTED is retriable", codes.Aborted, true},
		{"NOT_FOUND is not retriable", codes.NotFound, false},
		{"INVALID_ARGUMENT is not retriable", codes.InvalidArgument, false},
		{"PERMISSION_DENIED is not retriable", codes.PermissionDenied, false},
		{"INTERNAL is not retriable", codes.Internal, false},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable, codes.ResourceExhausted, codes.Aborted})
			invoker := new(mockInvoker)
			expectedCalls := 1
			if tc.shouldRetry {
				expectedCalls = 3
			}
			invoker.On("Invoke", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(status.Error(tc.code, "test error")).Times(expectedCalls)

			err := interceptor(context.Background(), "/oteldemo.PaymentService/Charge", nil, nil, nil, invoker.Invoke)
			assert.Error(t, err)
			invoker.AssertExpectations(t)
		})
	}
}

// TestAC3_MaxTwoRetriesThreeTotalAttempts verifies maximum 2 retries (total 3 attempts) before failing
func TestAC3_MaxTwoRetriesThreeTotalAttempts(t *testing.T) {
	interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable})
	invoker := new(mockInvoker)
	invoker.On("Invoke", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(status.Error(codes.Unavailable, "transient error")).Times(3)

	err := interceptor(context.Background(), "/oteldemo.PaymentService/Charge", nil, nil, nil, invoker.Invoke)
	assert.Error(t, err)
	assert.Equal(t, codes.Unavailable, status.Code(err))
	invoker.AssertExpectations(t)
}

// TestAC4_ExponentialBackoffUsed verifies exponential backoff with 100ms initial delay, doubling each attempt, capped at 1s
func TestAC4_ExponentialBackoffUsed(t *testing.T) {
	interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable})
	invoker := new(mockInvoker)
	
	var attemptTimes []time.Time
	invoker.On("Invoke", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
		attemptTimes = append(attemptTimes, time.Now())
	}).Return(status.Error(codes.Unavailable, "transient error")).Times(3)

	start := time.Now()
	err := interceptor(context.Background(), "/oteldemo.PaymentService/Charge", nil, nil, nil, invoker.Invoke)
	assert.Error(t, err)

	// Verify delays between attempts: ~100ms between 1st and 2nd, ~200ms between 2nd and 3rd
	delay1 := attemptTimes[1].Sub(attemptTimes[0])
	delay2 := attemptTimes[2].Sub(attemptTimes[1])

	assert.GreaterOrEqual(t, delay1, 80*time.Millisecond) // Allow 20ms tolerance
	assert.LessOrEqual(t, delay1, 120*time.Millisecond)

	assert.GreaterOrEqual(t, delay2, 180*time.Millisecond)
	assert.LessOrEqual(t, delay2, 220*time.Millisecond)

	// Total time should be ~300ms + overhead
	totalTime := time.Since(start)
	assert.GreaterOrEqual(t, totalTime, 280*time.Millisecond)
	assert.LessOrEqual(t, totalTime, 400*time.Millisecond)
}

// TestAC5_IdempotencyKeyIncludedInWriteCalls verifies write calls include same idempotency key in metadata for all retries
func TestAC5_IdempotencyKeyIncludedInWriteCalls(t *testing.T) {
	interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable})
	invoker := new(mockInvoker)
	
	var idempotencyKeys []string
	invoker.On("Invoke", mock.MatchedBy(func(ctx context.Context) bool {
		md, ok := metadata.FromOutgoingContext(ctx)
		if !ok {
			return false
		}
		keys := md.Get("idempotency-key")
		if len(keys) == 0 {
			return false
		}
		idempotencyKeys = append(idempotencyKeys, keys[0])
		return true
	}), mock.MatchedBy(func(method string) bool {
		return method == "/oteldemo.PaymentService/Charge" || method == "/oteldemo.ShippingService/ShipOrder"
	}), mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(status.Error(codes.Unavailable, "transient error")).Times(3)

	// Generate idempotency key and attach to context
	key := GenerateIdempotencyKey()
	ctx := metadata.AppendToOutgoingContext(context.Background(), "idempotency-key", key)

	err := interceptor(ctx, "/oteldemo.PaymentService/Charge", nil, nil, nil, invoker.Invoke)
	assert.Error(t, err)
	
	// Verify all attempts had the same idempotency key
	assert.Len(t, idempotencyKeys, 3)
	for _, k := range idempotencyKeys {
		assert.Equal(t, key, k)
	}
}

// TestAC6_RetryAttemptsMetricIncremented verifies retry attempts counter is incremented per service and status code
func TestAC6_RetryAttemptsMetricIncremented(t *testing.T) {
	reg := prometheus.NewRegistry()
	metrics := NewRetryMetrics(reg)
	// Note: Assuming interceptor accepts metrics as a parameter (or is wired with it globally)
	// Adjust if implementation uses different wiring
	interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable}, metrics)
	invoker := new(mockInvoker)
	invoker.On("Invoke", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(status.Error(codes.Unavailable, "transient error")).Times(3)

	err := interceptor(context.Background(), "/oteldemo.PaymentService/Charge", nil, nil, nil, invoker.Invoke)
	assert.Error(t, err)

	// Check that attempts counter has 2 entries (retries: 2 attempts after initial)
	attemptCount := testutil.ToFloat64(metrics.Attempts.WithLabelValues("PaymentService", "UNAVAILABLE"))
	assert.Equal(t, float64(2), attemptCount)
}

// TestAC7_RetryFailuresMetricIncremented verifies failures counter is incremented when all retries are exhausted
func TestAC7_RetryFailuresMetricIncremented(t *testing.T) {
	reg := prometheus.NewRegistry()
	metrics := NewRetryMetrics(reg)
	interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable}, metrics)
	invoker := new(mockInvoker)
	invoker.On("Invoke", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(status.Error(codes.Unavailable, "transient error")).Times(3)

	err := interceptor(context.Background(), "/oteldemo.ShippingService/ShipOrder", nil, nil, nil, invoker.Invoke)
	assert.Error(t, err)

	// Check that failures counter has 1 entry
	failureCount := testutil.ToFloat64(metrics.Failures.WithLabelValues("ShippingService", "UNAVAILABLE"))
	assert.Equal(t, float64(1), failureCount)
}

// TestAC8_NonRetriableCodesReturnImmediately verifies non-retriable codes return immediately with no retries
func TestAC8_NonRetriableCodesReturnImmediately(t *testing.T) {
	interceptor := NewRetryInterceptor(2, 100*time.Millisecond, []codes.Code{codes.Unavailable, codes.ResourceExhausted, codes.Aborted})
	invoker := new(mockInvoker)
	invoker.On("Invoke", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(status.Error(codes.NotFound, "not found")).Times(1)

	start := time.Now()
	err := interceptor(context.Background(), "/oteldemo.ProductCatalogService/GetProduct", nil, nil, nil, invoker.Invoke)
	assert.Error(t, err)
	assert.Equal(t, codes.NotFound, status.Code(err))
	
	// No delay expected
	assert.Less(t, time.Since(start), 50*time.Millisecond)
	invoker.AssertExpectations(t)
}
