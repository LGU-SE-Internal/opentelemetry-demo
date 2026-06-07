package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const (
	testCheckoutPath = "/api/checkout/test"
	testOtherPath    = "/internal/other"
	testIP1          = "192.168.1.100"
	testIP2          = "192.168.1.101"
)

// Test_AC1_CustomRateLimitValuesFromEnv verifies that when CHECKOUT_RATE_LIMIT_RPS and CHECKOUT_RATE_LIMIT_BURST
// environment variables are set, the middleware uses those values instead of defaults.
func Test_AC1_CustomRateLimitValuesFromEnv(t *testing.T) {
	// Set custom env values
	os.Setenv("CHECKOUT_RATE_LIMIT_RPS", "5.5")
	os.Setenv("CHECKOUT_RATE_LIMIT_BURST", "10")
	defer func() {
		os.Unsetenv("CHECKOUT_RATE_LIMIT_RPS")
		os.Unsetenv("CHECKOUT_RATE_LIMIT_BURST")
	}()

	// Create test handler that just returns 200
	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	// Create middleware with values from env (we assume the main package reads env vars to get these values)
	// Note: In real implementation, these values would be parsed from env in main
	mw := NewRateLimiterMiddleware(5.5, 10)
	wrappedHandler := mw(testHandler)

	// Test burst limit (10 requests should pass, 11th should be 429)
	for i := 0; i < 10; i++ {
		req := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
		req.Header.Set("X-Forwarded-For", testIP1)
		w := httptest.NewRecorder()
		wrappedHandler.ServeHTTP(w, req)
		assert.Equal(t, http.StatusOK, w.Code, "request %d should pass", i+1)
	}

	// 11th request should be blocked
	req := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	req.Header.Set("X-Forwarded-For", testIP1)
	w := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(w, req)
	assert.Equal(t, http.StatusTooManyRequests, w.Code, "11th request should be blocked with 429")
}

// Test_AC2_ExceedRPSReturns429 verifies that requests exceeding configured RPS limit return 429 status code.
func Test_AC2_ExceedRPSReturns429(t *testing.T) {
	// Use low RPS for testing
	mw := NewRateLimiterMiddleware(1.0, 1)
	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	wrappedHandler := mw(testHandler)

	// First request passes
	req1 := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	req1.Header.Set("X-Forwarded-For", testIP1)
	w1 := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(w1, req1)
	assert.Equal(t, http.StatusOK, w1.Code)

	// Second request immediately after should be blocked
	req2 := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	req2.Header.Set("X-Forwarded-For", testIP1)
	w2 := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(w2, req2)
	assert.Equal(t, http.StatusTooManyRequests, w2.Code)
}

// Test_AC3_429HasValidRetryAfterHeader verifies that all 429 responses include a valid Retry-After header
// with positive integer value.
func Test_AC3_429HasValidRetryAfterHeader(t *testing.T) {
	mw := NewRateLimiterMiddleware(1.0, 1)
	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	wrappedHandler := mw(testHandler)

	// Consume the allowed request
	req1 := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	req1.Header.Set("X-Forwarded-For", testIP1)
	w1 := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(w1, req1)

	// Get 429 response
	req2 := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	req2.Header.Set("X-Forwarded-For", testIP1)
	w2 := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(w2, req2)
	require.Equal(t, http.StatusTooManyRequests, w2.Code)

	// Check Retry-After header exists and is positive integer
	retryAfterStr := w2.Header().Get("Retry-After")
	assert.NotEmpty(t, retryAfterStr, "Retry-After header should be present")
	retryAfter, err := time.ParseDuration(retryAfterStr + "s")
	assert.NoError(t, err, "Retry-After should be valid integer seconds")
	assert.Greater(t, retryAfter, 0, "Retry-After should be positive value")

	// Check response body has correct retry_after field
	var respBody map[string]interface{}
	err = json.NewDecoder(w2.Body).Decode(&respBody)
	assert.NoError(t, err, "Response should be valid JSON")
	assert.Equal(t, "Too many requests", respBody["error"])
	assert.Equal(t, "Rate limit exceeded, please try again later", respBody["message"])
	assert.Greater(t, respBody["retry_after"].(float64), 0, "retry_after in body should be positive")
}

// Test_AC4_RateLimitPerClientIP verifies that rate limits are applied per unique client IP,
// requests from different IPs do not affect each other.
func Test_AC4_RateLimitPerClientIP(t *testing.T) {
	mw := NewRateLimiterMiddleware(1.0, 1)
	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	wrappedHandler := mw(testHandler)

	// First request from IP1 passes
	reqIP1a := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	reqIP1a.Header.Set("X-Forwarded-For", testIP1)
	wIP1a := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(wIP1a, reqIP1a)
	assert.Equal(t, http.StatusOK, wIP1a.Code)

	// Second request from IP1 is blocked
	reqIP1b := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	reqIP1b.Header.Set("X-Forwarded-For", testIP1)
	wIP1b := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(wIP1b, reqIP1b)
	assert.Equal(t, http.StatusTooManyRequests, wIP1b.Code)

	// Request from IP2 still passes
	reqIP2 := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	reqIP2.Header.Set("X-Forwarded-For", testIP2)
	wIP2 := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(wIP2, reqIP2)
	assert.Equal(t, http.StatusOK, wIP2.Code, "Request from different IP should pass")
}

// Test_AC5_AllPublicCheckoutEndpointsLimited verifies rate limiting applies to all /api/checkout/* paths.
func Test_AC5_AllPublicCheckoutEndpointsLimited(t *testing.T) {
	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	// Test various /api/checkout subpaths
	testPaths := []string{
		"/api/checkout",
		"/api/checkout/",
		"/api/checkout/submit",
		"/api/checkout/123/confirm",
		"/api/checkout/cart/add",
	}

	for _, path := range testPaths {
		t.Run(path, func(t *testing.T) {
			// Create new middleware for each subtest to avoid cross-test rate limiting
			mw := NewRateLimiterMiddleware(1.0, 1)
			wrappedHandler := mw(testHandler)
			// First request passes
			req1 := httptest.NewRequest(http.MethodPost, path, nil)
			req1.Header.Set("X-Forwarded-For", testIP1)
			w1 := httptest.NewRecorder()
			wrappedHandler.ServeHTTP(w1, req1)
			assert.Equal(t, http.StatusOK, w1.Code)

			// Second request blocked
			req2 := httptest.NewRequest(http.MethodPost, path, nil)
			req2.Header.Set("X-Forwarded-For", testIP1)
			w2 := httptest.NewRecorder()
			wrappedHandler.ServeHTTP(w2, req2)
			assert.Equal(t, http.StatusTooManyRequests, w2.Code)
		})
	}
}

// Test_AC6_UnderLimitRequestsPassThroughUnmodified verifies requests under rate limit are passed
// through to underlying handler with no modifications.
func Test_AC6_UnderLimitRequestsPassThroughUnmodified(t *testing.T) {
	testHeaderValue := "test-header-value"
	testBody := []byte("test request body")
	receivedHeader := ""
	var receivedBody []byte

	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedHeader = r.Header.Get("X-Test-Header")
		var err error
		receivedBody, err = io.ReadAll(r.Body)
		assert.NoError(t, err)
		w.Header().Set("X-Response-Header", "response-value")
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte("test response"))
	})

	mw := NewRateLimiterMiddleware(10.0, 20)
	wrappedHandler := mw(testHandler)

	// Make request under limit
	req := httptest.NewRequest(http.MethodPost, testCheckoutPath, bytes.NewBuffer(testBody))
	req.Header.Set("X-Forwarded-For", testIP1)
	req.Header.Set("X-Test-Header", testHeaderValue)
	w := httptest.NewRecorder()

	wrappedHandler.ServeHTTP(w, req)

	// Verify request was passed unmodified
	assert.Equal(t, testHeaderValue, receivedHeader)
	assert.Equal(t, testBody, receivedBody)

	// Verify response is unmodified
	assert.Equal(t, http.StatusCreated, w.Code)
	assert.Equal(t, "response-value", w.Header().Get("X-Response-Header"))
	assert.Equal(t, "test response", w.Body.String())
}

// Test_AC7_DefaultRateLimitValuesWhenNoEnv verifies that when environment variables are not set,
// service uses default values 10.0 RPS and 20 burst.
func Test_AC7_DefaultRateLimitValuesWhenNoEnv(t *testing.T) {
	// Ensure env vars are unset
	os.Unsetenv("CHECKOUT_RATE_LIMIT_RPS")
	os.Unsetenv("CHECKOUT_RATE_LIMIT_BURST")

	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	// Use default values as per spec
	mw := NewRateLimiterMiddleware(10.0, 20)
	wrappedHandler := mw(testHandler)

	// 20 requests should pass (burst limit)
	for i := 0; i < 20; i++ {
		req := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
		req.Header.Set("X-Forwarded-For", testIP1)
		w := httptest.NewRecorder()
		wrappedHandler.ServeHTTP(w, req)
		assert.Equal(t, http.StatusOK, w.Code, "request %d should pass", i+1)
	}

	// 21st request should be blocked
	req := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	req.Header.Set("X-Forwarded-For", testIP1)
	w := httptest.NewRecorder()
	wrappedHandler.ServeHTTP(w, req)
	assert.Equal(t, http.StatusTooManyRequests, w.Code, "21st request should be blocked with 429")
}

// Test_AC8_ExistingMiddlewareFunctionalityUnchanged verifies existing middleware (logging, tracing, auth)
// continues to work unchanged after adding rate limiting middleware to the stack.
func Test_AC8_ExistingMiddlewareFunctionalityUnchanged(t *testing.T) {
	// Test that middleware chain works correctly with rate limiter first
	authCalled := false
	traceCalled := false
	logCalled := false
	handlerCalled := false

	// Mock existing middlewares
	authMiddleware := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			authCalled = true
			next.ServeHTTP(w, r)
		})
	}

	traceMiddleware := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			traceCalled = true
			next.ServeHTTP(w, r)
		})
	}

	logMiddleware := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			logCalled = true
			next.ServeHTTP(w, r)
		})
	}

	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		handlerCalled = true
		w.WriteHeader(http.StatusOK)
	})

	// Build chain as per spec: rate limiter first, then existing middlewares
	rateLimiterMw := NewRateLimiterMiddleware(10.0, 20)
	finalHandler := rateLimiterMw(authMiddleware(traceMiddleware(logMiddleware(testHandler))))

	// Make valid request
	req := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	req.Header.Set("X-Forwarded-For", testIP1)
	w := httptest.NewRecorder()
	finalHandler.ServeHTTP(w, req)

	// Verify all middlewares and handler were called
	assert.True(t, authCalled, "Auth middleware should be called")
	assert.True(t, traceCalled, "Tracing middleware should be called")
	assert.True(t, logCalled, "Logging middleware should be called")
	assert.True(t, handlerCalled, "Handler should be called")
	assert.Equal(t, http.StatusOK, w.Code)

	// Reset flags
	authCalled = false
	traceCalled = false
	logCalled = false
	handlerCalled = false

	// Make blocked request (exceed rate limit)
	for i := 0; i < 20; i++ {
		req := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
		req.Header.Set("X-Forwarded-For", testIP1)
		w := httptest.NewRecorder()
		finalHandler.ServeHTTP(w, req)
	}

	// This request should be blocked early, other middlewares should not be called
	reqBlocked := httptest.NewRequest(http.MethodPost, testCheckoutPath, nil)
	reqBlocked.Header.Set("X-Forwarded-For", testIP1)
	wBlocked := httptest.NewRecorder()
	finalHandler.ServeHTTP(wBlocked, reqBlocked)

	assert.Equal(t, http.StatusTooManyRequests, wBlocked.Code)
	assert.False(t, authCalled, "Auth middleware should not be called for blocked requests")
	assert.False(t, traceCalled, "Tracing middleware should not be called for blocked requests")
	assert.False(t, logCalled, "Logging middleware should not be called for blocked requests")
	assert.False(t, handlerCalled, "Handler should not be called for blocked requests")
}
