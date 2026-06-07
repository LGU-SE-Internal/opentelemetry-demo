package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

// TestAC1_LivenessUpWhenRunning tests AC-1: GET /health/liveness returns 200 OK with {"status": "UP"} when service is running
func TestAC1_LivenessUpWhenRunning(t *testing.T) {
	req, err := http.NewRequest("GET", "/health/liveness", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.DefaultServeMux // Assume main service mux is used

	handler.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusOK, rr.Code, "Expected 200 OK status for liveness endpoint when running")
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Expected Content-Type: application/json header")

	var response map[string]interface{}
	err = json.Unmarshal(rr.Body.Bytes(), &response)
	assert.NoError(t, err, "Expected valid JSON response body")
	assert.Equal(t, "UP", response["status"], "Expected status UP in response")
}

// TestAC2_LivenessDownWhenShuttingDown tests AC-2: GET /health/liveness returns 503 when service is shutting down
func TestAC2_LivenessDownWhenShuttingDown(t *testing.T) {
	// Simulate shutdown state
	// Note: This test assumes an exported shutdown flag or mechanism exists in the implementation
	// For spec testing, we validate the expected behavior when shutdown is triggered

	req, err := http.NewRequest("GET", "/health/liveness", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.DefaultServeMux

	// TODO: Simulate shutdown state once implementation exists
	// For now this test will fail as expected

	handler.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusServiceUnavailable, rr.Code, "Expected 503 Service Unavailable when shutting down")
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Expected Content-Type: application/json header")

	var response map[string]interface{}
	err = json.Unmarshal(rr.Body.Bytes(), &response)
	assert.NoError(t, err, "Expected valid JSON response body")
	assert.Equal(t, "DOWN", response["status"], "Expected status DOWN in response")
	assert.Equal(t, "service shutting down", response["error"], "Expected correct error message for shutdown state")
}

// TestAC3_ReadinessDownWhenCatalogNotLoaded tests AC-3: GET /health/readiness returns 503 when catalog not loaded
func TestAC3_ReadinessDownWhenCatalogNotLoaded(t *testing.T) {
	req, err := http.NewRequest("GET", "/health/readiness", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.DefaultServeMux

	// Test scenario before catalog is loaded
	handler.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusServiceUnavailable, rr.Code, "Expected 503 Service Unavailable when catalog not loaded")
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Expected Content-Type: application/json header")

	var response map[string]interface{}
	err = json.Unmarshal(rr.Body.Bytes(), &response)
	assert.NoError(t, err, "Expected valid JSON response body")
	assert.Equal(t, "DOWN", response["status"], "Expected status DOWN in response")
	assert.Equal(t, "catalog not loaded", response["error"], "Expected correct error message when catalog not loaded")
}

// TestAC4_ReadinessUpWhenCatalogLoaded tests AC-4: GET /health/readiness returns 200 with catalog_count when loaded
func TestAC4_ReadinessUpWhenCatalogLoaded(t *testing.T) {
	req, err := http.NewRequest("GET", "/health/readiness", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.DefaultServeMux

	// Test scenario after catalog is fully loaded
	handler.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusOK, rr.Code, "Expected 200 OK status when catalog is loaded")
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Expected Content-Type: application/json header")

	var response map[string]interface{}
	err = json.Unmarshal(rr.Body.Bytes(), &response)
	assert.NoError(t, err, "Expected valid JSON response body")
	assert.Equal(t, "UP", response["status"], "Expected status UP in response")
	catalogCount, ok := response["catalog_count"].(float64)
	assert.True(t, ok, "catalog_count should be a number")
	assert.GreaterOrEqual(t, int(catalogCount), 0, "Expected catalog_count to be non-negative integer")
}

// TestAC5_ReadinessDownWhenCatalogInaccessible tests AC-5: GET /health/readiness returns 503 when catalog becomes inaccessible post startup
func TestAC5_ReadinessDownWhenCatalogInaccessible(t *testing.T) {
	req, err := http.NewRequest("GET", "/health/readiness", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	handler := http.DefaultServeMux

	// Test scenario where catalog was loaded but now is inaccessible
	handler.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusServiceUnavailable, rr.Code, "Expected 503 Service Unavailable when catalog becomes inaccessible")
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Expected Content-Type: application/json header")

	var response map[string]interface{}
	err = json.Unmarshal(rr.Body.Bytes(), &response)
	assert.NoError(t, err, "Expected valid JSON response body")
	assert.Equal(t, "DOWN", response["status"], "Expected status DOWN in response")
	assert.Contains(t, response["error"].(string), "catalog", "Expected error message to reference catalog access issue")
}

// TestAC6_HealthRequestsProduceStructuredLogs tests AC-6: All health requests produce structured INFO logs with required fields
func TestAC6_HealthRequestsProduceStructuredLogs(t *testing.T) {
	// Capture zap logs
	var logBuffer bytes.Buffer
	encoder := zapcore.NewJSONEncoder(zap.NewProductionEncoderConfig())
	core := zapcore.NewCore(encoder, zapcore.AddSync(&logBuffer), zap.InfoLevel)
	logger := zap.New(core)

	// Replace global logger (implementation should use existing zap logger)
	originalLogger := zap.L()
	zap.ReplaceGlobals(logger)
	defer zap.ReplaceGlobals(originalLogger)

	// Test liveness endpoint log
	req, _ := http.NewRequest("GET", "/health/liveness", nil)
	rr := httptest.NewRecorder()
	handler := http.DefaultServeMux
	start := time.Now()
	handler.ServeHTTP(rr, req)
	time.Since(start)

	// Check log output
	logOutput := logBuffer.String()
	assert.Contains(t, logOutput, "\"endpoint\":\"/health/liveness\"", "Log should contain endpoint field")
	assert.Contains(t, logOutput, "\"status_code\":", "Log should contain status_code field")
	assert.Contains(t, logOutput, "\"duration_ms\":", "Log should contain duration_ms field")
	if rr.Code != http.StatusOK {
		assert.Contains(t, logOutput, "\"error\":", "Log should contain error field for non-200 responses")
	}

	// Reset buffer and test readiness endpoint log
	logBuffer.Reset()
	req, _ = http.NewRequest("GET", "/health/readiness", nil)
	rr = httptest.NewRecorder()
	start = time.Now()
	handler.ServeHTTP(rr, req)
	time.Since(start)

	logOutput = logBuffer.String()
	assert.Contains(t, logOutput, "\"endpoint\":\"/health/readiness\"", "Log should contain endpoint field for readiness")
	assert.Contains(t, logOutput, "\"status_code\":", "Log should contain status_code field for readiness")
	assert.Contains(t, logOutput, "\"duration_ms\":", "Log should contain duration_ms field for readiness")
	if rr.Code != http.StatusOK {
		assert.Contains(t, logOutput, "\"error\":", "Log should contain error field for non-200 responses")
	}
}

// TestAC7_HealthEndpointsOnSamePort tests AC-7: Health endpoints are accessible on same port as main service
func TestAC7_HealthEndpointsOnSamePort(t *testing.T) {
	// This test validates that the health endpoints are registered on the main service mux
	// which is bound to the main service port, not a separate server

	// Check that both endpoints are registered on the default mux (used by main service)
	livenessHandler, livenessPattern := http.DefaultServeMux.Handler(&http.Request{URL: &url.URL{Path: "/health/liveness"}})
	readinessHandler, readinessPattern := http.DefaultServeMux.Handler(&http.Request{URL: &url.URL{Path: "/health/readiness"}})

	assert.NotNil(t, livenessHandler, "Liveness endpoint should be registered on main service mux")
	assert.Equal(t, "/health/liveness", livenessPattern, "Liveness endpoint pattern should match")
	assert.NotNil(t, readinessHandler, "Readiness endpoint should be registered on main service mux")
	assert.Equal(t, "/health/readiness", readinessPattern, "Readiness endpoint pattern should match")
}

// TestAC8_AllResponsesHaveJsonContentType tests AC-8: All health endpoint responses have Content-Type: application/json header
func TestAC8_AllResponsesHaveJsonContentType(t *testing.T) {
	// Test liveness success case
	req, _ := http.NewRequest("GET", "/health/liveness", nil)
	rr := httptest.NewRecorder()
	http.DefaultServeMux.ServeHTTP(rr, req)
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Liveness response should have JSON Content-Type")

	// Test readiness success case
	req, _ = http.NewRequest("GET", "/health/readiness", nil)
	rr = httptest.NewRecorder()
	http.DefaultServeMux.ServeHTTP(rr, req)
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Readiness response should have JSON Content-Type")

	// Test liveness failure case (simulate shutdown)
	// TODO: Add shutdown simulation when implementation exists
	req, _ = http.NewRequest("GET", "/health/liveness", nil)
	rr = httptest.NewRecorder()
	http.DefaultServeMux.ServeHTTP(rr, req)
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Liveness failure response should have JSON Content-Type")

	// Test readiness failure case (before catalog loaded)
	req, _ = http.NewRequest("GET", "/health/readiness", nil)
	rr = httptest.NewRecorder()
	http.DefaultServeMux.ServeHTTP(rr, req)
	assert.Equal(t, "application/json", rr.Header().Get("Content-Type"), "Readiness failure response should have JSON Content-Type")
}
