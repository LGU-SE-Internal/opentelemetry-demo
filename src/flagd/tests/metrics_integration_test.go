package tests

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/common/expfmt"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const (
	metricsEndpoint = "http://localhost:8016/metrics"
	evaluationEndpoint = "http://localhost:8080/flags/v1/evaluate"
)

// AC-1: When making a GET request to http://<service>:8016/metrics, the endpoint returns a 200 OK status code with Content-Type header matching text/plain; version=0.0.4
func TestAC1_MetricsEndpointReturns200WithCorrectContentType(t *testing.T) {
	t.Parallel()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, metricsEndpoint, nil)
	require.NoError(t, err)

	resp, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer resp.Body.Close()

	assert.Equal(t, http.StatusOK, resp.StatusCode)
	assert.Equal(t, "text/plain; version=0.0.4", resp.Header.Get("Content-Type"))
}

// AC-2: The /metrics endpoint includes the feature_flag.evaluation.requests.total counter metric, which increments by 1 for every feature flag evaluation request processed (success or failure)
func TestAC2_EvaluationRequestsTotalCounterIncrements(t *testing.T) {
	t.Parallel()
	// Get initial metric value
	before := getMetricValue(t, "feature_flag.evaluation.requests.total", nil)

	// Make one evaluation request
	makeEvaluationRequest(t, `{"flagKey": "test-flag", "context": {}}`, http.StatusOK)

	// Get metric value after request
	after := getMetricValue(t, "feature_flag.evaluation.requests.total", nil)

	// Counter should have increased by 1
	assert.Equal(t, before+1, after, "feature_flag.evaluation.requests.total should increment by 1 per evaluation request")
}

// AC-3: The /metrics endpoint includes the feature_flag.evaluation.errors.total counter metric, which increments by 1 with the correct error_type label for every failed feature flag evaluation
func TestAC3_EvaluationErrorsTotalCounterIncrementsWithCorrectErrorType(t *testing.T) {
	t.Parallel()
	testCases := []struct {
		name           string
		requestBody    string
		expectedStatus int
		expectedError  string
	}{
		{
			name:           "flag_not_found error",
			requestBody:    `{"flagKey": "non-existent-flag-12345", "context": {}}`,
			expectedStatus: http.StatusNotFound,
			expectedError:  "flag_not_found",
		},
		{
			name:           "invalid_flag_key error",
			requestBody:    `{"flagKey": "", "context": {}}`,
			expectedStatus: http.StatusBadRequest,
			expectedError:  "invalid_flag_key",
		},
		{
			name:           "invalid_context error",
			requestBody:    `{"flagKey": "test-flag", "context": "invalid-context-type"}`,
			expectedStatus: http.StatusBadRequest,
			expectedError:  "invalid_context",
		},
	}

	for _, tc := range testCases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			labels := map[string]string{"error_type": tc.expectedError}
			before := getMetricValue(t, "feature_flag.evaluation.errors.total", labels)

			makeEvaluationRequest(t, tc.requestBody, tc.expectedStatus)

			after := getMetricValue(t, "feature_flag.evaluation.errors.total", labels)
			assert.Equal(t, before+1, after, fmt.Sprintf("feature_flag.evaluation.errors.total with error_type=%s should increment by 1", tc.expectedError))
		})
	}
}

// AC-4: The /metrics endpoint includes the feature_flag.evaluation.duration histogram metric, which records time (in milliseconds) taken for each feature flag evaluation request
func TestAC4_EvaluationDurationHistogramExistsAndRecordsLatency(t *testing.T) {
	t.Parallel()
	// Make several evaluation requests to ensure histogram has samples
	for i := 0; i < 10; i++ {
		makeEvaluationRequest(t, `{"flagKey": "test-flag", "context": {}}`, http.StatusOK)
	}

	// Check histogram exists with sum and count > 0
	count := getMetricValue(t, "feature_flag.evaluation.duration_count", nil)
	sum := getMetricValue(t, "feature_flag.evaluation.duration_sum", nil)

	assert.Greater(t, count, float64(0), "feature_flag.evaluation.duration_count should be > 0 after evaluations")
	assert.Greater(t, sum, float64(0), "feature_flag.evaluation.duration_sum should be > 0 after evaluations")
}

// AC-5: The /metrics endpoint includes the feature_flag.configs.active gauge metric, which correctly reports the current number of loaded active feature flag configurations at all times
func TestAC5_ActiveConfigsGaugeReportsCorrectCount(t *testing.T) {
	t.Parallel()
	// Expected count from demo.flagd.json in the repo
	expectedActiveConfigs := float64(15) // TODO: replace with actual count from demo config if different

	gaugeValue := getMetricValue(t, "feature_flag.configs.active", nil)
	assert.Equal(t, expectedActiveConfigs, gaugeValue, "feature_flag.configs.active should report correct number of loaded active configurations")
}

// AC-6: The /metrics endpoint includes the http.server.requests.total counter metric, which increments with method, path, and status_code labels for every request made to any service endpoint (including /metrics itself)
func TestAC6_HttpServerRequestsTotalCounterIncrementsForAllEndpoints(t *testing.T) {
	t.Parallel()
	testCases := []struct {
		name           string
		path           string
		method         string
		expectedStatus int
	}{
		{
			name:           "GET /metrics endpoint",
			path:           "/metrics",
			method:         http.MethodGet,
			expectedStatus: http.StatusOK,
		},
		{
			name:           "POST /flags/v1/evaluate endpoint",
			path:           "/flags/v1/evaluate",
			method:         http.MethodPost,
			expectedStatus: http.StatusOK,
		},
		{
			name:           "GET non-existent endpoint (404)",
			path:           "/non-existent-path",
			method:         http.MethodGet,
			expectedStatus: http.StatusNotFound,
		},
	}

	for _, tc := range testCases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			labels := map[string]string{
				"method": tc.method,
				"path": tc.path,
				"status_code": fmt.Sprintf("%d", tc.expectedStatus),
			}
			before := getMetricValue(t, "http.server.requests.total", labels)

			// Make request to the endpoint
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			var req *http.Request
			var err error
			if tc.method == http.MethodPost {
				req, err = http.NewRequestWithContext(ctx, tc.method, "http://localhost:8080"+tc.path, strings.NewReader(`{"flagKey": "test-flag", "context": {}}`))
				req.Header.Set("Content-Type", "application/json")
			} else if tc.path == "/metrics" {
				req, err = http.NewRequestWithContext(ctx, tc.method, metricsEndpoint, nil)
			} else {
				req, err = http.NewRequestWithContext(ctx, tc.method, "http://localhost:8080"+tc.path, nil)
			}
			require.NoError(t, err)

			resp, err := http.DefaultClient.Do(req)
			require.NoError(t, err)
			defer resp.Body.Close()
			assert.Equal(t, tc.expectedStatus, resp.StatusCode)

			// Check counter incremented
			after := getMetricValue(t, "http.server.requests.total", labels)
			assert.Equal(t, before+1, after, fmt.Sprintf("http.server.requests.total with labels %v should increment by 1", labels))
		})
	}
}

// AC-7: All published metrics follow OpenTelemetry semantic conventions for feature flag and HTTP server metrics where defined
func TestAC7_MetricsFollowOpenTelemetrySemanticConventions(t *testing.T) {
	t.Parallel()
	// Fetch all metrics
	resp, err := http.Get(metricsEndpoint)
	require.NoError(t, err)
	defer resp.Body.Close()

	parser := expfmt.TextParser{}
	metricFamilies, err := parser.TextToMetricFamilies(resp.Body)
	require.NoError(t, err)

	// Check feature flag metrics match OTel convention names
	requiredFeatureFlagMetrics := []string{
		"feature_flag.evaluation.requests.total",
		"feature_flag.evaluation.errors.total",
		"feature_flag.evaluation.duration",
		"feature_flag.configs.active",
	}
	for _, metricName := range requiredFeatureFlagMetrics {
		_, exists := metricFamilies[metricName]
		assert.True(t, exists, fmt.Sprintf("Metric %s should exist following OTel feature flag conventions", metricName))
	}

	// Check HTTP server metrics match OTel convention names
	_, exists := metricFamilies["http.server.requests.total"]
	assert.True(t, exists, "Metric http.server.requests.total should exist following OTel HTTP server conventions")
}

// AC-8: The service continues to function normally and serve feature flag evaluation requests without degradation when metrics collection is enabled
func TestAC8_ServiceFunctionalityUnaffectedByMetricsCollection(t *testing.T) {
	t.Parallel()
	// Make 100 concurrent evaluation requests to ensure no degradation
	concurrency := 100
	successCount := 0
	errCount := 0

	done := make(chan bool, concurrency)
	for i := 0; i < concurrency; i++ {
		go func() {
			defer func() { done <- true }()
			resp, err := http.Post(evaluationEndpoint, "application/json", strings.NewReader(`{"flagKey": "test-flag", "context": {"user": "test"}}`))
			if err != nil {
				errCount++
				return
			}
			defer resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				successCount++
			} else {
				errCount++
			}
		}()
	}

	// Wait for all requests to complete
	for i := 0; i < concurrency; i++ {
		<-done
	}

	// All requests should succeed
	assert.Equal(t, 0, errCount, "No evaluation requests should fail when metrics are enabled")
	assert.Equal(t, concurrency, successCount, "All evaluation requests should succeed when metrics are enabled")
}

// Helper function to get metric value from Prometheus endpoint
func getMetricValue(t *testing.T, metricName string, labels map[string]string) float64 {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, metricsEndpoint, nil)
	require.NoError(t, err)

	resp, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	require.NoError(t, err)

	parser := expfmt.TextParser{}
	metricFamilies, err := parser.TextToMetricFamilies(strings.NewReader(string(body)))
	require.NoError(t, err)

	metricFamily, exists := metricFamilies[metricName]
	if !exists {
		return 0
	}

	for _, metric := range metricFamily.Metric {
		match := true
		for k, v := range labels {
			found := false
			for _, labelPair := range metric.Label {
				if labelPair.GetName() == k && labelPair.GetValue() == v {
					found = true
					break
				}
			}
			if !found {
				match = false
				break
			}
		}
		if match {
			if metricFamily.GetType() == expfmt.MetricType_COUNTER {
				return metric.Counter.GetValue()
			} else if metricFamily.GetType() == expfmt.MetricType_GAUGE {
				return metric.Gauge.GetValue()
			} else if metricFamily.GetType() == expfmt.MetricType_HISTOGRAM {
				if strings.HasSuffix(metricName, "_count") {
					return float64(metric.Histogram.GetSampleCount())
				} else if strings.HasSuffix(metricName, "_sum") {
					return metric.Histogram.GetSampleSum()
				}
			}
		}
	}

	return 0
}

// Helper function to make an evaluation request
func makeEvaluationRequest(t *testing.T, body string, expectedStatus int) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, evaluationEndpoint, strings.NewReader(body))
	require.NoError(t, err)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer resp.Body.Close()
	assert.Equal(t, expectedStatus, resp.StatusCode)
}
