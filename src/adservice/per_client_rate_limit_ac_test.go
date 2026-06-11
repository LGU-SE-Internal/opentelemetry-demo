package main

import (
	"context"
	"net"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"

	pb "github.com/open-telemetry/opentelemetry-demo/src/adservice/genproto/oteldemo/adservice/v1"
)

const (
	testBufSize = 1024 * 1024
	testClientIDA = "client-a-id-123"
	testClientIDB = "client-b-id-456"
	testClientIPA = "10.0.0.1"
	testClientIPB = "10.0.0.2"
)

func setupTestServer(ctx context.Context, t *testing.T, envVars map[string]string) *grpc.ClientConn {
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

	// Create bufconn listener
	lis := bufconn.Listen(testBufSize)
	s := grpc.NewServer(
		// Client ID extraction and rate limiting interceptors will be added here by implementation
	)
	pb.RegisterAdServiceServer(s, &adService{})

	go func() {
		if err := s.Serve(lis); err != nil && err != grpc.ErrServerStopped {
			t.Errorf("Server exited with error: %v", err)
		}
	}()
	t.Cleanup(func() {
		s.Stop()
		lis.Close()
	})

	// Dial server with fake remote address context if needed
	conn, err := grpc.DialContext(ctx, "bufnet",
		grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) {
			return lis.Dial()
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	require.NoError(t, err)
	t.Cleanup(func() { conn.Close() })

	return conn
}

// Test_AC1_XClientIdHeaderUsedAsIdentifier
// AC-1: When a gRPC request includes a non-empty `x-client-id` metadata header, that value is used as the client identifier for rate limiting
func Test_AC1_XClientIdHeaderUsedAsIdentifier(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Set per-client rate limit to 1, so each client can only make 1 request per second
	conn := setupTestServer(ctx, t, map[string]string{
		"AD_SERVICE_PER_CLIENT_RATE_LIMIT": "1",
		"AD_SERVICE_GLOBAL_RATE_LIMIT": "100",
	})
	client := pb.NewAdServiceClient(conn)

	// First request with x-client-id header should succeed
	md := metadata.New(map[string]string{"x-client-id": testClientIDA})
	ctxWithClientID := metadata.NewOutgoingContext(ctx, md)
	_, err := client.GetAds(ctxWithClientID, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.NoError(t, err)

	// Second request with same x-client-id should be rate limited (same client ID)
	_, err = client.GetAds(ctxWithClientID, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.Error(t, err)
	assert.Equal(t, codes.ResourceExhausted, status.Code(err))

	// Second request with different x-client-id should succeed (different client ID)
	md2 := metadata.New(map[string]string{"x-client-id": testClientIDB})
	ctxWithClientID2 := metadata.NewOutgoingContext(ctx, md2)
	_, err = client.GetAds(ctxWithClientID2, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.NoError(t, err, "Different client ID should not be rate limited")
}

// Test_AC2_RemoteIPUsedAsFallbackIdentifier
// AC-2: When a gRPC request has no `x-client-id` header, the remote IP address of the client is used as the client identifier
func Test_AC2_RemoteIPUsedAsFallbackIdentifier(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Set per-client rate limit to 1
	conn := setupTestServer(ctx, t, map[string]string{
		"AD_SERVICE_PER_CLIENT_RATE_LIMIT": "1",
		"AD_SERVICE_GLOBAL_RATE_LIMIT": "100",
	})
	client := pb.NewAdServiceClient(conn)

	// First request from IP A, no x-client-id: should succeed
	// Note: Remote IP will be simulated in interceptor for test, or via context
	// For test purpose, we use x-forwarded-for as fallback, matching existing pattern
	ctxWithoutClientID := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-forwarded-for": testClientIPA}))
	_, err := client.GetAds(ctxWithoutClientID, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.NoError(t, err)

	// Second request from same IP A: should be rate limited
	_, err = client.GetAds(ctxWithoutClientID, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.Error(t, err)
	assert.Equal(t, codes.ResourceExhausted, status.Code(err))

	// Request from IP B: should succeed
	ctxWithoutClientID2 := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-forwarded-for": testClientIPB}))
	_, err = client.GetAds(ctxWithoutClientID2, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.NoError(t, err, "Different remote IP should not be rate limited")
}

// Test_AC3_PerClientCountersIndependent
// AC-3: Two different client IDs have independent rate limit counters: if client A exceeds their limit, client B is still able to make requests as long as they are under their own limit and global limit is not hit
func Test_AC3_PerClientCountersIndependent(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Set per-client rate limit to 2, global limit to 100 (so global not hit)
	conn := setupTestServer(ctx, t, map[string]string{
		"AD_SERVICE_PER_CLIENT_RATE_LIMIT": "2",
		"AD_SERVICE_GLOBAL_RATE_LIMIT": "100",
	})
	client := pb.NewAdServiceClient(conn)

	// Client A sends 3 requests: 2 allowed, 1 rate limited
	clientACtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDA}))
	clientARateLimited := 0
	for i := 0; i < 3; i++ {
		_, err := client.GetAds(clientACtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
		if err != nil && status.Code(err) == codes.ResourceExhausted {
			clientARateLimited++
		}
	}
	assert.Equal(t, 1, clientARateLimited, "Client A should have 1 rate limited request")

	// Client B sends 2 requests: both should be allowed, no rate limiting
	clientBCtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDB}))
	clientBRateLimited := 0
	for i := 0; i < 2; i++ {
		_, err := client.GetAds(clientBCtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
		if err != nil && status.Code(err) == codes.ResourceExhausted {
			clientBRateLimited++
		}
	}
	assert.Equal(t, 0, clientBRateLimited, "Client B should have no rate limited requests, counters are independent")
}

// Test_AC4_PerClientLimitEnforced
// AC-4: When the `AD_SERVICE_PER_CLIENT_RATE_LIMIT` environment variable is set to N, a single client cannot make more than N requests per second before getting a RESOURCE_EXHAUSTED response
func Test_AC4_PerClientLimitEnforced(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	const limitN = 5

	// Set per-client rate limit to N
	conn := setupTestServer(ctx, t, map[string]string{
		"AD_SERVICE_PER_CLIENT_RATE_LIMIT": string(rune(limitN + '0')),
		"AD_SERVICE_GLOBAL_RATE_LIMIT": "100",
	})
	client := pb.NewAdServiceClient(conn)
	clientCtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDA}))

	// Send N + 2 requests quickly, N allowed, 2 should be rate limited
	start := time.Now()
	rateLimitedCount := 0
	for i := 0; i < limitN + 2; i++ {
		_, err := client.GetAds(clientCtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
		if err != nil {
			st, ok := status.FromError(err)
			require.True(t, ok)
			if st.Code() == codes.ResourceExhausted {
				rateLimitedCount++
			} else {
				t.Fatalf("Unexpected error code: %v", st.Code())
			}
		}
	}
	duration := time.Since(start)
	assert.Less(t, duration, 1*time.Second, "All requests sent in under 1 second")
	assert.Equal(t, 2, rateLimitedCount, "Expected exactly 2 rate limited requests when limit is %d, sent %d", limitN, limitN+2)
}

// Test_AC5_PerClientLimitReturnsResourceExhausted
// AC-5: When a client exceeds their per-client rate limit, the response has gRPC status code RESOURCE_EXHAUSTED
func Test_AC5_PerClientLimitReturnsResourceExhausted(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Set per-client rate limit to 1
	conn := setupTestServer(ctx, t, map[string]string{
		"AD_SERVICE_PER_CLIENT_RATE_LIMIT": "1",
		"AD_SERVICE_GLOBAL_RATE_LIMIT": "100",
	})
	client := pb.NewAdServiceClient(conn)
	clientCtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDA}))

	// First request: success
	_, err := client.GetAds(clientCtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.NoError(t, err)

	// Second request: should fail with RESOURCE_EXHAUSTED
	_, err = client.GetAds(clientCtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
	require.Error(t, err)
	st, ok := status.FromError(err)
	require.True(t, ok)
	assert.Equal(t, codes.ResourceExhausted, st.Code())
	assert.Contains(t, st.Message(), "rate limit", "Error message should mention rate limit")
}

// Test_AC6_GlobalRateLimitStillApplies
// AC-6: The existing global rate limit still applies: if total requests from all clients exceed the global limit, all incoming requests get RESOURCE_EXHAUSTED response regardless of individual client usage
func Test_AC6_GlobalRateLimitStillApplies(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Set per-client limit to 2, global limit to 3 (so total across all clients can't exceed 3)
	conn := setupTestServer(ctx, t, map[string]string{
		"AD_SERVICE_PER_CLIENT_RATE_LIMIT": "2",
		"AD_SERVICE_GLOBAL_RATE_LIMIT": "3",
	})
	client := pb.NewAdServiceClient(conn)

	// Client A sends 2 requests (allowed, under per-client limit)
	clientACtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDA}))
	for i := 0; i < 2; i++ {
		_, err := client.GetAds(clientACtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
		require.NoError(t, err)
	}

	// Client B sends 2 requests: 1 allowed (total now 3), 1 should be rate limited by global limit
	clientBCtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDB}))
	rateLimited := 0
	for i := 0; i < 2; i++ {
		_, err := client.GetAds(clientBCtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
		if err != nil && status.Code(err) == codes.ResourceExhausted {
			rateLimited++
		}
	}
	assert.Equal(t, 1, rateLimited, "Global limit should trigger even though per-client limit is not hit for Client B")
}

// Test_AC7_PerClientLimitDisabledWhenZeroOrNegative
// AC-7: When `AD_SERVICE_PER_CLIENT_RATE_LIMIT` is set to 0 or negative value, per-client rate limiting is disabled and only global rate limit applies
func Test_AC7_PerClientLimitDisabledWhenZeroOrNegative(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	// Test case 1: set per-client limit to 0
	t.Run("per_client_limit_zero_disabled", func(t *testing.T) {
		conn := setupTestServer(ctx, t, map[string]string{
			"AD_SERVICE_PER_CLIENT_RATE_LIMIT": "0",
			"AD_SERVICE_GLOBAL_RATE_LIMIT": "10",
		})
		client := pb.NewAdServiceClient(conn)
		clientCtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDA}))

		// Send 8 requests from same client: all should succeed (per-client limit disabled, global limit 10 not hit)
		for i := 0; i < 8; i++ {
			_, err := client.GetAds(clientCtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
			require.NoError(t, err)
		}
	})

	// Test case 2: set per-client limit to negative value
	t.Run("per_client_limit_negative_disabled", func(t *testing.T) {
		conn := setupTestServer(ctx, t, map[string]string{
			"AD_SERVICE_PER_CLIENT_RATE_LIMIT": "-5",
			"AD_SERVICE_GLOBAL_RATE_LIMIT": "10",
		})
		client := pb.NewAdServiceClient(conn)
		clientCtx := metadata.NewOutgoingContext(ctx, metadata.New(map[string]string{"x-client-id": testClientIDA}))

		// Send 8 requests from same client: all should succeed (per-client limit disabled, global limit 10 not hit)
		for i := 0; i < 8; i++ {
			_, err := client.GetAds(clientCtx, &pb.GetAdsRequest{ContextKeys: []string{"test"}})
			require.NoError(t, err)
		}
	})
}
