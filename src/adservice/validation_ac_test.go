package main

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	pb "github.com/open-telemetry/opentelemetry-demo/src/adservice/pb/oteldemo/pb"
)

const (
	testGRPCPort = "localhost:9555"
)

func setupTestClient(t *testing.T) pb.AdServiceClient {
	// Start service in background
	go func() {
		main()
	}()

	// Wait for service to start
	time.Sleep(2 * time.Second)

	conn, err := grpc.Dial(testGRPCPort, grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err, "Failed to connect to gRPC server")

	t.Cleanup(func() {
		_ = conn.Close()
	})

	return pb.NewAdServiceClient(conn)
}

func TestAC1_EmptyUserIdRejected(t *testing.T) {
	client := setupTestClient(t)
	req := &pb.AdRequest{
		UserId:      "",
		ContextKeys: []string{"test"},
		Category:    []string{"clothing"},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "user_id is required")
}

func TestAC2_UserIdExceedsMaxLengthRejected(t *testing.T) {
	client := setupTestClient(t)
	longUserId := strings.Repeat("a", 129)
	req := &pb.AdRequest{
		UserId:      longUserId,
		ContextKeys: []string{"test"},
		Category:    []string{"clothing"},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "user_id exceeds maximum length of 128 characters")
}

func TestAC3_UserIdWithInvalidCharsRejected(t *testing.T) {
	client := setupTestClient(t)
	invalidUserId := "user@123" // contains @ which is invalid
	req := &pb.AdRequest{
		UserId:      invalidUserId,
		ContextKeys: []string{"test"},
		Category:    []string{"clothing"},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "user_id contains invalid characters")
}

func TestAC4_EmptyContextKeyRejected(t *testing.T) {
	client := setupTestClient(t)
	req := &pb.AdRequest{
		UserId:      "valid_user_123",
		ContextKeys: []string{"test", ""}, // empty string in context keys
		Category:    []string{"clothing"},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "context_keys cannot contain empty values")
}

func TestAC5_ContextKeyExceedsMaxLengthRejected(t *testing.T) {
	client := setupTestClient(t)
	longKey := strings.Repeat("b", 65)
	req := &pb.AdRequest{
		UserId:      "valid_user_123",
		ContextKeys: []string{longKey},
		Category:    []string{"clothing"},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "context_key exceeds maximum length of 64 characters")
}

func TestAC6_ContextKeyWithInvalidCharsRejected(t *testing.T) {
	client := setupTestClient(t)
	invalidKey := "context:key" // contains colon which is invalid
	req := &pb.AdRequest{
		UserId:      "valid_user_123",
		ContextKeys: []string{invalidKey},
		Category:    []string{"clothing"},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "context_key contains invalid characters")
}

func TestAC7_EmptyCategoryRejected(t *testing.T) {
	client := setupTestClient(t)
	req := &pb.AdRequest{
		UserId:      "valid_user_123",
		ContextKeys: []string{"test"},
		Category:    []string{"clothing", ""}, // empty string in categories
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "category cannot contain empty values")
}

func TestAC8_CategoryExceedsMaxLengthRejected(t *testing.T) {
	client := setupTestClient(t)
	longCategory := strings.Repeat("c", 33)
	req := &pb.AdRequest{
		UserId:      "valid_user_123",
		ContextKeys: []string{"test"},
		Category:    []string{longCategory},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "category exceeds maximum length of 32 characters")
}

func TestAC9_CategoryWithInvalidCharsRejected(t *testing.T) {
	client := setupTestClient(t)
	invalidCategory := "clothing_shoes" // contains underscore which is invalid for categories
	req := &pb.AdRequest{
		UserId:      "valid_user_123",
		ContextKeys: []string{"test"},
		Category:    []string{invalidCategory},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.Nil(t, resp)
	require.Error(t, err)

	st, ok := status.FromError(err)
	assert.True(t, ok)
	assert.Equal(t, codes.InvalidArgument, st.Code())
	assert.Contains(t, st.Message(), "category contains invalid characters")
}

func TestAC11_ValidRequestProcessedNormally(t *testing.T) {
	client := setupTestClient(t)
	req := &pb.AdRequest{
		UserId:      "valid-user_123",
		ContextKeys: []string{"valid-key-1", "another_valid_key2"},
		Category:    []string{"clothing", "home-goods"},
	}

	resp, err := client.GetAds(context.Background(), req)
	assert.NoError(t, err)
	assert.NotNil(t, resp)
	assert.GreaterOrEqual(t, len(resp.Ads), 0)
}

func TestAC12_ValidationPerformanceUnder1ms(t *testing.T) {
	client := setupTestClient(t)
	req := &pb.AdRequest{
		UserId:      "valid-user_123",
		ContextKeys: []string{"test-key"},
		Category:    []string{"clothing"},
	}

	// Run 100 times to get average
	var totalDuration time.Duration
	iterations := 100

	for i := 0; i < iterations; i++ {
		start := time.Now()
		_, err := client.GetAds(context.Background(), req)
		dur := time.Since(start)
		totalDuration += dur
		assert.NoError(t, err)
	}

	avgDuration := totalDuration / time.Duration(iterations)
	assert.Less(t, avgDuration, 1*time.Millisecond, "Average validation time should be less than 1ms")
}
