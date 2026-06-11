package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/sony/gobreaker"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/stretchr/testify/assert"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Mock invokers for testing
func cbMockInvoker(_ context.Context, _ string, _, _ interface{}, _ *grpc.ClientConn, _ ...grpc.CallOption) error {
	return nil
}

func cbMockInvokerAlwaysFails(_ context.Context, _ string, _, _ interface{}, _ *grpc.ClientConn, _ ...grpc.CallOption) error {
	return status.Error(codes.Unavailable, "service down")
}

func TestAC1_CircuitOpensAfter50PercentFailureIn10sWindow(t *testing.T) {
	t.Parallel()
	cfg := CircuitBreakerConfig{
		ServiceName:             "test-payment",
		FailureThresholdPercent: 50,
		OpenStateTimeout:        30 * time.Second,
		HalfOpenMaxRequests:     5,
		RollingWindowDuration:   10 * time.Second,
	}
	cb := NewCircuitBreaker(cfg)
	interceptor := CircuitBreakerClientInterceptor(cb)
	ctx := context.Background()

	// First request fails
	err := interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvokerAlwaysFails)
	assert.Error(t, err)

	// Second request succeeds
	err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
	assert.NoError(t, err)

	// Third request fails: failure rate 2/3 = 66.6% > 50% should trip
	err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvokerAlwaysFails)
	assert.Error(t, err)

	// Fourth request should be rejected with ErrCircuitOpen
	err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
	assert.Equal(t, ErrCircuitOpen, err)
	assert.Equal(t, gobreaker.StateOpen, cb.State())
}

func TestAC2_HalfOpenStateTransition(t *testing.T) {
	t.Parallel()
	cfg := CircuitBreakerConfig{
		ServiceName:            "test-shipping",
		FailureThresholdPercent: 50,
		OpenStateTimeout:       1 * time.Second,
		HalfOpenMaxRequests:    5,
		RollingWindowDuration:  10 * time.Second,
	}
	cb := gobreaker.NewCircuitBreaker(gobreaker.Settings{
		Name:        cfg.ServiceName,
		MaxRequests: cfg.HalfOpenMaxRequests,
		Interval:    cfg.RollingWindowDuration,
		Timeout:     cfg.OpenStateTimeout,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			failureRatio := float64(counts.TotalFailures) / float64(counts.Requests)
			return counts.Requests >= 2 && failureRatio >= float64(cfg.FailureThresholdPercent)/100
		},
	})
	interceptor := CircuitBreakerClientInterceptor(cb)
	ctx := context.Background()

	// Trip circuit first
	_ = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
	_ = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvokerAlwaysFails)
	assert.Equal(t, gobreaker.StateOpen, cb.State())

	// Wait for open timeout
	time.Sleep(1100 * time.Millisecond)

	// Test 1: >=80% success -> close
	successCount := 4
	failureCount := 1
	allowed := 0
	for i := 0; i < 10; i++ {
		var err error
		if i < successCount {
			err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
		} else if i < successCount + failureCount {
			err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvokerAlwaysFails)
		} else {
			err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
			if err == ErrCircuitOpen { break }
		}
		if err != ErrCircuitOpen { allowed++ }
	}
	assert.Equal(t, 5, allowed)
	assert.Equal(t, gobreaker.StateClosed, cb.State())

	// Re-trip and test 2: <80% success -> reopen
	_ = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
	_ = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvokerAlwaysFails)
	assert.Equal(t, gobreaker.StateOpen, cb.State())
	time.Sleep(1100 * time.Millisecond)

	successCount = 3
	failureCount = 2
	allowed = 0
	for i := 0; i < 10; i++ {
		var err error
		if i < successCount {
			err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
		} else if i < successCount + failureCount {
			err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvokerAlwaysFails)
		} else {
			err = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
			if err == ErrCircuitOpen { break }
		}
		if err != ErrCircuitOpen { allowed++ }
	}
	assert.Equal(t, 5, allowed)
	assert.Equal(t, gobreaker.StateOpen, cb.State())
}

func TestAC3_StateTransitionLogs(t *testing.T) {
	t.Parallel()
	var logOutput strings.Builder
	// Replace with test logger
	cfg := CircuitBreakerConfig{
		ServiceName:            "test-currency",
		FailureThresholdPercent: 50,
		OpenStateTimeout:       1 * time.Second,
		HalfOpenMaxRequests:    5,
		RollingWindowDuration:  10 * time.Second,
	}
	cb := gobreaker.NewCircuitBreaker(gobreaker.Settings{
		Name:        cfg.ServiceName,
		MaxRequests: cfg.HalfOpenMaxRequests,
		Interval:    cfg.RollingWindowDuration,
		Timeout:     cfg.OpenStateTimeout,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			failureRatio := float64(counts.TotalFailures) / float64(counts.Requests)
			return counts.Requests >= 2 && failureRatio >= float64(cfg.FailureThresholdPercent)/100
		},
		OnStateChange: func(name string, from gobreaker.State, to gobreaker.State) {
			// Log should be JSON with fields: service_name, previous_state, new_state, failure_count, timestamp
			fmt.Fprintf(&logOutput, `{"service_name":"%s","previous_state":"%s","new_state":"%s","failure_count":%d,"timestamp":"%s"}`,
				name, from.String(), to.String(), cb.Counts().TotalFailures, time.Now().Format(time.RFC3339))
		},
	})
	interceptor := CircuitBreakerClientInterceptor(cb)
	ctx := context.Background()

	// Trigger closed -> open
	_ = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
	_ = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvokerAlwaysFails)
	assert.Contains(t, logOutput.String(), `"service_name":"test-currency"`)
	assert.Contains(t, logOutput.String(), `"previous_state":"closed"`)
	assert.Contains(t, logOutput.String(), `"new_state":"open"`)
}

func TestAC4_PrometheusMetricsExposed(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())
	server := httptest.NewServer(mux)
	defer server.Close()

	cfg := CircuitBreakerConfig{
		ServiceName:            "test-productcatalog",
		FailureThresholdPercent: 50,
		OpenStateTimeout:       30 * time.Second,
		HalfOpenMaxRequests:    5,
		RollingWindowDuration:  10 * time.Second,
	}
	cb := gobreaker.NewCircuitBreaker(gobreaker.Settings{Name: cfg.ServiceName})
	interceptor := CircuitBreakerClientInterceptor(cb)
	ctx := context.Background()

	resp, err := http.Get(fmt.Sprintf("%s/metrics", server.URL))
	assert.NoError(t, err)
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	// Verify state gauge exists
	assert.Contains(t, string(body), `otelcheckout_circuit_breaker_state`)
	// Verify event counter exists
	assert.Contains(t, string(body), `otelcheckout_circuit_breaker_events_total`)
}

func TestAC5_AllGRPCClientsHaveCircuitBreakerInterceptor(t *testing.T) {
	t.Parallel()
	clients := GetAllGRPCClients()
	assert.GreaterOrEqual(t, len(clients), 4, "should have payment, shipping, currency, productcatalog clients")

	for svc, conn := range clients {
		hasInterceptor := false
		for _, i := range conn.GetUnaryClientInterceptors() {
			// Check if it's our circuit breaker interceptor
			if fmt.Sprintf("%T", i) == "func(*gobreaker.CircuitBreaker) grpc.UnaryClientInterceptor" {
				hasInterceptor = true
				break
			}
		}
		assert.True(t, hasInterceptor, "service %s missing circuit breaker interceptor", svc)
	}
}

func TestAC6_CircuitBreakerLatencyOverhead(t *testing.T) {
	t.Parallel()
	cfg := CircuitBreakerConfig{
		ServiceName:            "test-latency",
		FailureThresholdPercent: 50,
		OpenStateTimeout:       30 * time.Second,
		HalfOpenMaxRequests:    5,
		RollingWindowDuration:  10 * time.Second,
	}
	cb := gobreaker.NewCircuitBreaker(gobreaker.Settings{Name: cfg.ServiceName})
	interceptor := CircuitBreakerClientInterceptor(cb)
	ctx := context.Background()

	start := time.Now()
	for i := 0; i < 1000; i++ {
		_ = interceptor(ctx, "/test.Method", nil, nil, nil, cbMockInvoker)
	}
	avg := time.Since(start) / 1000
	assert.LessOrEqual(t, avg, 1*time.Millisecond, "average overhead per request should be <=1ms, got %v", avg)
}

func TestAC7_UnitTestCoverage(t *testing.T) {
	t.Run("CircuitOpensAfter10ConsecutiveFailures", func(t *testing.T) {
		t.Skip("implementation required")
	})
	t.Run("CircuitTransitionsToHalfOpenAfter30s", func(t *testing.T) {
		t.Skip("implementation required")
	})
	t.Run("CircuitClosesAfterSuccessfulTestRequests", func(t *testing.T) {
		t.Skip("implementation required")
	})
	t.Run("CircuitReopensAfterFailedTestRequestsInHalfOpen", func(t *testing.T) {
		t.Skip("implementation required")
	})
}
