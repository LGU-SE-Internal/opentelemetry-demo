package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestAC1_HealthReturns200WhenQuoteServiceReachable tests AC-1: When GET /health is called and the quote service is reachable, returns 200 OK
func TestAC1_HealthReturns200WhenQuoteServiceReachable(t *testing.T) {
	// Setup test server
	req, err := http.NewRequest("GET", "/health", nil)
	require.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.HandlerFunc(healthHandler) // Assume healthHandler is the handler for /health endpoint

	// Simulate quote service being reachable
	mockQuoteServiceReachable = true

	handler.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusOK, rr.Code)
}

// TestAC2_HealthReturns503WhenQuoteServiceUnreachable tests AC-2: When GET /health is called and quote service is unreachable, returns 503 Service Unavailable
func TestAC2_HealthReturns503WhenQuoteServiceUnreachable(t *testing.T) {
	req, err := http.NewRequest("GET", "/health", nil)
	require.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.HandlerFunc(healthHandler)

	// Simulate quote service being unreachable
	mockQuoteServiceReachable = false

	handler.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusServiceUnavailable, rr.Code)
}

// TestAC3_200ResponseHasCorrectStructure tests AC-3: 200 responses include correct JSON fields
func TestAC3_200ResponseHasCorrectStructure(t *testing.T) {
	req, err := http.NewRequest("GET", "/health", nil)
	require.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.HandlerFunc(healthHandler)

	mockQuoteServiceReachable = true
	handler.ServeHTTP(rr, req)

	var response map[string]interface{}
	err = json.Unmarshal(rr.Body.Bytes(), &response)
	require.NoError(t, err)

	// Check status field
	assert.Equal(t, "healthy", response["status"])

	// Check timestamp is valid RFC3339
	timestampStr, ok := response["timestamp"].(string)
	require.True(t, ok)
	_, err = time.Parse(time.RFC3339, timestampStr)
	assert.NoError(t, err)

	// Check dependencies
	dependencies, ok := response["dependencies"].(map[string]interface{})
	require.True(t, ok)
	assert.Equal(t, "healthy", dependencies["quoteService"])
}

// TestAC4_503ResponseHasCorrectStructure tests AC-4: 503 responses include correct JSON fields
func TestAC4_503ResponseHasCorrectStructure(t *testing.T) {
	req, err := http.NewRequest("GET", "/health", nil)
	require.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.HandlerFunc(healthHandler)

	mockQuoteServiceReachable = false
	handler.ServeHTTP(rr, req)

	var response map[string]interface{}
	err = json.Unmarshal(rr.Body.Bytes(), &response)
	require.NoError(t, err)

	// Check status field
	assert.Equal(t, "unhealthy", response["status"])

	// Check timestamp is valid RFC3339
	timestampStr, ok := response["timestamp"].(string)
	require.True(t, ok)
	_, err = time.Parse(time.RFC3339, timestampStr)
	assert.NoError(t, err)

	// Check dependencies
	dependencies, ok := response["dependencies"].(map[string]interface{})
	require.True(t, ok)
	assert.Equal(t, "unhealthy", dependencies["quoteService"])
}

// TestAC5_ResponseWithinOneSecond tests AC-5: /health endpoint returns response in <= 1 second even when quote service is slow
func TestAC5_ResponseWithinOneSecond(t *testing.T) {
	req, err := http.NewRequest("GET", "/health", nil)
	require.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.HandlerFunc(healthHandler)

	// Simulate slow quote service (takes 2 seconds to respond)
	mockQuoteServiceResponseDelay = 2 * time.Second

	startTime := time.Now()
	handler.ServeHTTP(rr, req)
	duration := time.Since(startTime)

	// Response should be <= 1 second
	assert.LessOrEqual(t, duration.Seconds(), 1.0)
}

// TestAC6_NoAuthenticationRequired tests AC-6: /health accepts requests without authentication headers
func TestAC6_NoAuthenticationRequired(t *testing.T) {
	req, err := http.NewRequest("GET", "/health", nil)
	require.NoError(t, err)

	// Explicitly remove any auth headers
	req.Header.Del("Authorization")
	req.Header.Del("Cookie")

	rr := httptest.NewRecorder()
	handler := http.HandlerFunc(healthHandler)

	mockQuoteServiceReachable = true
	handler.ServeHTTP(rr, req)

	// Should not get 401/403
	assert.NotEqual(t, http.StatusUnauthorized, rr.Code)
	assert.NotEqual(t, http.StatusForbidden, rr.Code)
	// Should return 200 as service is healthy
	assert.Equal(t, http.StatusOK, rr.Code)
}

// TestAC7_NoSideEffects tests AC-7: Calling /health does not modify any service state or persist data
func TestAC7_NoSideEffects(t *testing.T) {
	// Check that multiple calls return the same state when dependencies are unchanged
	req, err := http.NewRequest("GET", "/health", nil)
	require.NoError(t, err)

	mockQuoteServiceReachable = true

	// First call
	rr1 := httptest.NewRecorder()
	handler := http.HandlerFunc(healthHandler)
	handler.ServeHTTP(rr1, req)
	assert.Equal(t, http.StatusOK, rr1.Code)

	// Second call immediately after
	rr2 := httptest.NewRecorder()
	handler.ServeHTTP(rr2, req)
	assert.Equal(t, http.StatusOK, rr2.Code)

	// Both responses should have same status for quote service
	var resp1, resp2 map[string]interface{}
	_ = json.Unmarshal(rr1.Body.Bytes(), &resp1)
	_ = json.Unmarshal(rr2.Body.Bytes(), &resp2)
	deps1 := resp1["dependencies"].(map[string]interface{})
	deps2 := resp2["dependencies"].(map[string]interface{})
	assert.Equal(t, deps1["quoteService"], deps2["quoteService"])
}

// Mock variables to simulate quote service behavior (will be replaced with actual implementation mocks)
var (
	mockQuoteServiceReachable     = true
	mockQuoteServiceResponseDelay = 0 * time.Second
)
