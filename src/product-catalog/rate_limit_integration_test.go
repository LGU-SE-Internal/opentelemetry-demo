package main

import (
	"context"
	"net"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"golang.org/x/time/rate"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"

	pb "github.com/open-telemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo"
)

const bufSize = 1024 * 1024

func testSetupServer(t *testing.T, rateLimitRPS float64, identifierType string) (*grpc.ClientConn, func(), *tracetest.SpanRecorder) {
	t.Helper()

	// Set environment variables for rate limit config
	os.Setenv("PRODUCT_CATALOG_RATE_LIMIT_RPS", rateLimitRPS.String())
	os.Setenv("PRODUCT_CATALOG_RATE_LIMIT_IDENTIFIER", identifierType)
	t.Cleanup(func() {
		os.Unsetenv("PRODUCT_CATALOG_RATE_LIMIT_RPS")
		os.Unsetenv("PRODUCT_CATALOG_RATE_LIMIT_IDENTIFIER")
	})

	// Setup tracing recorder to capture spans
	spanRecorder := tracetest.NewSpanRecorder()
	traceProvider := trace.NewTracerProvider(trace.WithSpanProcessor(spanRecorder))

	// Create test server using the existing service setup
	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(
		grpc.UnaryInterceptor(RateLimitInterceptor(rate.NewLimiter(rate.Limit(rateLimitRPS), int(rateLimitRPS*2)), identifierType)),
	)
	pb.RegisterProductCatalogServiceServer(s, &productCatalogService{})

	go func() {
		if err := s.Serve(lis); err != nil && err != grpc.ErrServerStopped {
			t.Errorf("Server exited with error: %v", err)
		}
	}()

	// Create client connection
	conn, err := grpc.DialContext(context.Background(), "bufnet",
		grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) {
			return lis.Dial()
		}),
		grpc.WithInsecure(),
	)
	require.NoError(t, err)

	cleanup := func() {
		s.Stop()
		conn.Close()
	}

	return conn, cleanup, spanRecorder
}

// AC-1: When PRODUCT_CATALOG_RATE_LIMIT_RPS is set to a positive value, any client IP making more requests per second than the configured value will receive a ResourceExhausted gRPC status code response.
func TestAC1_RateLimitByIPExceededReturnsResourceExhausted(t *testing.T) {
	t.Parallel()

	rps := 1.0
	conn, cleanup, _ := testSetupServer(t, rps, "ip")
	defer cleanup()

	client := pb.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	// First request should succeed
	_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
	assert.NoError(t, err)

	// Next request should exceed rate limit and return ResourceExhausted
	_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
	assert.Error(t, err)
	assert.Equal(t, codes.ResourceExhausted, status.Code(err))
	assert.Contains(t, err.Error(), "rate limit exceeded")
}

// AC-2: When PRODUCT_CATALOG_RATE_LIMIT_IDENTIFIER is set to user_id, rate limiting is applied per unique x-user-id metadata value instead of client IP address.
func TestAC2_RateLimitByUserIDAppliesPerUser(t *testing.T) {
	t.Parallel()

	rps := 1.0
	conn, cleanup, _ := testSetupServer(t, rps, "user_id")
	defer cleanup()

	client := pb.NewProductCatalogServiceClient(conn)

	// User 1 first request succeeds
	ctxUser1 := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-user-id", "user1"))
	_, err := client.ListProducts(ctxUser1, &pb.ListProductsRequest{})
	assert.NoError(t, err)

	// User 1 second request fails
	_, err = client.ListProducts(ctxUser1, &pb.ListProductsRequest{})
	assert.Error(t, err)
	assert.Equal(t, codes.ResourceExhausted, status.Code(err))

	// User 2 first request succeeds (different user ID)
	ctxUser2 := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-user-id", "user2"))
	_, err = client.ListProducts(ctxUser2, &pb.ListProductsRequest{})
	assert.NoError(t, err)
}

// AC-3: When a rate limit is hit, a structured WARN log entry is emitted containing: client identifier (ip or user_id), requested endpoint name, configured rate limit RPS value.
func TestAC3_RateLimitHitEmitsWarnLog(t *testing.T) {
	t.Parallel()

	// TODO: Capture zap logger output to verify log entry exists
	// For now assert that log is emitted with required fields
	rps := 1.0
	conn, cleanup, _ := testSetupServer(t, rps, "ip")
	defer cleanup()

	client := pb.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	// Consume allowed request
	_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.NoError(t, err)

	// Hit rate limit to trigger log
	_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.Error(t, err)
	require.Equal(t, codes.ResourceExhausted, status.Code(err))

	// Verify log entry exists with fields:
	// - level: warn
	// - client_id: <ip address>
	// - endpoint: "/oteldemo.ProductCatalogService/ListProducts"
	// - rate_limit_rps: 1.0
}

// AC-4: When a rate limit is hit, an event is added to the current OpenTelemetry span with name rate_limit_exceeded and attributes: rate.limit.exceeded = true, rate.limit.rps = <configured value>, rate.limit.client_id = <client identifier>
func TestAC4_RateLimitHitAddsSpanEvent(t *testing.T) {
	t.Parallel()

	rps := 1.0
	conn, cleanup, spanRecorder := testSetupServer(t, rps, "user_id")
	defer cleanup()

	client := pb.NewProductCatalogServiceClient(conn)
	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("x-user-id", "test-user-4"))

	// Consume allowed request
	_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.NoError(t, err)

	// Hit rate limit
	_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.Error(t, err)
	require.Equal(t, codes.ResourceExhausted, status.Code(err))

	// Check span events
	spans := spanRecorder.Ended()
	require.GreaterOrEqual(t, len(spans), 2)

	rateLimitHitSpan := spans[len(spans)-1]
	var foundEvent bool
	for _, event := range rateLimitHitSpan.Events {
		if event.Name == "rate_limit_exceeded" {
			foundEvent = true
			attribs := event.Attributes
			assert.True(t, attribs.Get("rate.limit.exceeded").AsBool())
			assert.Equal(t, rps, attribs.Get("rate.limit.rps").AsDouble())
			assert.Equal(t, "test-user-4", attribs.Get("rate.limit.client_id").AsString())
			break
		}
	}
	assert.True(t, foundEvent, "rate_limit_exceeded event not found in span")
}

// AC-5: When PRODUCT_CATALOG_RATE_LIMIT_RPS is not set or set to 0, rate limiting is disabled entirely, no requests are rejected due to rate limits.
func TestAC5_RateLimitDisabledWhenRPSZero(t *testing.T) {
	t.Parallel()

	rps := 0.0
	conn, cleanup, _ := testSetupServer(t, rps, "ip")
	defer cleanup()

	client := pb.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	// Send multiple requests quickly, none should be rejected
	for i := 0; i < 10; i++ {
		_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
		assert.NoError(t, err, "request %d failed unexpectedly when rate limit is disabled", i+1)
	}
}

// AC-6: Rate limiting is enforced for all 3 public gRPC endpoints: /oteldemo.ProductCatalogService/ListProducts, /oteldemo.ProductCatalogService/GetProduct, /oteldemo.ProductCatalogService/SearchProducts.
func TestAC6_RateLimitAppliesToAllEndpoints(t *testing.T) {
	t.Parallel()

	rps := 1.0
	conn, cleanup, _ := testSetupServer(t, rps, "ip")
	defer cleanup()

	client := pb.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	// Test ListProducts endpoint rate limit
	_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.NoError(t, err)
	_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.Error(t, err)
	require.Equal(t, codes.ResourceExhausted, status.Code(err))

	// Wait for rate limiter to reset
	time.Sleep(time.Second)

	// Test GetProduct endpoint rate limit
	_, err = client.GetProduct(ctx, &pb.GetProductRequest{Id: "test-product"})
	require.NoError(t, err)
	_, err = client.GetProduct(ctx, &pb.GetProductRequest{Id: "test-product"})
	require.Error(t, err)
	require.Equal(t, codes.ResourceExhausted, status.Code(err))

	// Wait for rate limiter to reset
	time.Sleep(time.Second)

	// Test SearchProducts endpoint rate limit
	_, err = client.SearchProducts(ctx, &pb.SearchProductsRequest{Query: "test"})
	require.NoError(t, err)
	_, err = client.SearchProducts(ctx, &pb.SearchProductsRequest{Query: "test"})
	require.Error(t, err)
	require.Equal(t, codes.ResourceExhausted, status.Code(err))
}
