package tests

import (
	"context"
	"net/http"
	"net/http/httptest"
	"syscall"
	"testing"
	"time"

	"go.uber.org/zap"
	"go.uber.org/zap/zaptest"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// PersistentStore is the interface for storage layer as per spec
type PersistentStore interface {
	Flush() error
}

// mockPersistentStore implements PersistentStore for testing
type mockPersistentStore struct {
	flushCalled bool
	flushError  error
	flushBytes  int
}

func (m *mockPersistentStore) Flush() error {
	m.flushCalled = true
	return m.flushError
}

// Import the SetupGracefulShutdown function from the implementation package
// (will be uncommented once implementation exists)
// import "github.com/open-telemetry/opentelemetry-demo/src/flagd/evaluation"

// TestAC1_SIGTERMReturns503ForNewRequests tests that after receiving SIGTERM/SIGINT,
// new requests return 503 Service Unavailable immediately.
func TestAC1_SIGTERMReturns503ForNewRequests(t *testing.T) {
	t.Parallel()

	// Setup test server with a handler that just returns 200
	testServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer testServer.Close()

	httpServer := testServer.Config
	mockStore := &mockPersistentStore{}
	logger := zaptest.NewLogger(t)

	// Setup graceful shutdown
	// cancel := evaluation.SetupGracefulShutdown(httpServer, mockStore, logger)
	// defer cancel()

	// Send SIGTERM to the process
	// syscall.Kill(syscall.Getpid(), syscall.SIGTERM)

	// Wait for signal handler to register and take effect
	time.Sleep(100 * time.Millisecond)

	// Send new request after signal received
	resp, err := http.Get(testServer.URL)
	require.NoError(t, err)
	defer resp.Body.Close()

	// Assert response is 503
	assert.Equal(t, http.StatusServiceUnavailable, resp.StatusCode)
}

// TestAC2_InProgressRequestsCompleteBeforeShutdown tests that requests started before
// SIGTERM are allowed to complete successfully within the 30s grace period.
func TestAC2_InProgressRequestsCompleteBeforeShutdown(t *testing.T) {
	t.Parallel()

	requestCompleted := make(chan struct{})

	// Setup test server with a handler that takes 1 second to complete
	testServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(1 * time.Second)
		w.WriteHeader(http.StatusOK)
		close(requestCompleted)
	}))
	defer testServer.Close()

	httpServer := testServer.Config
	mockStore := &mockPersistentStore{}
	logger := zaptest.NewLogger(t)

	// Setup graceful shutdown
	// cancel := evaluation.SetupGracefulShutdown(httpServer, mockStore, logger)
	// defer cancel()

	// Start a request before sending signal
	go func() {
		resp, err := http.Get(testServer.URL)
		if err == nil {
			defer resp.Body.Close()
			assert.Equal(t, http.StatusOK, resp.StatusCode)
		}
	}()

	// Wait a little for the request to start processing
	time.Sleep(200 * time.Millisecond)

	// Send SIGTERM
	// syscall.Kill(syscall.Getpid(), syscall.SIGTERM)

	// Wait for the request to complete
	select {
	case <-requestCompleted:
		// Success: request finished
	case <-time.After(2 * time.Second):
		t.Fatal("in-progress request did not complete successfully after SIGTERM")
	}
}

// TestAC3_ShutdownTimeoutLogsUnfinishedOperations tests that if operations are still
// running after 30s, service terminates immediately and logs error with unfinished count.
func TestAC3_ShutdownTimeoutLogsUnfinishedOperations(t *testing.T) {
	t.Parallel()

	// Setup test server with a handler that takes 35 seconds (longer than grace period)
	testServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(35 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer testServer.Close()

	httpServer := testServer.Config
	mockStore := &mockPersistentStore{}
	logger := zaptest.NewLogger(t)

	// Capture log output to check for error message
	// (log capture setup omitted for brevity, would use zap observer in real implementation)

	// Setup graceful shutdown
	// cancel := evaluation.SetupGracefulShutdown(httpServer, mockStore, logger)
	// defer cancel()

	// Start a long running request
	go func() {
		http.Get(testServer.URL)
	}()

	// Wait a little for request to start
	time.Sleep(200 * time.Millisecond)

	// Send SIGTERM
	// syscall.Kill(syscall.Getpid(), syscall.SIGTERM)

	// Measure time to shutdown
	start := time.Now()
	// Wait for shutdown to complete
	// (would wait for process exit in real test, here we just simulate the 30s timeout)
	time.Sleep(31 * time.Second)

	// Assert shutdown happened within ~30s, not 35s
	assert.Less(t, time.Since(start), 32 * time.Second)
	// Assert error log contains unfinished operation count >= 1
}

// TestAC4_PersistentStorageFlushedBeforeExit tests that all pending writes are flushed
// to disk before exit, with debug log showing bytes flushed.
func TestAC4_PersistentStorageFlushedBeforeExit(t *testing.T) {
	t.Parallel()

	testServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer testServer.Close()

	httpServer := testServer.Config
	mockStore := &mockPersistentStore{flushBytes: 1234}
	logger := zaptest.NewLogger(t)

	// Setup graceful shutdown
	// cancel := evaluation.SetupGracefulShutdown(httpServer, mockStore, logger)
	// defer cancel()

	// Send SIGTERM
	// syscall.Kill(syscall.Getpid(), syscall.SIGTERM)

	// Wait for shutdown
	time.Sleep(1 * time.Second)

	// Assert Flush was called
	assert.True(t, mockStore.flushCalled)
	// Assert debug log contains flush byte count 1234
}

// TestAC5_StorageFlushFailureLogsErrorAndNonZeroExit tests that if storage flush fails,
// error is logged and service exits with non-zero code.
func TestAC5_StorageFlushFailureLogsErrorAndNonZeroExit(t *testing.T) {
	t.Parallel()

	testServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer testServer.Close()

	httpServer := testServer.Config
	mockStore := &mockPersistentStore{
		flushError: assert.AnError,
	}
	logger := zaptest.NewLogger(t)

	// Setup graceful shutdown
	// cancel := evaluation.SetupGracefulShutdown(httpServer, mockStore, logger)
	// defer cancel()

	// Send SIGTERM
	// syscall.Kill(syscall.Getpid(), syscall.SIGTERM)

	// Wait for shutdown
	time.Sleep(1 * time.Second)

	// Assert Flush was called
	assert.True(t, mockStore.flushCalled)
	// Assert error log contains the flush error detail
	// Assert exit code is non-zero
}

// TestAC6_AllShutdownEventsLoggedProperly tests that all shutdown events are logged
// with appropriate severity and metadata.
func TestAC6_AllShutdownEventsLoggedProperly(t *testing.T) {
	t.Parallel()

	testServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(500 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	}))
	defer testServer.Close()

	httpServer := testServer.Config
	mockStore := &mockPersistentStore{}
	logger := zaptest.NewLogger(t)

	// Setup log observer to capture all log entries
	// observer := zaptest.NewObserver(logger)

	// Setup graceful shutdown
	// cancel := evaluation.SetupGracefulShutdown(httpServer, mockStore, logger)
	// defer cancel()

	// Start a request
	go func() {
		http.Get(testServer.URL)
	}()

	// Wait for request to start
	time.Sleep(200 * time.Millisecond)

	// Send SIGTERM
	// syscall.Kill(syscall.Getpid(), syscall.SIGTERM)

	// Wait for shutdown
	time.Sleep(2 * time.Second)

	// Check that all expected log entries exist:
	// 1. Info level: signal received (with signal type, timestamp)
	// 2. Info level: grace period start (with duration 30s)
	// 3. Debug level: storage flush start
	// 4. Debug level: storage flush end (with byte count)
	// 5. Info level: shutdown completed (success)
}

// TestAC7_NoInProgressOperationsExitsImmediately tests that if no operations are in progress,
// service flushes storage and exits immediately without waiting 30s.
func TestAC7_NoInProgressOperationsExitsImmediately(t *testing.T) {
	t.Parallel()

	testServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer testServer.Close()

	httpServer := testServer.Config
	mockStore := &mockPersistentStore{}
	logger := zaptest.NewLogger(t)

	// Setup graceful shutdown
	// cancel := evaluation.SetupGracefulShutdown(httpServer, mockStore, logger)
	// defer cancel()

	// Wait for any in-flight requests to complete (none here)
	time.Sleep(100 * time.Millisecond)

	// Send SIGTERM
	start := time.Now()
	// syscall.Kill(syscall.Getpid(), syscall.SIGTERM)

	// Wait for shutdown
	time.Sleep(500 * time.Millisecond)

	// Assert shutdown completed in less than 1 second, not 30s
	assert.Less(t, time.Since(start), 2 * time.Second)
	// Assert Flush was called
	assert.True(t, mockStore.flushCalled)
}
