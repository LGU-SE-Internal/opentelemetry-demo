package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel/trace"
	"golang.org/x/time/rate"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"

	pb "github.com/open-telemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo/productcatalogservice/v1"
)

const bufSize = 1024 * 1024

func startTestServer(t *testing.T, envVars map[string]string) *grpc.ClientConn {
	t.Helper()

	// Set environment variables
	for k, v := range envVars {
		os.Setenv(k, v)
	}
	t.Cleanup(func() {
		for k := range envVars {
			os.Unsetenv(k)
		}
	})

	lis := bufconn.Listen(bufSize)
	s := grpc.NewServer(
		grpc.UnaryInterceptor(UnaryRateLimitInterceptor(
			rate.Limit(getDefaultRateLimitRPM()),
			getEndpointRateLimitOverrides(),
		)),
	)

	svc := &productCatalogService{
		tracer: trace.NewNoopTracerProvider().Tracer("test"),
	}
	pb.RegisterProductCatalogServiceServer(s, svc)

	go func() {
		if err := s.Serve(lis); err != nil {
			t.Logf("Server exited with error: %v", err)
		}
	}()

	conn, err := grpc.NewClient(
		"bufnet",
		grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) {
			return lis.Dial()
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	require.NoError(t, err)

	t.Cleanup(func() {
		s.Stop()
		conn.Close()
	})

	return conn
}

// AC-1: Default rate limit applies to all endpoints when no overrides exist
func TestAC1_DefaultRateLimitEnforced(t *testing.T) {
	// Arrange
	conn := startTestServer(t, map[string]string{
		"PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM": "100",
	})
	client := pb.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	// Act - send 100 successful requests
	var lastErr error
	for i := 0; i < 100; i++ {
		_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
		if err != nil {
			lastErr = err
			break
		}
	}
	require.NoError(t, lastErr, "first 100 requests should succeed")

	// 101st request should be rejected
	_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})

	// Assert
	require.Error(t, err)
	st, ok := status.FromError(err)
	require.True(t, ok)
	assert.Equal(t, codes.ResourceExhausted, st.Code())
	assert.Contains(t, st.Message(), "rate limit exceeded for endpoint /oteldemo.ProductCatalogService/ListProducts, limit: 100 requests per minute")
}

// AC-2: Per-endpoint override takes precedence over default
func TestAC2_PerEndpointRateLimitOverride(t *testing.T) {
	// Arrange
	endpointOverrides := map[string]int{
		"/oteldemo.ProductCatalogService/ListProducts": 200,
	}
	overrideJSON, err := json.Marshal(endpointOverrides)
	require.NoError(t, err)

	conn := startTestServer(t, map[string]string{
		"PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM": "100",
		"PRODUCT_CATALOG_ENDPOINT_RATE_LIMITS":   string(overrideJSON),
	})
	client := pb.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	// Test ListProducts endpoint (override 200 RPM)
	var lastErr error
	for i := 0; i < 200; i++ {
		_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
		if err != nil {
			lastErr = err
			break
		}
	}
	require.NoError(t, lastErr, "first 200 requests to ListProducts should succeed")
	_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.Error(t, err)
	st, ok := status.FromError(err)
	require.True(t, ok)
	assert.Equal(t, codes.ResourceExhausted, st.Code())
	assert.Contains(t, st.Message(), "limit: 200 requests per minute")

	// Test GetProduct endpoint (uses default 100 RPM)
	for i := 0; i < 100; i++ {
		_, err := client.GetProduct(ctx, &pb.GetProductRequest{Id: "test-product"})
		if err != nil {
			lastErr = err
			break
		}
	}
	require.NoError(t, lastErr, "first 100 requests to GetProduct should succeed")
	_, err = client.GetProduct(ctx, &pb.GetProductRequest{Id: "test-product"})
	require.Error(t, err)
	st, ok = status.FromError(err)
	require.True(t, ok)
	assert.Equal(t, codes.ResourceExhausted, st.Code())
	assert.Contains(t, st.Message(), "limit: 100 requests per minute")
}

// AC-3: Structured log emitted when rate limit is hit
func TestAC3_RateLimitExceededLogsEvent(t *testing.T) {
	// Arrange - override logger to capture output
	originalLogger := logger
	t.Cleanup(func() { logger = originalLogger })
	var logOutput []byte
	logger = logger.WithSink(&testLogSink{output: &logOutput})

	conn := startTestServer(t, map[string]string{
		"PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM": "1",
	})
	client := pb.NewProductCatalogServiceClient(conn)
	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("X-Forwarded-For", "192.168.1.100"))

	// Act - exceed rate limit
	_, err := client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.NoError(t, err)
	_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
	require.Error(t, err)

	// Assert log contains required fields
	assert.Contains(t, string(logOutput), `"level":"warn"`)
	assert.Contains(t, string(logOutput), `"msg":"rate limit exceeded"`)
	assert.Contains(t, string(logOutput), `"client_ip":"192.168.1.100"`)
	assert.Contains(t, string(logOutput), `"endpoint":"/oteldemo.ProductCatalogService/ListProducts"`)
	assert.Contains(t, string(logOutput), `"limit_rpm":1`)
}

// AC-4: Invalid non-integer default rate limit causes startup failure
func TestAC4_InvalidNonIntegerDefaultRateLimitFailsStartup(t *testing.T) {
	os.Setenv("PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM", "abc")
	defer os.Unsetenv("PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM")

	// Attempt to load config should panic/fatal
	require.PanicsWithValue(t, "invalid default rate limit value:", func() {
		getDefaultRateLimitRPM()
	})
}

// AC-5: Invalid JSON endpoint rate limits causes startup failure
func TestAC5_InvalidEndpointRateLimitsJSONFailsStartup(t *testing.T) {
	os.Setenv("PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM", "100")
	os.Setenv("PRODUCT_CATALOG_ENDPOINT_RATE_LIMITS", `{"bad": json}`)
	defer func() {
		os.Unsetenv("PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM")
		os.Unsetenv("PRODUCT_CATALOG_ENDPOINT_RATE_LIMITS")
	}()

	// Attempt to load config should panic/fatal
	require.PanicsWithValue(t, "invalid endpoint rate limits configuration:", func() {
		getEndpointRateLimitOverrides()
	})
}

// AC-6: Default rate limit < 1 causes startup failure
func TestAC6_DefaultRateLimitLessThanOneFailsStartup(t *testing.T) {
	testCases := []string{"0", "-1", "-100"}
	for _, tc := range testCases {
		t.Run(fmt.Sprintf("value_%s", tc), func(t *testing.T) {
			os.Setenv("PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM", tc)
			defer os.Unsetenv("PRODUCT_CATALOG_DEFAULT_RATE_LIMIT_RPM")

			require.PanicsWithValue(t, "invalid default rate limit value:", func() {
				getDefaultRateLimitRPM()
			})
		})
	}
}

// testLogSink captures log output for verification
type testLogSink struct {
	output *[]byte
}

func (s *testLogSink) Enabled(_ context.Context, _ int) bool {
	return true
}

func (s *testLogSink) Info(_ int, msg string, keysAndValues ...interface{}) {}

func (s *testLogSink) Error(err error, msg string, keysAndValues ...interface{}) {}

func (s *testLogSink) WithValues(keysAndValues ...interface{}) *testLogSink {
	return s
}

func (s *testLogSink) WithName(name string) *testLogSink {
	return s
}
