package main

import (
	"context"
	"fmt"
	"net"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"

	genproto "github.com/open-telemetry/opentelemetry-demo/src/productcatalog/genproto/oteldemo"
)

const bufSize = 1024 * 1024

var lis *bufconn.Listener

func bufDialer(context.Context, string) (net.Conn, error) {
	return lis.Dial()
}

func startTestServer(t *testing.T, rateLimitRPS float64) *grpc.ClientConn {
	lis = bufconn.Listen(bufSize)

	// Setup metric reader for testing
	reader := metric.NewManualReader()
	meterProvider := metric.NewMeterProvider(metric.WithReader(reader))

	// Initialize rate limit interceptor from spec
	rateLimitCounter, err := meterProvider.Meter("product-catalog").Int64Counter("otel_demo_product_catalog_rate_limited_requests_total")
	require.NoError(t, err)
	interceptor := RateLimitInterceptor(rateLimitRPS, rateLimitCounter)

	s := grpc.NewServer(grpc.UnaryInterceptor(interceptor))
	genproto.RegisterProductCatalogServiceServer(s, &server{})

	go func() {
		if err := s.Serve(lis); err != nil {
			t.Logf("Server exited with error: %v", err)
		}
	}()

	conn, err := grpc.DialContext(context.Background(), "bufnet", grpc.WithContextDialer(bufDialer), grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err)

	t.Cleanup(func() {
		s.Stop()
		conn.Close()
	})

	return conn
}

func getMetricValue(t *testing.T, reader *metric.ManualReader, clientIP, grpcMethod string) int64 {
	var rm metricdata.ResourceMetrics
	err := reader.Collect(context.Background(), &rm)
	require.NoError(t, err)

	for _, sm := range rm.ScopeMetrics {
		for _, m := range sm.Metrics {
			if m.Name == "otel_demo_product_catalog_rate_limited_requests_total" {
				sum, ok := m.Data.(metricdata.Sum[int64])
				require.True(t, ok)
				for _, dp := range sum.DataPoints {
					ipMatch := false
					methodMatch := false
					for _, attr := range dp.Attributes {
						if attr.Key == "client_ip" && attr.Value.AsString() == clientIP {
							ipMatch = true
						}
						if attr.Key == "grpc_method" && attr.Value.AsString() == grpcMethod {
							methodMatch = true
						}
					}
					if ipMatch && methodMatch {
						return dp.Value
					}
				}
			}
		}
	}

	return 0
}

// Test_AC1_RateLimitPerIPEnforcesNRequestsPerSecond tests AC-1: When rate limit is N, single IP gets (requests - N) errors per second
func Test_AC1_RateLimitPerIPEnforcesNRequestsPerSecond(t *testing.T) {
	t.Parallel()
	const testRPS = 10.0
	conn := startTestServer(t, testRPS)
	client := genproto.NewProductCatalogServiceClient(conn)

	ctx := context.Background()
	start := time.Now()
	successCount := 0
	errorCount := 0

	// Send 20 requests in 1 second, should get 10 success, 10 errors
	for i := 0; i < 20; i++ {
		_, err := client.ListProducts(ctx, &genproto.ListProductsRequest{})
		if err == nil {
			successCount++
		} else {
			st, ok := status.FromError(err)
			require.True(t, ok)
			assert.Equal(t, codes.ResourceExhausted, st.Code())
			assert.Equal(t, "rate limit exceeded, please try again later", st.Message())
			errorCount++
		}
		// Space requests evenly over 1 second
		time.Sleep(50 * time.Millisecond)
	}

	elapsed := time.Since(start)
	require.True(t, elapsed < 1100*time.Millisecond, "Test took too long: %v", elapsed)
	assert.InDelta(t, testRPS, successCount, 2, "Should have approximately %d successful requests", int(testRPS))
	assert.InDelta(t, 20-testRPS, errorCount, 2, "Should have approximately %d rate limited requests", int(20-testRPS))
}

// Test_AC2_DefaultRateLimitAppliesWhenEnvVarNotSet tests AC-2: Default rate limit 100 RPS applies when env var not set
func Test_AC2_DefaultRateLimitAppliesWhenEnvVarNotSet(t *testing.T) {
	t.Parallel()
	// Unset env var to test default
	originalVal, hasVal := os.LookupEnv("PRODUCT_CATALOG_RATE_LIMIT_RPS")
	if hasVal {
		defer os.Setenv("PRODUCT_CATALOG_RATE_LIMIT_RPS", originalVal)
	} else {
		defer os.Unsetenv("PRODUCT_CATALOG_RATE_LIMIT_RPS")
	}
	os.Unsetenv("PRODUCT_CATALOG_RATE_LIMIT_RPS")

	// Get rate limit value from config (should be default 100)
	defaultRate := getRateLimitConfigFromEnv()
	assert.Equal(t, 100.0, defaultRate)

	// Test that 100 requests succeed, over that get errors
	conn := startTestServer(t, defaultRate)
	client := genproto.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	successCount := 0
	for i := 0; i < 105; i++ {
		_, err := client.ListProducts(ctx, &genproto.ListProductsRequest{})
		if err == nil {
			successCount++
		} else {
			assert.Equal(t, codes.ResourceExhausted, status.Code(err))
		}
		time.Sleep(1 * time.Millisecond)
	}

	assert.GreaterOrEqual(t, successCount, 100)
}

// Test_AC3_RateLimitedRequestsIncrementMetric tests AC-3: Rate limited requests increment metric with correct labels
func Test_AC3_RateLimitedRequestsIncrementMetric(t *testing.T) {
	t.Parallel()
	const testRPS = 5.0
	reader := metric.NewManualReader()
	meterProvider := metric.NewMeterProvider(metric.WithReader(reader))
	rateLimitCounter, err := meterProvider.Meter("product-catalog").Int64Counter("otel_demo_product_catalog_rate_limited_requests_total")
	require.NoError(t, err)

	interceptor := RateLimitInterceptor(testRPS, rateLimitCounter)

	lis = bufconn.Listen(bufSize)
	s := grpc.NewServer(grpc.UnaryInterceptor(interceptor))
	genproto.RegisterProductCatalogServiceServer(s, &server{})
	go s.Serve(lis)
	defer s.Stop()

	conn, err := grpc.DialContext(context.Background(), "bufnet", grpc.WithContextDialer(bufDialer), grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err)
	defer conn.Close()
	client := genproto.NewProductCatalogServiceClient(conn)

	testIP := "192.168.1.100"
	testMethod := "/oteldemo.ProductCatalogService/ListProducts"

	ctx := metadata.AppendToOutgoingContext(context.Background(), "X-Forwarded-For", testIP)

	// Send 10 requests, expect 5 errors
	for i := 0; i < 10; i++ {
		client.ListProducts(ctx, &genproto.ListProductsRequest{})
		time.Sleep(10 * time.Millisecond)
	}

	metricVal := getMetricValue(t, reader, testIP, testMethod)
	assert.InDelta(t, 5, metricVal, 2, "Metric should count 5 rate limited requests")
}

// Test_AC4_DistinctIPsHaveIndependentRateLimits tests AC-4: Different IPs have separate rate limits
func Test_AC4_DistinctIPsHaveIndependentRateLimits(t *testing.T) {
	t.Parallel()
	const testRPS = 5.0
	conn := startTestServer(t, testRPS)
	client := genproto.NewProductCatalogServiceClient(conn)

	ip1 := "10.0.0.1"
	ip2 := "10.0.0.2"

	ctx1 := metadata.AppendToOutgoingContext(context.Background(), "X-Forwarded-For", ip1)
	ctx2 := metadata.AppendToOutgoingContext(context.Background(), "X-Forwarded-For", ip2)

	successIP1 := 0
	successIP2 := 0

	// Send 10 requests from each IP in parallel
	for i := 0; i < 10; i++ {
		go func() {
			_, err := client.ListProducts(ctx1, &genproto.ListProductsRequest{})
			if err == nil {
				successIP1++
			}
		}()
		go func() {
			_, err := client.ListProducts(ctx2, &genproto.ListProductsRequest{})
			if err == nil {
				successIP2++
			}
		}()
		time.Sleep(10 * time.Millisecond)
	}

	time.Sleep(200 * time.Millisecond)

	// Each IP should have ~5 successful requests, no impact on each other
	assert.InDelta(t, 5, successIP1, 2)
	assert.InDelta(t, 5, successIP2, 2)
	assert.Equal(t, successIP1, successIP2, "Both IPs should have same success rate")
}

// Test_AC5_WithinLimitRequestsPassThroughUnchanged tests AC-5: In-limit requests are unmodified
func Test_AC5_WithinLimitRequestsPassThroughUnchanged(t *testing.T) {
	t.Parallel()
	conn := startTestServer(t, 100.0)
	client := genproto.NewProductCatalogServiceClient(conn)
	ctx := context.Background()

	// Test GetProduct request/response is unchanged
	testProductID := "OLJCESPC7Z"
	resp, err := client.GetProduct(ctx, &genproto.GetProductRequest{Id: testProductID})
	require.NoError(t, err)
	assert.Equal(t, testProductID, resp.Id)
	assert.NotEmpty(t, resp.Name)
	assert.NotEmpty(t, resp.Price)

	// Test ListProducts returns correct number of products
	listResp, err := client.ListProducts(ctx, &genproto.ListProductsRequest{})
	require.NoError(t, err)
	assert.Greater(t, len(listResp.Products), 0)
}

// Test_AC6_ClientIPExtractedCorrectly tests AC-6: IP extracted from X-Forwarded-For first, else peer address
func Test_AC6_ClientIPExtractedCorrectly(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name           string
		xForwardedFor  string
		expectedIP     string
	}{
		{
			name:           "Single IP in X-Forwarded-For",
			xForwardedFor:  "203.0.113.42",
			expectedIP:     "203.0.113.42",
		},
		{
			name:           "Multiple IPs in X-Forwarded-For, first used",
			xForwardedFor:  "198.51.100.10, 172.16.0.5, 10.0.0.2",
			expectedIP:     "198.51.100.10",
		},
		{
			name:           "No X-Forwarded-For header, use peer address",
			xForwardedFor:  "",
			expectedIP:     "127.0.0.1", // bufconn peer is loopback
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			const testRPS = 1.0
			reader := metric.NewManualReader()
			meterProvider := metric.NewMeterProvider(metric.WithReader(reader))
			rateLimitCounter, err := meterProvider.Meter("product-catalog").Int64Counter("otel_demo_product_catalog_rate_limited_requests_total")
			require.NoError(t, err)
			interceptor := RateLimitInterceptor(testRPS, rateLimitCounter)

			lis = bufconn.Listen(bufSize)
			s := grpc.NewServer(grpc.UnaryInterceptor(interceptor))
			genproto.RegisterProductCatalogServiceServer(s, &server{})
			go s.Serve(lis)
			defer s.Stop()

			conn, err := grpc.DialContext(context.Background(), "bufnet", grpc.WithContextDialer(bufDialer), grpc.WithTransportCredentials(insecure.NewCredentials()))
			require.NoError(t, err)
			defer conn.Close()
			client := genproto.NewProductCatalogServiceClient(conn)

			var ctx context.Context
			if tt.xForwardedFor != "" {
				ctx = metadata.AppendToOutgoingContext(context.Background(), "X-Forwarded-For", tt.xForwardedFor)
			} else {
				ctx = context.Background()
			}

			// Send 2 requests, first success, second rate limited
			client.ListProducts(ctx, &genproto.ListProductsRequest{})
			client.ListProducts(ctx, &genproto.ListProductsRequest{})

			// Check metric has expected IP
			metricVal := getMetricValue(t, reader, tt.expectedIP, "/oteldemo.ProductCatalogService/ListProducts")
			assert.Equal(t, int64(1), metricVal, "Metric should count rate limit for IP %s", tt.expectedIP)
		})
	}
}
