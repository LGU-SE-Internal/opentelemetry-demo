package main

import (
	"context"
	"database/sql"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

var (
	shutdownInProgress atomic.Bool
	inflightRequests   atomic.Int64
)

// SetupSignalHandler registers handlers for SIGINT and SIGTERM signals and returns a context
// that is canceled when a shutdown signal is received.
func SetupSignalHandler() (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(context.Background())
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		sig := <-sigChan
		slog.Info("shutdown signal received",
			slog.String("event", "shutdown_signal_received"),
			slog.String("signal", sig.String()),
			slog.Int("inflight_requests", int(inflightRequests.Load())),
			slog.Int("grace_period_seconds", 10),
		)
		slog.Info("inflight requests count",
			slog.String("event", "inflight_requests_count"),
			slog.Int("inflight_requests", int(inflightRequests.Load())),
			slog.Int("grace_period_seconds", 10),
		)
		shutdownInProgress.Store(true)
		cancel()
	}()

	return ctx, cancel
}

// GracefulShutdown performs clean shutdown of all service resources within the given timeout period.
// Returns an error if any resource fails to close properly before the timeout expires.
func GracefulShutdown(ctx context.Context, timeout time.Duration, httpServer *http.Server, grpcServer *grpc.Server, dbConn *sql.DB) error {
	shutdownCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	var shutdownErr error

	// First shutdown HTTP server to stop accepting new requests
	if httpServer != nil {
		if err := httpServer.Shutdown(shutdownCtx); err != nil {
			if errors.Is(err, context.DeadlineExceeded) {
				slog.Warn("grace period expired while shutting down HTTP server",
					slog.String("event", "grace_period_expired"),
					slog.Int("grace_period_seconds", int(timeout.Seconds())),
					slog.String("error", err.Error()),
				)
				shutdownErr = context.DeadlineExceeded
			} else {
				slog.Error("failed to shutdown HTTP server",
					slog.String("event", "resource_close_error"),
					slog.String("resource", "http_server"),
					slog.String("error", err.Error()),
				)
				shutdownErr = errors.Join(shutdownErr, err)
			}
		}
	}

	// Next shutdown gRPC server gracefully
	if grpcServer != nil {
		grpcStopped := make(chan struct{})
		go func() {
			grpcServer.GracefulStop()
			close(grpcStopped)
		}()

		select {
		case <-grpcStopped:
			// gRPC server stopped gracefully
		case <-shutdownCtx.Done():
			if shutdownCtx.Err() == context.DeadlineExceeded {
				slog.Warn("grace period expired while shutting down gRPC server",
					slog.String("event", "grace_period_expired"),
					slog.Int("grace_period_seconds", int(timeout.Seconds())),
					slog.String("error", shutdownCtx.Err().Error()),
				)
				grpcServer.Stop()
				shutdownErr = context.DeadlineExceeded
			}
		}
	}

	// Close database connection
	if dbConn != nil {
		if err := dbConn.Close(); err != nil {
			slog.Error("failed to close database connection",
				slog.String("event", "resource_close_error"),
				slog.String("resource", "database"),
				slog.String("error", err.Error()),
			)
			shutdownErr = errors.Join(shutdownErr, err)
		}
	}

	if shutdownErr == nil {
		slog.Info("shutdown completed successfully",
			slog.String("event", "shutdown_complete"),
			slog.Int("inflight_requests", int(inflightRequests.Load())),
		)
	} else if !errors.Is(shutdownErr, context.DeadlineExceeded) {
		slog.Error("shutdown completed with errors",
			slog.String("event", "service_exit"),
			slog.String("error", shutdownErr.Error()),
		)
	} else {
		slog.Info("shutdown completed after grace period",
			slog.String("event", "service_exit"),
		)
	}

	return shutdownErr
}

// HTTPShutdownMiddleware returns a middleware that counts inflight requests and rejects new requests with 503 when shutting down
func HTTPShutdownMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if shutdownInProgress.Load() {
			http.Error(w, "Service Unavailable", http.StatusServiceUnavailable)
			return
		}

		inflightRequests.Add(1)
		defer inflightRequests.Add(-1)

		next.ServeHTTP(w, r)
	})
}

// GRPCShutdownInterceptor returns a gRPC unary interceptor that rejects new requests with Unavailable status when shutting down
func GRPCShutdownInterceptor(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
	if shutdownInProgress.Load() {
		return nil, status.Error(codes.Unavailable, "service is shutting down")
	}

	inflightRequests.Add(1)
	defer inflightRequests.Add(-1)

	return handler(ctx, req)
}
