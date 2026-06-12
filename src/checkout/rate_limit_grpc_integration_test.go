package main

import (
	"context"
	"net"
	"os"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
)

const (
	bufSize = 1024 * 1024
	testIP1 = "192.168.1.100"
	testIP2 = "192.168.1.101"
	testEndpoint = "/oteldemo.CheckoutService/PlaceOrder"
)

// Test_AC1_ExceedRPMReturnsResourceExhausted verifies that when rate limit is enabled,
// requests exceeding RPM limit return RESOURCE_EXHAUSTED status code, and reset after window
func Test_AC1_ExceedRPMReturnsResourceExhausted(t *testing.T) {
	// Setup env for test
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM", "2")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM")
		resetRateLimitMetrics()
	}()

	// Setup test gRPC server with rate limit interceptor
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(
		grpc.UnaryInterceptor(RateLimitUnaryInterceptor()),
	)
	RegisterCheckoutServiceServer(s, &mockCheckoutServer{})
	go func() {
		if err := s.Serve(lis); err != nil {
			t.Logf("Server exited with error: %v", err)
		}
	}()
	defer s.Stop()

	// Create client connection
	ctx := context.Background()
	conn, err := grpc.DialContext(ctx, "bufnet", grpc.WithContextDialer(func(ctx context.Context, address string) (net.Conn, error) {
		return lis.Dial()
	}), grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err)
	defer conn.Close()
	client := NewCheckoutServiceClient(conn)

	// Add X-Forwarded-For header to context for client IP
	ctx = metadata.NewOutgoingContext(ctx, metadata.Pairs("x-forwarded-for", testIP1))

	// First two requests should pass
	for i := 0; i < 2; i++ {
		resp, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
		assert.NoError(t, err, "Request %d should pass", i+1)
		assert.NotNil(t, resp)
	}

	// Third request should be blocked
	resp, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
	assert.Error(t, err, "Third request should be blocked")
	assert.Equal(t, codes.ResourceExhausted, status.Code(err), "Should return RESOURCE_EXHAUSTED status code")
	assert.Nil(t, resp)

	// Wait for rate limit window to reset (simulate 60s pass)
	time.Sleep(1 * time.Second) // TODO: Replace with mock time once implementation is available

	// Request should pass again after window reset
	resp, err = client.PlaceOrder(ctx, &PlaceOrderRequest{})
	assert.NoError(t, err, "Request should pass after window reset")
	assert.NotNil(t, resp)
}

// Test_AC2_RateLimitDisabledAllowsAllRequests verifies that when rate limit is disabled,
// no requests are blocked regardless of frequency
func Test_AC2_RateLimitDisabledAllowsAllRequests(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED", "false")
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM", "2")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM")
		resetRateLimitMetrics()
	}()

	// Setup test server
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(
		grpc.UnaryInterceptor(RateLimitUnaryInterceptor()),
	)
	RegisterCheckoutServiceServer(s, &mockCheckoutServer{})
	go func() { s.Serve(lis) }()
	defer s.Stop()

	// Create client
	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-forwarded-for", testIP1))
	conn, _ := grpc.DialContext(ctx, "bufnet", grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) { return lis.Dial() }), grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer conn.Close()
	client := NewCheckoutServiceClient(conn)

	// Send 10 requests, all should pass
	for i := 0; i < 10; i++ {
		resp, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
		assert.NoError(t, err, "Request %d should pass when rate limit is disabled", i+1)
		assert.NotNil(t, resp)
	}
}

// Test_AC3_CustomRPMValueApplies verifies that custom RPM value from env is applied correctly
func Test_AC3_CustomRPMValueApplies(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM", "5")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM")
		resetRateLimitMetrics()
	}()

	// Setup test server
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(grpc.UnaryInterceptor(RateLimitUnaryInterceptor()))
	RegisterCheckoutServiceServer(s, &mockCheckoutServer{})
	go func() { s.Serve(lis) }()
	defer s.Stop()

	// Client setup
	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-forwarded-for", testIP1))
	conn, _ := grpc.DialContext(ctx, "bufnet", grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) { return lis.Dial() }), grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer conn.Close()
	client := NewCheckoutServiceClient(conn)

	// First 5 requests pass
	for i := 0; i < 5; i++ {
		resp, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
		assert.NoError(t, err, "Request %d should pass with custom RPM=5", i+1)
		assert.NotNil(t, resp)
	}

	// 6th request blocked
	resp, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
	assert.Error(t, err)
	assert.Equal(t, codes.ResourceExhausted, status.Code(err))
	assert.Nil(t, resp)
}

// Test_AC4_ExceededResponseContainsClientIP verifies that error message contains client IP
func Test_AC4_ExceededResponseContainsClientIP(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM", "1")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM")
		resetRateLimitMetrics()
	}()

	// Setup server and client
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(grpc.UnaryInterceptor(RateLimitUnaryInterceptor()))
	RegisterCheckoutServiceServer(s, &mockCheckoutServer{})
	go func() { s.Serve(lis) }()
	defer s.Stop()

	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-forwarded-for", testIP1))
	conn, _ := grpc.DialContext(ctx, "bufnet", grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) { return lis.Dial() }), grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer conn.Close()
	client := NewCheckoutServiceClient(conn)

	// Consume allowed request
	client.PlaceOrder(ctx, &PlaceOrderRequest{})

	// Get blocked response
	_, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
	require.Error(t, err)
	st, ok := status.FromError(err)
	require.True(t, ok)
	assert.Contains(t, st.Message(), testIP1, "Error message should contain client IP address")
	assert.Contains(t, st.Message(), "Rate limit exceeded", "Error message should contain rate limit exceeded text")
}

// Test_AC5_ExceededCounterIncrements verifies that rate_limit_exceeded_total counter increments correctly
func Test_AC5_ExceededCounterIncrements(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM", "1")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM")
		resetRateLimitMetrics()
	}()

	// Setup server and client
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(grpc.UnaryInterceptor(RateLimitUnaryInterceptor()))
	RegisterCheckoutServiceServer(s, &mockCheckoutServer{})
	go func() { s.Serve(lis) }()
	defer s.Stop()

	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-forwarded-for", testIP1))
	conn, _ := grpc.DialContext(ctx, "bufnet", grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) { return lis.Dial() }), grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer conn.Close()
	client := NewCheckoutServiceClient(conn)

	// Initial count should be 0
	initialCount := testutil.ToFloat64(checkoutServiceRateLimitExceededTotal.WithLabelValues(testIP1, testEndpoint))
	assert.Equal(t, 0.0, initialCount)

	// Consume allowed request (should not increment exceeded counter)
	client.PlaceOrder(ctx, &PlaceOrderRequest{})
	countAfterAllowed := testutil.ToFloat64(checkoutServiceRateLimitExceededTotal.WithLabelValues(testIP1, testEndpoint))
	assert.Equal(t, 0.0, countAfterAllowed)

	// Blocked request should increment counter by 1
	_, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
	require.Error(t, err)
	countAfterBlocked := testutil.ToFloat64(checkoutServiceRateLimitExceededTotal.WithLabelValues(testIP1, testEndpoint))
	assert.Equal(t, 1.0, countAfterBlocked)
}

// Test_AC6_AllowedCounterIncrements verifies that rate_limit_allowed_total counter increments correctly
func Test_AC6_AllowedCounterIncrements(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM", "2")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM")
		resetRateLimitMetrics()
	}()

	// Setup server and client
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(grpc.UnaryInterceptor(RateLimitUnaryInterceptor()))
	RegisterCheckoutServiceServer(s, &mockCheckoutServer{})
	go func() { s.Serve(lis) }()
	defer s.Stop()

	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-forwarded-for", testIP1))
	conn, _ := grpc.DialContext(ctx, "bufnet", grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) { return lis.Dial() }), grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer conn.Close()
	client := NewCheckoutServiceClient(conn)

	// Initial count 0
	initialCount := testutil.ToFloat64(checkoutServiceRateLimitAllowedTotal.WithLabelValues(testIP1, testEndpoint))
	assert.Equal(t, 0.0, initialCount)

	// First allowed request increments by 1
	client.PlaceOrder(ctx, &PlaceOrderRequest{})
	count1 := testutil.ToFloat64(checkoutServiceRateLimitAllowedTotal.WithLabelValues(testIP1, testEndpoint))
	assert.Equal(t, 1.0, count1)

	// Second allowed request increments by 1
	client.PlaceOrder(ctx, &PlaceOrderRequest{})
	count2 := testutil.ToFloat64(checkoutServiceRateLimitAllowedTotal.WithLabelValues(testIP1, testEndpoint))
	assert.Equal(t, 2.0, count2)

	// Blocked request does not increment allowed counter
	_, err := client.PlaceOrder(ctx, &PlaceOrderRequest{})
	require.Error(t, err)
	count3 := testutil.ToFloat64(checkoutServiceRateLimitAllowedTotal.WithLabelValues(testIP1, testEndpoint))
	assert.Equal(t, 2.0, count3)
}

// Test_AC7_IPExtractedFromXForwardedForFallsBackToRemoteIP verifies IP extraction logic
func Test_AC7_IPExtractedFromXForwardedForFallsBackToRemoteIP(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM", "1")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM")
		resetRateLimitMetrics()
	}()

	// Setup server
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(grpc.UnaryInterceptor(RateLimitUnaryInterceptor()))
	RegisterCheckoutServiceServer(s, &mockCheckoutServer{})
	go func() { s.Serve(lis) }()
	defer s.Stop()

	// First test: X-Forwarded-For present
	ctx1 := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-forwarded-for", testIP1))
	conn1, _ := grpc.DialContext(ctx1, "bufnet", grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) { return lis.Dial() }), grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer conn1.Close()
	client1 := NewCheckoutServiceClient(conn1)

	// Consume request for IP1
	client1.PlaceOrder(ctx1, &PlaceOrderRequest{})
	// Second request from same IP blocked
	_, err := client1.PlaceOrder(ctx1, &PlaceOrderRequest{})
	assert.Error(t, err)
	assert.Equal(t, codes.ResourceExhausted, status.Code(err))

	// Second test: No X-Forwarded-For, use remote IP (bufconn uses pipe address, simulate different client)
	// Context without X-Forwarded-For header
	ctx2 := context.Background()
	conn2, _ := grpc.DialContext(ctx2, "bufnet", grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) { return lis.Dial() }), grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer conn2.Close()
	client2 := NewCheckoutServiceClient(conn2)

	// Request from different IP (remote addr) should pass
	resp, err := client2.PlaceOrder(ctx2, &PlaceOrderRequest{})
	assert.NoError(t, err, "Request from different remote IP should pass")
	assert.NotNil(t, resp)
}

// mockCheckoutServer is a minimal implementation of CheckoutServiceServer for testing
type mockCheckoutServer struct {
	UnimplementedCheckoutServiceServer
}

func (m *mockCheckoutServer) PlaceOrder(ctx context.Context, req *PlaceOrderRequest) (*PlaceOrderResponse, error) {
	return &PlaceOrderResponse{}, nil
}

// resetRateLimitMetrics resets the Prometheus counters between tests
func resetRateLimitMetrics() {
	checkoutServiceRateLimitExceededTotal.Reset()
	checkoutServiceRateLimitAllowedTotal.Reset()
}
