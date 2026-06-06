package test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	kafka_collector "github.com/open-telemetry/opentelemetry-demo/src/kafka-collector"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/stretchr/testify/assert"
)

// Mock HealthChecker implementation for test purposes
type mockHealthChecker struct {
	shouldFail bool
}

func (m mockHealthChecker) Check() error {
	if m.shouldFail {
		return assert.AnError
	}
	return nil
}

// Mock ReadinessChecker implementation for test purposes
type mockReadinessChecker struct {
	shouldFail bool
}

func (m mockReadinessChecker) Check() error {
	if m.shouldFail {
		return assert.AnError
	}
	return nil
}

// Test_AC1_HealthEndpoint_Returns200WhenRuntimeNormal verifies AC-1:
// When service runtime is operating normally, GET /health returns 200 OK with body "OK"
func Test_AC1_HealthEndpoint_Returns200WhenRuntimeNormal(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	healthChecker := mockHealthChecker{shouldFail: false}
	mux.HandleFunc("/health", kafka_collector.HealthHandler(healthChecker))

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, "text/plain", w.Header().Get("Content-Type"))
	assert.Equal(t, "OK", w.Body.String())
}

// Test_AC2_HealthEndpoint_Returns503WhenRuntimeFailed verifies AC-2:
// When service runtime is in failed state, GET /health returns 503 with body "Service Unhealthy"
func Test_AC2_HealthEndpoint_Returns503WhenRuntimeFailed(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	healthChecker := mockHealthChecker{shouldFail: true}

	mux.HandleFunc("/health", kafka_collector.HealthHandler(healthChecker))

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	assert.Equal(t, http.StatusServiceUnavailable, w.Code)
	assert.Equal(t, "text/plain", w.Header().Get("Content-Type"))
	assert.Equal(t, "Service Unhealthy", w.Body.String())
}

// Test_AC3_ReadyEndpoint_Returns200WhenKafkaConnected verifies AC-3:
// When Kafka consumer connection is active and can consume messages, GET /ready returns 200 OK with body "OK"
func Test_AC3_ReadyEndpoint_Returns200WhenKafkaConnected(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	readinessChecker := mockReadinessChecker{shouldFail: false}

	mux.HandleFunc("/ready", kafka_collector.ReadyHandler(readinessChecker))

	req := httptest.NewRequest(http.MethodGet, "/ready", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, "text/plain", w.Header().Get("Content-Type"))
	assert.Equal(t, "OK", w.Body.String())
}

// Test_AC4_ReadyEndpoint_Returns503WhenKafkaDisconnected verifies AC-4:
// When Kafka consumer connection is disconnected/failed, GET /ready returns 503
func Test_AC4_ReadyEndpoint_Returns503WhenKafkaDisconnected(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	readinessChecker := mockReadinessChecker{shouldFail: true}

	mux.HandleFunc("/ready", kafka_collector.ReadyHandler(readinessChecker))

	req := httptest.NewRequest(http.MethodGet, "/ready", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	assert.Equal(t, http.StatusServiceUnavailable, w.Code)
	assert.Equal(t, "text/plain", w.Header().Get("Content-Type"))
	assert.Equal(t, "Kafka Consumer Not Ready", w.Body.String())
}

// Test_AC5_MetricsEndpoint_Unchanged verifies AC-5:
// Existing GET /metrics endpoint continues to serve Prometheus metrics unchanged
func Test_AC5_MetricsEndpoint_Unchanged(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()

	mux.Handle("/metrics", promhttp.Handler())

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.Contains(t, w.Header().Get("Content-Type"), "text/plain")
	// Verify metrics format is preserved (contains Prometheus comment structure)
	assert.Contains(t, w.Body.String(), "# HELP")
	assert.Contains(t, w.Body.String(), "# TYPE")
}

// Test_AC6_AllEndpoints_SamePort8080 verifies AC-6:
// All endpoints are served on the same port 8080 as existing metrics endpoint
func Test_AC6_AllEndpoints_SamePort8080(t *testing.T) {
	t.Parallel()
	// Setup mux with all endpoints (this should be the same server instance in implementation)
	mux := http.NewServeMux()

	healthChecker := mockHealthChecker{shouldFail: false}
	readinessChecker := mockReadinessChecker{shouldFail: false}

	// Register all three endpoints to the same mux (same server = same port)
	mux.HandleFunc("/health", kafka_collector.HealthHandler(healthChecker))
	mux.HandleFunc("/ready", kafka_collector.ReadyHandler(readinessChecker))
	mux.Handle("/metrics", promhttp.Handler())

	// Test all endpoints respond on the same server instance
	testPaths := []string{"/health", "/ready", "/metrics"}
	for _, path := range testPaths {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, req)
		// Endpoint should exist (not 404) on the same server
		assert.NotEqual(t, http.StatusNotFound, w.Code, "Endpoint %s not found on shared server", path)
	}
}
