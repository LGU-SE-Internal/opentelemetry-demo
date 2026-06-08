package main

import (
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

const binaryPath = "./kafka-collector-test"

func TestMain(m *testing.M) {
	// Build the test binary first
	buildCmd := exec.Command("go", "build", "-o", binaryPath, ".")
	err := buildCmd.Run()
	if err != nil {
		os.Exit(1)
	}
	code := m.Run()
	// Clean up
	os.Remove(binaryPath)
	os.Exit(code)
}

func TestAC1_DefaultValuesWhenNoEnvSet(t *testing.T) {
	// Unset both environment variables
	os.Unsetenv("KAFKA_COLLECTOR_HTTP_PORT")
	os.Unsetenv("KAFKA_COLLECTOR_KAFKA_TOPICS")

	cmd := exec.Command(binaryPath)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	// Start the service
	err := cmd.Start()
	if err != nil {
		t.Fatalf("Failed to start service: %v", err)
	}

	// Give it time to start
	time.Sleep(2 * time.Second)

	// Check if process is still running (should not have exited)
	if err := cmd.Process.Signal(os.Kill); err != nil {
		t.Fatalf("Service exited unexpectedly: %v, stderr: %s", err, stderr.String())
	}

	// Check logs for default port 8080 and default topic checkout-events
	output := stderr.String() + stdout.String()
	if !strings.Contains(output, "8080") {
		t.Error("Expected log to contain default port 8080")
	}
	if !strings.Contains(output, "checkout-events") {
		t.Error("Expected log to contain default topic checkout-events")
	}
}

func TestAC2_CustomPortWhenValidSet(t *testing.T) {
	os.Unsetenv("KAFKA_COLLECTOR_KAFKA_TOPICS")
	os.Setenv("KAFKA_COLLECTOR_HTTP_PORT", "9090")
	defer os.Unsetenv("KAFKA_COLLECTOR_HTTP_PORT")

	cmd := exec.Command(binaryPath)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Start()
	if err != nil {
		t.Fatalf("Failed to start service: %v", err)
	}

	time.Sleep(2 * time.Second)

	if err := cmd.Process.Signal(os.Kill); err != nil {
		t.Fatalf("Service exited unexpectedly: %v, stderr: %s", err, stderr.String())
	}

	output := stderr.String() + stdout.String()
	if !strings.Contains(output, "9090") {
		t.Error("Expected log to contain custom port 9090")
	}
	if !strings.Contains(output, "checkout-events") {
		t.Error("Expected log to contain default topic checkout-events")
	}
}

func TestAC3_ExitOnNonNumericPort(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_HTTP_PORT", "abc")
	defer os.Unsetenv("KAFKA_COLLECTOR_HTTP_PORT")

	cmd := exec.Command(binaryPath)
	var stderr strings.Builder
	cmd.Stderr = &stderr

	err := cmd.Run()
	if err == nil {
		t.Fatal("Expected service to exit with error, but it succeeded")
	}

	exitCode := cmd.ProcessState.ExitCode()
	if exitCode != 1 {
		t.Errorf("Expected exit code 1, got %d", exitCode)
	}

	if !strings.Contains(strings.ToLower(stderr.String()), "invalid port") {
		t.Error("Expected error log about invalid port value")
	}
}

func TestAC4_ExitOnPortOutOfRange(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_HTTP_PORT", "65536")
	defer os.Unsetenv("KAFKA_COLLECTOR_HTTP_PORT")

	cmd := exec.Command(binaryPath)
	var stderr strings.Builder
	cmd.Stderr = &stderr

	err := cmd.Run()
	if err == nil {
		t.Fatal("Expected service to exit with error, but it succeeded")
	}

	exitCode := cmd.ProcessState.ExitCode()
	if exitCode != 1 {
		t.Errorf("Expected exit code 1, got %d", exitCode)
	}

	if !strings.Contains(strings.ToLower(stderr.String()), "port") || !strings.Contains(strings.ToLower(stderr.String()), "range") {
		t.Error("Expected error log about invalid port range")
	}
}

func TestAC5_CustomTopicsWhenValidListSet(t *testing.T) {
	os.Unsetenv("KAFKA_COLLECTOR_HTTP_PORT")
	os.Setenv("KAFKA_COLLECTOR_KAFKA_TOPICS", "checkout-events,payment-events,shipping-events")
	defer os.Unsetenv("KAFKA_COLLECTOR_KAFKA_TOPICS")

	cmd := exec.Command(binaryPath)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Start()
	if err != nil {
		t.Fatalf("Failed to start service: %v", err)
	}

	time.Sleep(2 * time.Second)

	if err := cmd.Process.Signal(os.Kill); err != nil {
		t.Fatalf("Service exited unexpectedly: %v, stderr: %s", err, stderr.String())
	}

	output := stderr.String() + stdout.String()
	if !strings.Contains(output, "checkout-events") {
		t.Error("Expected log to contain topic checkout-events")
	}
	if !strings.Contains(output, "payment-events") {
		t.Error("Expected log to contain topic payment-events")
	}
	if !strings.Contains(output, "shipping-events") {
		t.Error("Expected log to contain topic shipping-events")
	}
}

func TestAC6_ExitOnEmptyTopics(t *testing.T) {
	os.Setenv("KAFKA_COLLECTOR_KAFKA_TOPICS", "")
	defer os.Unsetenv("KAFKA_COLLECTOR_KAFKA_TOPICS")

	cmd := exec.Command(binaryPath)
	var stderr strings.Builder
	cmd.Stderr = &stderr

	err := cmd.Run()
	if err == nil {
		t.Fatal("Expected service to exit with error, but it succeeded")
	}

	exitCode := cmd.ProcessState.ExitCode()
	if exitCode != 1 {
		t.Errorf("Expected exit code 1, got %d", exitCode)
	}

	if !strings.Contains(strings.ToLower(stderr.String()), "empty") || !strings.Contains(strings.ToLower(stderr.String()), "topic") {
		t.Error("Expected error log about empty topics list")
	}
}

func TestAC7_IgnoreEmptyValuesInTopicsList(t *testing.T) {
	os.Unsetenv("KAFKA_COLLECTOR_HTTP_PORT")
	os.Setenv("KAFKA_COLLECTOR_KAFKA_TOPICS", "checkout-events,,payment-events,")
	defer os.Unsetenv("KAFKA_COLLECTOR_KAFKA_TOPICS")

	cmd := exec.Command(binaryPath)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Start()
	if err != nil {
		t.Fatalf("Failed to start service: %v", err)
	}

	time.Sleep(2 * time.Second)

	if err := cmd.Process.Signal(os.Kill); err != nil {
		t.Fatalf("Service exited unexpectedly: %v, stderr: %s", err, stderr.String())
	}

	output := stderr.String() + stdout.String()
	if !strings.Contains(output, "checkout-events") {
		t.Error("Expected log to contain topic checkout-events")
	}
	if !strings.Contains(output, "payment-events") {
		t.Error("Expected log to contain topic payment-events")
	}
	if strings.Contains(output, ",,") || strings.Contains(output, "  ") {
		t.Error("Expected empty topic values to be ignored")
	}
}
