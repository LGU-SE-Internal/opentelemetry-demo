package main

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const testMetricsPort = 9465 // Use non-default port for testing

// AC-1: When PRODUCT_CATALOG_METRICS_PORT is set to valid port, GET /metrics returns 200 OK with correct content type
func TestAC1_MetricsEndpointServesCorrectResponse(t *testing.T) {
	// Set valid port env var
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))

	// Start metrics server
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err, "Expected metrics server to start successfully on valid port")

	// Wait for server to be up
	time.Sleep(100 * time.Millisecond)

	// Make request to /metrics endpoint
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err, "Expected to be able to connect to metrics server")
	defer resp.Body.Close()

	// Verify response status and content type
	assert.Equal(t, http.StatusOK, resp.StatusCode, "Expected 200 OK response from /metrics")
	assert.Equal(t, "text/plain; version=0.0.4", resp.Header.Get("Content-Type"), "Expected correct Prometheus content type")
}

// AC-2: Metrics endpoint contains otelsql database connection pool metrics
func TestAC2_DatabaseMetricsPresent(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err)
	time.Sleep(100 * time.Millisecond)

	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err)
	defer resp.Body.Close()

	bodyBytes, err := io.ReadAll(resp.Body)
	require.NoError(t, err)
	body := string(bodyBytes)

	// Verify required otelsql metrics exist
	assert.Contains(t, body, "sql_connection_pool_open_connections", "Expected sql_connection_pool_open_connections metric present")
	assert.Contains(t, body, "sql_connection_pool_usage", "Expected sql_connection_pool_usage metric present")
	assert.Contains(t, body, "sql_connection_pool_wait_count", "Expected sql_connection_pool_wait_count metric present")
}

// AC-3: product_search_requests_total counter increments with correct result_count_bucket labels
func TestAC3_SearchRequestCounterBuckets(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err)

	// Get counters from registry/collector
	var searchCounter SearchRequestCounter = getSearchRequestCounter() // Assume this is exported for testing

	// Test each bucket
	testCases := []struct {
		resultCount    int
		expectedBucket string
	}{
		{0, "0"},
		{5, "1-10"},
		{10, "1-10"},
		{11, "11-100"},
		{100, "11-100"},
		{101, "100+"},
		{500, "100+"},
	}

	for _, tc := range testCases {
		t.Run(fmt.Sprintf("bucket_%s", tc.expectedBucket), func(t *testing.T) {
			searchCounter.Inc(tc.resultCount)

			// Get metrics
			resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
			require.NoError(t, err)
			defer resp.Body.Close()
			body, _ := io.ReadAll(resp.Body)

			// Verify metric with correct label exists
			expectedLine := fmt.Sprintf(`product_search_requests_total{result_count_bucket="%s"`, tc.expectedBucket)
			assert.Contains(t, string(body), expectedLine, "Expected search counter with bucket %s", tc.expectedBucket)
		})
	}
}

// AC-4: product_view_requests_total counter increments with correct product_id label
func TestAC4_ProductViewCounterLabels(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err)

	var viewCounter ProductViewCounter = getProductViewCounter()
	testProductID := "test-product-123"

	// Increment counter
	viewCounter.Inc(testProductID)

	// Get metrics
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err)
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	// Verify metric with product ID label exists
	expectedLine := fmt.Sprintf(`product_view_requests_total{product_id="%s"`, testProductID)
	assert.Contains(t, string(body), expectedLine, "Expected view counter with product_id %s", testProductID)
}

// AC-5: catalog_load_operations_total counter increments with correct status labels
func TestAC5_CatalogLoadCounterStatus(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err)

	var loadCounter CatalogLoadCounter = getCatalogLoadCounter()

	// Test success case
	loadCounter.Inc(true)
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err)
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	assert.Contains(t, string(body), `catalog_load_operations_total{status="success"`, "Expected success catalog load counter")

	// Test failure case
	loadCounter.Inc(false)
	resp, err = http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err)
	body, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	assert.Contains(t, string(body), `catalog_load_operations_total{status="failure"`, "Expected failure catalog load counter")
}

// AC-6: Metrics endpoint contains standard Go runtime metrics
func TestAC6_GoRuntimeMetricsPresent(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err)
	time.Sleep(100 * time.Millisecond)

	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err)
	defer resp.Body.Close()
	bodyBytes, _ := io.ReadAll(resp.Body)
	body := string(bodyBytes)

	// Verify required runtime metrics exist
	requiredMetrics := []string{
		"go_goroutines",
		"go_memstats_alloc_bytes",
		"go_memstats_gc_cpu_fraction",
		"go_memstats_heap_alloc_bytes",
	}
	for _, metric := range requiredMetrics {
		assert.Contains(t, body, metric, "Expected runtime metric %s present", metric)
	}
}

// AC-7: All metrics follow OpenTelemetry semantic conventions
func TestAC7_MetricsFollowSemanticConventions(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err)
	time.Sleep(100 * time.Millisecond)

	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err)
	defer resp.Body.Close()
	bodyBytes, _ := io.ReadAll(resp.Body)
	lines := strings.Split(string(bodyBytes), "\n")

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue // Skip comments and empty lines
		}

		// Check metric name is snake_case (no uppercase letters)
		metricName := strings.Split(line, "{")[0]
		assert.Equal(t, strings.ToLower(metricName), metricName, "Metric name %s is not snake_case", metricName)

		// Check all label names are snake_case
		if strings.Contains(line, "{") && strings.Contains(line, "}") {
			labelsPart := strings.SplitN(line, "{", 2)[1]
			labelsPart = strings.SplitN(labelsPart, "}", 2)[0]
			labels := strings.Split(labelsPart, ",")
			for _, label := range labels {
				if label == "" {
					continue
				}
				labelName := strings.SplitN(label, "=", 2)[0]
				assert.Equal(t, strings.ToLower(labelName), labelName, "Label name %s for metric %s is not snake_case", labelName, metricName)
			}
		}

		// Check service name label exists and is correct
		assert.Contains(t, line, `service_name="product-catalog"`, "Metric %s missing service_name label with value product-catalog", metricName)
	}

	// Check unit labels exist for applicable metrics
	assert.Contains(t, string(bodyBytes), `unit="bytes"`, "Expected unit label for memory metrics")
}

// AC-8: Invalid or used port causes service startup error and exit
func TestAC8_PortConflictCausesStartupError(t *testing.T) {
	// Start a dummy server to occupy the test port
	listener, err := net.Listen("tcp", fmt.Sprintf(":%d", testMetricsPort))
	require.NoError(t, err, "Failed to start dummy server to occupy port")
	defer listener.Close()

	// Try to start metrics server on same port
	err = StartMetricsServer(testMetricsPort)
	assert.Error(t, err, "Expected error when starting metrics server on occupied port")

	// Test invalid port number (<=0 or >65535)
	invalidPorts := []int{-1, 0, 65536, 70000}
	for _, port := range invalidPorts {
		t.Run(fmt.Sprintf("invalid_port_%d", port), func(t *testing.T) {
			err := StartMetricsServer(port)
			assert.Error(t, err, "Expected error when using invalid port %d", port)
		})
	}
}

// AC-9: Metrics server runs independently of main API server
func TestAC9_MetricsServerIndependentOfMainAPI(t *testing.T) {
	t.Setenv("PRODUCT_CATALOG_METRICS_PORT", fmt.Sprintf("%d", testMetricsPort))
	err := StartMetricsServer(testMetricsPort)
	require.NoError(t, err)
	time.Sleep(100 * time.Millisecond)

	// Verify metrics works first
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, resp.StatusCode)
	resp.Body.Close()

	// Simulate main API server being down (stop main server if running)
	stopMainAPIServer() // Assume this function stops the main service API server

	// Verify metrics endpoint still works
	resp, err = http.Get(fmt.Sprintf("http://localhost:%d/metrics", testMetricsPort))
	assert.NoError(t, err, "Expected metrics server to still be reachable after main API server stops")
	assert.Equal(t, http.StatusOK, resp.StatusCode, "Expected 200 OK from metrics after main API stops")
	resp.Body.Close()
}
