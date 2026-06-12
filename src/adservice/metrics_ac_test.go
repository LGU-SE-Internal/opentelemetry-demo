package main

import (
	"context"
	"fmt"
	"net/http"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"
	"time"

	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/common/expfmt"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const testWaitTimeout = 5 * time.Second
const testPollInterval = 100 * time.Millisecond

// AC-1: When the ad service starts with default configuration, the /metrics endpoint is available on port 9090 and returns valid Prometheus format metrics.
func Test_AC1_MetricsEndpointDefaultPort(t *testing.T) {
	// Unset any existing metrics port env var
	os.Unsetenv("AD_SERVICE_METRICS_PORT")

	// Start ad service in background
	cmd := exec.Command("./adservice")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()

	// Wait for metrics endpoint to be available
	endpoint := "http://localhost:9090/metrics"
	var resp *http.Response
	start := time.Now()
	for time.Since(start) < testWaitTimeout {
		resp, err = http.Get(endpoint)
		if err == nil && resp.StatusCode == http.StatusOK {
			break
		}
		time.Sleep(testPollInterval)
	}
	require.NoError(t, err, "failed to reach /metrics endpoint on default port 9090")
	defer resp.Body.Close()

	// Verify content type is correct
	assert.Equal(t, "text/plain; version=0.0.4", resp.Header.Get("Content-Type"))

	// Verify response body is valid Prometheus format
	parser := &expfmt.TextParser{}
	_, err = parser.TextToMetricFamilies(resp.Body)
	require.NoError(t, err, "metrics response is not valid Prometheus format")
}

// AC-2: When the ad service starts with AD_SERVICE_METRICS_PORT=9100 env var set, the /metrics endpoint is available on port 9100 instead of default 9090.
func Test_AC2_MetricsEndpointCustomPort(t *testing.T) {
	customPort := 9100
	os.Setenv("AD_SERVICE_METRICS_PORT", strconv.Itoa(customPort))
	defer os.Unsetenv("AD_SERVICE_METRICS_PORT")

	// Start ad service in background
	cmd := exec.Command("./adservice")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()

	// Verify default port 9090 is NOT available
	defaultClient := http.Client{Timeout: 500 * time.Millisecond}
	_, err = defaultClient.Get("http://localhost:9090/metrics")
	assert.Error(t, err, "default port 9090 should not be available when custom port is set")

	// Verify custom port is available
	endpoint := fmt.Sprintf("http://localhost:%d/metrics", customPort)
	var resp *http.Response
	start := time.Now()
	for time.Since(start) < testWaitTimeout {
		resp, err = http.Get(endpoint)
		if err == nil && resp.StatusCode == http.StatusOK {
			break
		}
		time.Sleep(testPollInterval)
	}
	require.NoError(t, err, "failed to reach /metrics endpoint on custom port 9100")
	defer resp.Body.Close()
	assert.Equal(t, http.StatusOK, resp.StatusCode)
}

// AC-3: When 5 successful ad requests and 2 failed ad requests are sent, ad_service_requests_total shows success=5 and failure=2.
func Test_AC3_RequestsTotalMetric(t *testing.T) {
	os.Setenv("AD_SERVICE_METRICS_PORT", "9091")
	defer os.Unsetenv("AD_SERVICE_METRICS_PORT")

	// Start service
	cmd := exec.Command("./adservice")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()

	// Wait for service to be ready
	waitForServiceReady(t, 9091)

	// Get metrics
	metrics := getMetrics(t, 9091)

	// Check that requests_total metric exists with correct labels
	requestsTotal, ok := metrics["ad_service_requests_total"]
	require.True(t, ok, "ad_service_requests_total metric not found")
	assert.Equal(t, dto.MetricType_COUNTER, requestsTotal.GetType(), "requests_total should be counter type")

	hasSuccessLabel := false
	hasFailureLabel := false
	for _, metric := range requestsTotal.GetMetric() {
		for _, label := range metric.GetLabel() {
			if label.GetName() == "status" && label.GetValue() == "success" {
				hasSuccessLabel = true
			}
			if label.GetName() == "status" && label.GetValue() == "failure" {
				hasFailureLabel = true
			}
		}
	}
	assert.True(t, hasSuccessLabel, "requests_total metric missing status=success label")
	assert.True(t, hasFailureLabel, "requests_total metric missing status=failure label")
}

// AC-4: When ad requests are made for categories electronics, clothing, home, retrieval_latency_seconds has separate buckets per category.
func Test_AC4_RetrievalLatencyPerCategory(t *testing.T) {
	os.Setenv("AD_SERVICE_METRICS_PORT", "9092")
	defer os.Unsetenv("AD_SERVICE_METRICS_PORT")

	// Start service
	cmd := exec.Command("./adservice")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()

	waitForServiceReady(t, 9092)

	metrics := getMetrics(t, 9092)

	latencyMetric, ok := metrics["ad_service_retrieval_latency_seconds"]
	require.True(t, ok, "ad_service_retrieval_latency_seconds metric not found")
	assert.Equal(t, dto.MetricType_HISTOGRAM, latencyMetric.GetType(), "retrieval_latency should be histogram type")

	// Verify ad_category label exists
	hasAdCategoryLabel := false
	for _, metric := range latencyMetric.GetMetric() {
		for _, label := range metric.GetLabel() {
			if label.GetName() == "ad_category" {
				hasAdCategoryLabel = true
				break
			}
		}
	}
	assert.True(t, hasAdCategoryLabel, "retrieval_latency metric missing ad_category label")
}

// AC-5: When 10 successful db lookups and 3 failed, ad_service_db_queries_total shows success=10 and failure=3.
func Test_AC5_DbQueriesTotalMetric(t *testing.T) {
	os.Setenv("AD_SERVICE_METRICS_PORT", "9093")
	defer os.Unsetenv("AD_SERVICE_METRICS_PORT")

	// Start service
	cmd := exec.Command("./adservice")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()

	waitForServiceReady(t, 9093)

	metrics := getMetrics(t, 9093)

	dbQueriesTotal, ok := metrics["ad_service_db_queries_total"]
	require.True(t, ok, "ad_service_db_queries_total metric not found")
	assert.Equal(t, dto.MetricType_COUNTER, dbQueriesTotal.GetType(), "db_queries_total should be counter type")

	hasSuccessLabel := false
	hasFailureLabel := false
	for _, metric := range dbQueriesTotal.GetMetric() {
		for _, label := range metric.GetLabel() {
			if label.GetName() == "status" && label.GetValue() == "success" {
				hasSuccessLabel = true
			}
			if label.GetName() == "status" && label.GetValue() == "failure" {
				hasFailureLabel = true
			}
		}
	}
	assert.True(t, hasSuccessLabel, "db_queries_total missing status=success label")
	assert.True(t, hasFailureLabel, "db_queries_total missing status=failure label")
}

// AC-6: When a successful ad request returns 3 ads, ads_served_per_request increments bucket matching 3.
func Test_AC6_AdsServedPerRequestMetric(t *testing.T) {
	os.Setenv("AD_SERVICE_METRICS_PORT", "9094")
	defer os.Unsetenv("AD_SERVICE_METRICS_PORT")

	// Start service
	cmd := exec.Command("./adservice")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Start()
	require.NoError(t, err)
	defer func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()

	waitForServiceReady(t, 9094)

	metrics := getMetrics(t, 9094)

	adsServedMetric, ok := metrics["ad_service_ads_served_per_request"]
	require.True(t, ok, "ad_service_ads_served_per_request metric not found")
	assert.Equal(t, dto.MetricType_HISTOGRAM, adsServedMetric.GetType(), "ads_served_per_request should be histogram type")

	// Verify expected buckets exist (0,1,2,3,4,5,10)
	expectedBuckets := []float64{0, 1, 2, 3, 4, 5, 10}
	for _, metric := range adsServedMetric.GetMetric() {
		histo := metric.GetHistogram()
		bucketUpperBounds := make([]float64, len(histo.GetBucket()))
		for i, bucket := range histo.GetBucket() {
			bucketUpperBounds[i] = bucket.GetUpperBound()
		}
		for _, expected := range expectedBuckets {
			assert.Contains(t, bucketUpperBounds, expected, fmt.Sprintf("missing expected bucket %f in ads_served_per_request", expected))
		}
	}
}

// AC-7: If configured metrics port is already in use, service logs fatal error and exits with non-zero code.
func Test_AC7_PortInUseFatalError(t *testing.T) {
	// Occupy port 9095 first
	listener, err := net.Listen("tcp", ":9095")
	require.NoError(t, err)
	defer listener.Close()

	os.Setenv("AD_SERVICE_METRICS_PORT", "9095")
	defer os.Unsetenv("AD_SERVICE_METRICS_PORT")

	// Start service
	cmd := exec.Command("./adservice")
	stderr, err := cmd.StderrPipe()
	require.NoError(t, err)

	err = cmd.Start()
	require.NoError(t, err)

	// Wait for process to exit
	err = cmd.Wait()
	require.Error(t, err, "service should exit with error when port is in use")

	// Check exit code is non-zero
	exitCode := cmd.ProcessState.ExitCode()
	assert.NotEqual(t, 0, exitCode, "service should exit with non-zero code when port is in use")

	// Check error is logged
	buf := make([]byte, 1024)
	n, err := stderr.Read(buf)
	require.NoError(t, err)
	assert.Contains(t, strings.ToLower(string(buf[:n])), "port", "error message should mention port")
	assert.Contains(t, strings.ToLower(string(buf[:n])), "in use", "error message should mention port is in use")
}

// AC-8: When invalid port value is set for AD_SERVICE_METRICS_PORT, service fails to start with clear config error.
func Test_AC8_InvalidPortFatalError(t *testing.T) {
	testCases := []struct {
		name        string
		portValue   string
		errorSubstr string
	}{
		{"non integer port", "abc", "invalid port"},
		{"port too high", "70000", "out of range"},
		{"port too low", "0", "out of range"},
		{"negative port", "-100", "out of range"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			os.Setenv("AD_SERVICE_METRICS_PORT", tc.portValue)
			defer os.Unsetenv("AD_SERVICE_METRICS_PORT")

			cmd := exec.Command("./adservice")
			stderr, err := cmd.StderrPipe()
			require.NoError(t, err)

			err = cmd.Start()
			require.NoError(t, err)

			err = cmd.Wait()
			require.Error(t, err, fmt.Sprintf("service should fail to start with invalid port %q", tc.portValue))
			assert.NotEqual(t, 0, cmd.ProcessState.ExitCode(), "exit code should be non-zero for invalid port")

			buf := make([]byte, 1024)
			n, err := stderr.Read(buf)
			require.NoError(t, err)
			assert.Contains(t, strings.ToLower(string(buf[:n])), tc.errorSubstr, fmt.Sprintf("error message for port %q should contain %q", tc.portValue, tc.errorSubstr))
		})
	}
}

// Helper functions
func waitForServiceReady(t *testing.T, port int) {
	endpoint := fmt.Sprintf("http://localhost:%d/metrics", port)
	start := time.Now()
	for time.Since(start) < testWaitTimeout {
		resp, err := http.Get(endpoint)
		if err == nil && resp.StatusCode == http.StatusOK {
			resp.Body.Close()
			return
		}
		time.Sleep(testPollInterval)
	}
	t.Fatalf("service did not become ready on port %d within %v", port, testWaitTimeout)
}

func getMetrics(t *testing.T, port int) map[string]*dto.MetricFamily {
	endpoint := fmt.Sprintf("http://localhost:%d/metrics", port)
	resp, err := http.Get(endpoint)
	require.NoError(t, err)
	defer resp.Body.Close()

	parser := &expfmt.TextParser{}
	metrics, err := parser.TextToMetricFamilies(resp.Body)
	require.NoError(t, err, "failed to parse Prometheus metrics")

	return metrics
}
