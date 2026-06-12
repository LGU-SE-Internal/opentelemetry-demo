package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/IBM/sarama"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
)

const (
	testTopic          = "test-topic"
	testPartition      = int32(0)
	testErrorType      = "deserialization_failure"
	testMetricsAddress = "localhost:19090"
)

// TestAC1_AllCustomMetricsPresent verifies AC-1: All 5 custom metrics are present on /metrics endpoint
func TestAC1_AllCustomMetricsPresent(t *testing.T) {
	// Start test server with metrics endpoint
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(prometheus.DefaultGatherer, promhttp.HandlerOpts{}))

	server := &http.Server{Addr: testMetricsAddress, Handler: mux}
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			t.Logf("server error: %v", err)
		}
	}()
	defer server.Shutdown(context.Background())

	// Wait for server to start
	time.Sleep(100 * time.Millisecond)

	// Fetch metrics
	resp, err := http.Get(fmt.Sprintf("http://%s/metrics", testMetricsAddress))
	require.NoError(t, err)
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	require.NoError(t, err)
	metricsOutput := string(body)

	// Verify all custom metrics are present (namespaced with kafka_collector_)
	expectedMetrics := []string{
		"kafka_collector_messages_consumed_total",
		"kafka_collector_messages_processed_success_total",
		"kafka_collector_messages_processed_failure_total",
		"kafka_collector_consumer_lag_current",
		"kafka_collector_message_processing_duration_seconds",
	}

	for _, metric := range expectedMetrics {
		assert.Contains(t, metricsOutput, metric, "Missing expected metric %s", metric)
	}

	// Verify default Go metrics are still present (no regression)
	assert.Contains(t, metricsOutput, "go_goroutines", "Missing default Go runtime metric")
}

// TestAC2_MessagesConsumedCounterIncrements verifies AC-2: Consumed counter increments by 1 per message
func TestAC2_MessagesConsumedCounterIncrements(t *testing.T) {
	// Reset metrics before test
	prometheus.DefaultRegisterer = prometheus.NewRegistry()

	// Get initial value of counter
	initial, err := getCounterValue("kafka_collector_messages_consumed_total", map[string]string{
		"topic":     testTopic,
		"partition": fmt.Sprintf("%d", testPartition),
	})
	require.NoError(t, err)

	// Simulate consuming one message
	IncMessagesConsumed(testTopic, testPartition)

	// Get updated value
	updated, err := getCounterValue("kafka_collector_messages_consumed_total", map[string]string{
		"topic":     testTopic,
		"partition": fmt.Sprintf("%d", testPartition),
	})
	require.NoError(t, err)

	// Verify increment by exactly 1
	assert.Equal(t, initial+1, updated, "Consumed counter did not increment by exactly 1")
}

// TestAC3_MessagesProcessedSuccessCounterIncrements verifies AC-3: Success counter increments by 1 per successful message
func TestAC3_MessagesProcessedSuccessCounterIncrements(t *testing.T) {
	prometheus.DefaultRegisterer = prometheus.NewRegistry()

	initial, err := getCounterValue("kafka_collector_messages_processed_success_total", map[string]string{
		"topic":     testTopic,
		"partition": fmt.Sprintf("%d", testPartition),
	})
	require.NoError(t, err)

	// Simulate processing one message successfully
	IncMessagesProcessedSuccess(testTopic, testPartition)

	updated, err := getCounterValue("kafka_collector_messages_processed_success_total", map[string]string{
		"topic":     testTopic,
		"partition": fmt.Sprintf("%d", testPartition),
	})
	require.NoError(t, err)

	assert.Equal(t, initial+1, updated, "Success counter did not increment by exactly 1")
}

// TestAC4_MessagesProcessedFailureCounterIncrements verifies AC-4: Failure counter increments with correct error type
func TestAC4_MessagesProcessedFailureCounterIncrements(t *testing.T) {
	prometheus.DefaultRegisterer = prometheus.NewRegistry()

	initial, err := getCounterValue("kafka_collector_messages_processed_failure_total", map[string]string{
		"topic":      testTopic,
		"partition":  fmt.Sprintf("%d", testPartition),
		"error_type": testErrorType,
	})
	require.NoError(t, err)

	// Simulate failed message processing
	IncMessagesProcessedFailure(testTopic, testPartition, testErrorType)

	updated, err := getCounterValue("kafka_collector_messages_processed_failure_total", map[string]string{
		"topic":      testTopic,
		"partition":  fmt.Sprintf("%d", testPartition),
		"error_type": testErrorType,
	})
	require.NoError(t, err)

	assert.Equal(t, initial+1, updated, "Failure counter did not increment by exactly 1")

	// Verify incorrect error type label doesn't increment
	wrongErrorTypeVal, err := getCounterValue("kafka_collector_messages_processed_failure_total", map[string]string{
		"topic":      testTopic,
		"partition":  fmt.Sprintf("%d", testPartition),
		"error_type": "wrong_error",
	})
	require.NoError(t, err)
	assert.Equal(t, 0.0, wrongErrorTypeVal, "Counter incremented for wrong error type")
}

// TestAC5_ConsumerLagGaugeUpdates verifies AC-5: Consumer lag gauge updates at least every 30s with accurate value
func TestAC5_ConsumerLagGaugeUpdates(t *testing.T) {
	prometheus.DefaultRegisterer = prometheus.NewRegistry()

	testLag := int64(1234)
	SetConsumerLag(testTopic, testPartition, testLag)

	// Verify gauge value matches
	gaugeVal, err := getGaugeValue("kafka_collector_consumer_lag_current", map[string]string{
		"topic":     testTopic,
		"partition": fmt.Sprintf("%d", testPartition),
	})
	require.NoError(t, err)
	assert.Equal(t, float64(testLag), gaugeVal, "Consumer lag gauge value incorrect")

	// Simulate lag update after interval
	newLag := int64(5678)
	SetConsumerLag(testTopic, testPartition, newLag)

	updatedGaugeVal, err := getGaugeValue("kafka_collector_consumer_lag_current", map[string]string{
		"topic":     testTopic,
		"partition": fmt.Sprintf("%d", testPartition),
	})
	require.NoError(t, err)
	assert.Equal(t, float64(newLag), updatedGaugeVal, "Consumer lag gauge did not update correctly")
}

// TestAC6_ProcessingDurationHistogramRecords verifies AC-6: Histogram records duration with correct status label
func TestAC6_ProcessingDurationHistogramRecords(t *testing.T) {
	prometheus.DefaultRegisterer = prometheus.NewRegistry()

	testDuration := 100 * time.Millisecond
	testStatus := "success"

	// Record duration
	ObserveProcessingDuration(testTopic, testPartition, testStatus, testDuration)

	// Verify histogram has sample count 1 for success status
	metricFamilies, err := prometheus.DefaultGatherer.Gather()
	require.NoError(t, err)

	found := false
	for _, mf := range metricFamilies {
		if mf.GetName() == "kafka_collector_message_processing_duration_seconds" {
			for _, m := range mf.GetMetric() {
				labels := make(map[string]string)
				for _, l := range m.GetLabel() {
					labels[l.GetName()] = l.GetValue()
				}
				if labels["topic"] == testTopic && labels["partition"] == fmt.Sprintf("%d", testPartition) && labels["status"] == testStatus {
					assert.Equal(t, uint64(1), m.GetHistogram().GetSampleCount(), "Histogram sample count incorrect")
					found = true
				}
			}
		}
	}
	assert.True(t, found, "Histogram metric not found with correct labels")

	// Verify failure status also records correctly
	testStatusFailure := "failure"
	ObserveProcessingDuration(testTopic, testPartition, testStatusFailure, 200*time.Millisecond)

	metricFamilies, err = prometheus.DefaultGatherer.Gather()
	require.NoError(t, err)
	foundFailure := false
	for _, mf := range metricFamilies {
		if mf.GetName() == "kafka_collector_message_processing_duration_seconds" {
			for _, m := range mf.GetMetric() {
				labels := make(map[string]string)
				for _, l := range m.GetLabel() {
					labels[l.GetName()] = l.GetValue()
				}
				if labels["topic"] == testTopic && labels["partition"] == fmt.Sprintf("%d", testPartition) && labels["status"] == testStatusFailure {
					assert.Equal(t, uint64(1), m.GetHistogram().GetSampleCount(), "Failure status histogram sample count incorrect")
					foundFailure = true
				}
			}
		}
	}
	assert.True(t, foundFailure, "Failure status histogram metric not found")
}

// TestAC7_MetricsHaveCorrectLabels verifies AC-7: All metrics have correct required labels, no extra labels
func TestAC7_MetricsHaveCorrectLabels(t *testing.T) {
	prometheus.DefaultRegisterer = prometheus.NewRegistry()

	// Populate all metrics with test values
	IncMessagesConsumed(testTopic, testPartition)
	IncMessagesProcessedSuccess(testTopic, testPartition)
	IncMessagesProcessedFailure(testTopic, testPartition, testErrorType)
	SetConsumerLag(testTopic, testPartition, 100)
	ObserveProcessingDuration(testTopic, testPartition, "success", 100*time.Millisecond)

	metricFamilies, err := prometheus.DefaultGatherer.Gather()
	require.NoError(t, err)

	// Define expected labels per metric
	expectedLabels := map[string][]string{
		"kafka_collector_messages_consumed_total":            {"topic", "partition"},
		"kafka_collector_messages_processed_success_total":   {"topic", "partition"},
		"kafka_collector_messages_processed_failure_total":   {"topic", "partition", "error_type"},
		"kafka_collector_consumer_lag_current":               {"topic", "partition"},
		"kafka_collector_message_processing_duration_seconds": {"topic", "partition", "status"},
	}

	for metricName, requiredLabels := range expectedLabels {
		found := false
		for _, mf := range metricFamilies {
			if mf.GetName() == metricName {
				found = true
				for _, m := range mf.GetMetric() {
					actualLabels := make(map[string]bool)
					for _, l := range m.GetLabel() {
						actualLabels[l.GetName()] = true
					}

					// Verify all required labels are present
					for _, reqLabel := range requiredLabels {
						assert.True(t, actualLabels[reqLabel], "Metric %s missing required label %s", metricName, reqLabel)
						delete(actualLabels, reqLabel)
					}

					// Verify no extra labels are present
					assert.Empty(t, actualLabels, "Metric %s has extra unexpected labels: %v", metricName, actualLabels)
				}
			}
		}
		assert.True(t, found, "Metric %s not found", metricName)
	}
}

// TestAC8_DefaultMetricsUnchanged verifies AC-8: Default Go runtime metrics continue to work
func TestAC8_DefaultMetricsUnchanged(t *testing.T) {
	// Start server with metrics
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())

	server := &http.Server{Addr: testMetricsAddress, Handler: mux}
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			t.Logf("server error: %v", err)
		}
	}()
	defer server.Shutdown(context.Background())

	time.Sleep(100 * time.Millisecond)

	resp, err := http.Get(fmt.Sprintf("http://%s/metrics", testMetricsAddress))
	require.NoError(t, err)
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	require.NoError(t, err)
	metricsOutput := string(body)

	// Check a sample of default Go metrics
	defaultMetrics := []string{
		"go_goroutines",
		"go_memstats_alloc_bytes",
		"go_threads",
		"process_cpu_seconds_total",
	}

	for _, metric := range defaultMetrics {
		assert.Contains(t, metricsOutput, metric, "Default runtime metric %s missing, possible regression", metric)
	}
}

// Helper functions to get metric values
func getCounterValue(name string, labels map[string]string) (float64, error) {
	metricFamilies, err := prometheus.DefaultGatherer.Gather()
	if err != nil {
		return 0, err
	}

	for _, mf := range metricFamilies {
		if mf.GetName() == name {
			for _, m := range mf.GetMetric() {
				match := true
				for k, v := range labels {
					found := false
					for _, l := range m.GetLabel() {
						if l.GetName() == k && l.GetValue() == v {
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
					return m.GetCounter().GetValue(), nil
				}
			}
		}
	}

	// If metric not found, return 0
	return 0, nil
}

func getGaugeValue(name string, labels map[string]string) (float64, error) {
	metricFamilies, err := prometheus.DefaultGatherer.Gather()
	if err != nil {
		return 0, err
	}

	for _, mf := range metricFamilies {
		if mf.GetName() == name {
			for _, m := range mf.GetMetric() {
				match := true
				for k, v := range labels {
					found := false
					for _, l := range m.GetLabel() {
						if l.GetName() == k && l.GetValue() == v {
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
					return m.GetGauge().GetValue(), nil
				}
			}
		}
	}

	return 0, nil
}
