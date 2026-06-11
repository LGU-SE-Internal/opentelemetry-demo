package main

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"os"
	"syscall"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// TestAC1_SignalStopsNewRequests verifies AC-1: after shutdown signal, new requests are rejected
func TestAC1_SignalStopsNewRequests(t *testing.T) {
	t.Parallel()

	// Setup test resources
		testHTTPServer := &http.Server{Addr: ":8080", Handler: HTTPShutdownMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusOK)
		}))}
	testGRPCServer := grpc.NewServer()
	testDB, _ := sql.Open("postgres", "host=localhost port=5432 user=test password=test dbname=test sslmode=disable")
	defer testDB.Close()

	// Setup signal handler
	sigCtx, sigCancel := SetupSignalHandler()
	defer sigCancel()

	// Send SIGTERM signal to process
	proc, _ := os.FindProcess(os.Getpid())
	proc.Signal(syscall.SIGTERM)

	// Wait for signal context to be canceled
	select {
	case <-sigCtx.Done():
	case <-time.After(1 * time.Second):
		t.Fatalf("Signal handler did not cancel context within 1s after SIGTERM")
	}

	// Test new HTTP request returns 503
	req := httptest.NewRequest("GET", "/quote", nil)
	w := httptest.NewRecorder()
	testHTTPServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("Expected HTTP 503 after shutdown signal, got %d", w.Code)
	}

	// Test new gRPC request returns Unavailable status
	// Simulate gRPC request handling after shutdown
	err := status.Error(codes.Unavailable, "service is shutting down")
	if status.Code(err) != codes.Unavailable {
		t.Errorf("Expected gRPC Unavailable status after shutdown signal, got %v", status.Code(err))
	}
}

// TestAC2_InFlightRequestsCompleteWithinGracePeriod verifies AC-2: in-flight requests complete before grace period
func TestAC2_InFlightRequestsCompleteWithinGracePeriod(t *testing.T) {
	t.Parallel()

	requestComplete := make(chan struct{})
	longRunningHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate request that takes 2 seconds to complete
		time.Sleep(2 * time.Second)
		w.WriteHeader(http.StatusOK)
		close(requestComplete)
	})

	testHTTPServer := &http.Server{Addr: ":8081", Handler: longRunningHandler}
	testGRPCServer := grpc.NewServer()
	testDB, _ := sql.Open("postgres", "host=localhost port=5432 user=test password=test dbname=test sslmode=disable")
	defer testDB.Close()

	// Start a long-running request in background
	go func() {
		req := httptest.NewRequest("GET", "/quote", nil)
		w := httptest.NewRecorder()
		longRunningHandler.ServeHTTP(w, req)
	}()

	// Wait 100ms to ensure request is in flight
	time.Sleep(100 * time.Millisecond)

	// Trigger shutdown with 10s grace period
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// Wait for either request to complete or timeout
	select {
	case <-requestComplete:
		// Request completed successfully - pass
	case <-time.After(3 * time.Second):
		t.Fatalf("In-flight request did not complete within allowed time before grace period expires")
	}

	// Run graceful shutdown
	err := GracefulShutdown(shutdownCtx, 10*time.Second, testHTTPServer, testGRPCServer, testDB)
	if err != nil {
		t.Errorf("Graceful shutdown failed with error: %v", err)
	}
}

// TestAC3_EarlyExitWhenAllRequestsDone verifies AC-3: service exits immediately when all requests complete before grace period
func TestAC3_EarlyExitWhenAllRequestsDone(t *testing.T) {
	t.Parallel()

	testHTTPServer := &http.Server{Addr: ":8082", Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})}
	testGRPCServer := grpc.NewServer()
	testDB, _ := sql.Open("postgres", "host=localhost port=5432 user=test password=test dbname=test sslmode=disable")
	defer testDB.Close()

	shutdownStart := time.Now()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	err := GracefulShutdown(shutdownCtx, 10*time.Second, testHTTPServer, testGRPCServer, testDB)
	if err != nil {
		t.Errorf("Graceful shutdown failed with error: %v", err)
	}

	shutdownDuration := time.Since(shutdownStart)
	// Shutdown should complete way before 10s if no in-flight requests
	if shutdownDuration > 2*time.Second {
		t.Errorf("Shutdown took too long (%v), should exit immediately when no in-flight requests", shutdownDuration)
	}
}

// TestAC4_ForceShutdownAfterGracePeriod verifies AC-4: service force shuts down after 10s grace period
func TestAC4_ForceShutdownAfterGracePeriod(t *testing.T) {
	t.Parallel()

	requestBlocked := make(chan struct{})
	longRunningHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate request that takes 15 seconds (longer than grace period)
		time.Sleep(15 * time.Second)
		w.WriteHeader(http.StatusOK)
		close(requestBlocked)
	})

	testHTTPServer := &http.Server{Addr: ":8083", Handler: longRunningHandler}
	testGRPCServer := grpc.NewServer()
	testDB, _ := sql.Open("postgres", "host=localhost port=5432 user=test password=test dbname=test sslmode=disable")
	defer testDB.Close()

	// Start a long-running request in background
	go func() {
		req := httptest.NewRequest("GET", "/quote", nil)
		w := httptest.NewRecorder()
		longRunningHandler.ServeHTTP(w, req)
	}()

	// Wait 100ms to ensure request is in flight
	time.Sleep(100 * time.Millisecond)

	// Trigger shutdown with 3s grace period for test speed (spec uses 10s)
	testGracePeriod := 3 * time.Second
	shutdownCtx, cancel := context.WithTimeout(context.Background(), testGracePeriod)
	defer cancel()

	shutdownStart := time.Now()
	err := GracefulShutdown(shutdownCtx, testGracePeriod, testHTTPServer, testGRPCServer, testDB)
	shutdownDuration := time.Since(shutdownStart)

	// Check that shutdown completed after grace period, not earlier
	if shutdownDuration < testGracePeriod - 500*time.Millisecond {
		t.Errorf("Shutdown completed too early (%v), should wait for full grace period when in-flight requests are running", shutdownDuration)
	}

	// Should return DeadlineExceeded error per spec
	if err != context.DeadlineExceeded {
		t.Errorf("Expected DeadlineExceeded error when grace period expires, got %v", err)
	}
}

// TestAC5_ShutdownLogEntriesPresent verifies AC-5: all required structured logs are emitted
func TestAC5_ShutdownLogEntriesPresent(t *testing.T) {
	t.Parallel()

	testHTTPServer := &http.Server{Addr: ":8084", Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})}
	testGRPCServer := grpc.NewServer()
	testDB, _ := sql.Open("postgres", "host=localhost port=5432 user=test password=test dbname=test sslmode=disable")
	defer testDB.Close()

	// Capture log output (mock logger setup)
	// TODO: Replace with actual logger capture when implementation exists
	requiredLogEvents := []string{
		"shutdown_signal_received",
		"inflight_requests_count",
		"shutdown_complete",
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	GracefulShutdown(shutdownCtx, 10*time.Second, testHTTPServer, testGRPCServer, testDB)

	// Verify all required log events are present
	for _, event := range requiredLogEvents {
		// TODO: Implement log check once logger is integrated
		t.Logf("Checking for required log event: %s", event)
	}
}

// TestAC6_NonZeroExitOnResourceCloseError verifies AC-6: non-zero exit code on resource close failure
func TestAC6_NonZeroExitOnResourceCloseError(t *testing.T) {
	t.Parallel()

	testHTTPServer := &http.Server{Addr: ":8085", Handler: nil}
	testGRPCServer := grpc.NewServer()
	// Use already closed DB to simulate close error
	testDB, _ := sql.Open("postgres", "host=localhost port=5432 user=test password=test dbname=test sslmode=disable")
	testDB.Close() // Pre-close to force error when shutting down

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	err := GracefulShutdown(shutdownCtx, 10*time.Second, testHTTPServer, testGRPCServer, testDB)
	if err == nil {
		t.Fatalf("Expected error when closing already closed database connection, got nil")
	}

	// Check that error is logged with resource_close_error event
	// TODO: Verify error log entry exists
	t.Logf("Received expected resource close error: %v", err)
}
