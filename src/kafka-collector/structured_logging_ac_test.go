package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"testing"

	"go.opentelemetry.io/otel/trace"
)

// Logger interface from spec
type Logger interface {
	Debug(ctx context.Context, msg string, fields ...any)
	Info(ctx context.Context, msg string, fields ...any)
	Warn(ctx context.Context, msg string, fields ...any)
	Error(ctx context.Context, msg string, fields ...any)
}

// AC-1: All occurrences of Go standard library log.Print*/log.Fatal* calls replaced
func TestAC1_NoStandardLogCalls(t *testing.T) {
	t.Parallel()

	// Grep source code for log. calls excluding test files
	cmd := exec.Command("grep", "-r", `"log"`, "./", "--include=*.go", "--exclude=*_test.go")
	output, err := cmd.CombinedOutput()
	if err == nil && len(output) > 0 {
		t.Fatalf("Found standard log package imports: %s", string(output))
	}

	cmd = exec.Command("grep", "-r", `log\.`, "./", "--include=*.go", "--exclude=*_test.go")
	output, err = cmd.CombinedOutput()
	if err == nil && len(output) > 0 {
		t.Fatalf("Found standard log calls: %s", string(output))
	}
}

// AC-2: Log entries include trace_id and span_id when in traced context
func TestAC2_LogsIncludeTraceSpanIdsInTracedContext(t *testing.T) {
	t.Parallel()

	// Create test trace context
	traceID, _ := trace.TraceIDFromHex("00000000000000000000000000000001")
	spanID, _ := trace.SpanIDFromHex("0000000000000001")
	sc := trace.NewSpanContext(trace.SpanContextConfig{
		TraceID: traceID,
		SpanID:  spanID,
		TraceFlags: trace.TraceFlags(0x1),
	})
	ctx := trace.ContextWithSpanContext(context.Background(), sc)

	// Capture log output
	var logBuffer bytes.Buffer
	// TODO: Inject buffer into logger when implementation exists
	// This test will fail until logger is implemented

	// Simulate logging in traced context
	// logger.Info(ctx, "test message")

	// Parse log entry
	var logEntry map[string]interface{}
	err := json.Unmarshal(logBuffer.Bytes(), &logEntry)
	if err != nil {
		t.Fatalf("Failed to parse log entry: %v", err)
	}

	// Verify trace and span IDs
	if logEntry["trace_id"] != traceID.String() {
		t.Errorf("Expected trace_id %s, got %v", traceID.String(), logEntry["trace_id"])
	}
	if logEntry["span_id"] != spanID.String() {
		t.Errorf("Expected span_id %s, got %v", spanID.String(), logEntry["span_id"])
	}
}

// AC-3: Kafka processing logs include metadata fields
func TestAC3_KafkaLogsIncludeMetadataFields(t *testing.T) {
	t.Parallel()

	testTopic := "test-topic"
	var testPartition int32 = 2
	var testOffset int64 = 12345
	testConsumerGroup := "test-group"

	// Capture log output during message processing
	var logBuffer bytes.Buffer
	// TODO: Process test message with above metadata, capture logs
	// This test will fail until implementation exists

	// Parse all log entries
	decoder := json.NewDecoder(&logBuffer)
	foundProcessingLog := false
	for decoder.More() {
		var logEntry map[string]interface{}
		err := decoder.Decode(&logEntry)
		if err != nil {
			t.Fatalf("Invalid log entry: %v", err)
		}

		// Check for processing related log
		if strings.Contains(fmt.Sprintf("%v", logEntry["msg"]), "processed") || 
		   strings.Contains(fmt.Sprintf("%v", logEntry["msg"]), "consuming") {
			foundProcessingLog = true
			// Validate all required metadata fields
			if logEntry["kafka_topic"] != testTopic {
				t.Errorf("Expected kafka_topic %s, got %v", testTopic, logEntry["kafka_topic"])
			}
			if logEntry["kafka_partition"] != float64(testPartition) { // JSON numbers are float64
				t.Errorf("Expected kafka_partition %d, got %v", testPartition, logEntry["kafka_partition"])
			}
			if logEntry["kafka_offset"] != float64(testOffset) {
				t.Errorf("Expected kafka_offset %d, got %v", testOffset, logEntry["kafka_offset"])
			}
			if logEntry["consumer_group_id"] != testConsumerGroup {
				t.Errorf("Expected consumer_group_id %s, got %v", testConsumerGroup, logEntry["consumer_group_id"])
			}
		}
	}

	if !foundProcessingLog {
		t.Fatal("No Kafka processing log entries found")
	}
}

// AC-4: All logs have valid severity level
func TestAC4_AllLogsHaveValidSeverityLevel(t *testing.T) {
	t.Parallel()

	allowedLevels := map[string]bool{
		"debug": true,
		"info":  true,
		"warn":  true,
		"error": true,
	}

	// Capture all log output during service operation
	var logBuffer bytes.Buffer
	// TODO: Capture all log output
	// This test will fail until implementation exists

	// Parse all log entries
	decoder := json.NewDecoder(&logBuffer)
	for decoder.More() {
		var logEntry map[string]interface{}
		err := decoder.Decode(&logEntry)
		if err != nil {
			t.Fatalf("Invalid log entry: %v", err)
		}

		level, ok := logEntry["level"].(string)
		if !ok {
			t.Fatalf("Log entry missing valid severity level field: %v", logEntry)
		}
		if !allowedLevels[strings.ToLower(level)] {
			t.Errorf("Invalid severity level %s, must be one of debug, info, warn, error", level)
		}
	}
}

// AC-5: Existing log message content preserved
func TestAC5_ExistingLogMessagesPreserved(t *testing.T) {
	t.Parallel()

	// Extract all original log messages from current main.go
	cmd := exec.Command("grep", "-oP", `log\.(Print|Fatal).*?\"(.*?)\"`, "./main.go")
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("Failed to extract original log messages: %v", err)
	}

	originalMessages := make(map[string]bool)
	lines := strings.Split(string(output), "\n")
	for _, line := range lines {
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, "\"", 3)
		if len(parts) >= 2 {
			originalMessages[parts[1]] = true
		}
	}

	if len(originalMessages) == 0 {
		t.Fatal("No original log messages found")
	}

	// Capture all structured log messages
	var logBuffer bytes.Buffer
	// TODO: Capture all log output during normal operation
	// This test will fail until implementation exists

	// Collect all structured log messages
	structuredMessages := make(map[string]bool)
	decoder := json.NewDecoder(&logBuffer)
	for decoder.More() {
		var logEntry map[string]interface{}
		err := decoder.Decode(&logEntry)
		if err != nil {
			t.Fatalf("Invalid log entry: %v", err)
		}
		msg, ok := logEntry["msg"].(string)
		if ok {
			structuredMessages[msg] = true
		}
	}

	// Verify all original messages are present
	for msg := range originalMessages {
		if !structuredMessages[msg] {
			t.Errorf("Original log message missing from structured logs: %s", msg)
		}
	}
}

// AC-6: All log output is valid JSON
func TestAC6_LogOutputIsValidJSON(t *testing.T) {
	t.Parallel()

	// Capture all log output during service operation
	var logBuffer bytes.Buffer
	// TODO: Capture all log output
	// This test will fail until implementation exists

	// Validate all entries with jq
	cmd := exec.Command("jq", ".")
	cmd.Stdin = &logBuffer
	err := cmd.Run()
	if err != nil {
		t.Fatalf("Log output failed jq validation: %v", err)
	}
}
