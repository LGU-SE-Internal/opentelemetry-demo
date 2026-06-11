package evaluation

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.uber.org/zap"
)

// PersistentStore is the interface for storage layer with Flush method
type PersistentStore interface {
	Flush() (int, error)
}

// Error types for graceful shutdown
var (
	ErrShutdownTimeout    = errors.New("shutdown timed out before all operations completed")
	ErrStorageFlushFailed = errors.New("persistent storage flush failed")
)

const gracePeriod = 30 * time.Second

// SetupGracefulShutdown configures signal handlers for SIGINT/SIGTERM and implements graceful shutdown logic
func SetupGracefulShutdown(server *http.Server, storage PersistentStore, logger *zap.Logger) context.CancelFunc {
	ctx, cancel := context.WithCancel(context.Background())

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		select {
		case sig := <-sigChan:
			logger.Info("shutdown signal received",
				zap.String("signal", sig.String()),
				zap.Time("timestamp", time.Now()),
			)

			// Start grace period
			logger.Info("starting grace period for in-progress operations",
				zap.Duration("grace_period", gracePeriod),
			)

			shutdownCtx, shutdownCancel := context.WithTimeout(ctx, gracePeriod)
			defer shutdownCancel()

			// Shutdown HTTP server: stop accepting new requests, wait for active ones to complete
			if err := server.Shutdown(shutdownCtx); err != nil {
				if errors.Is(err, context.DeadlineExceeded) {
					logger.Error("shutdown timed out, unfinished operations remaining",
						zap.Error(ErrShutdownTimeout),
						zap.Int("unfinished_operations", getActiveRequestCount(server)),
					)
				} else {
					logger.Error("HTTP server shutdown failed", zap.Error(err))
				}
			}

			// Flush persistent storage
			logger.Debug("starting persistent storage flush")
			bytesFlushed, flushErr := storage.Flush()
			if flushErr != nil {
				logger.Error("persistent storage flush failed",
					zap.Error(ErrStorageFlushFailed),
					zap.NamedError("flush_error", flushErr),
				)
				os.Exit(1)
			}

			logger.Debug("persistent storage flush completed",
				zap.Int("bytes_flushed", bytesFlushed),
			)

			logger.Info("shutdown completed successfully")
			os.Exit(0)
		case <-ctx.Done():
			// Manual shutdown via cancel function
			signal.Stop(sigChan)
			return
		}
	}()

	return cancel
}

// getActiveRequestCount returns the number of active requests being handled by the server
// Note: This uses the internal Go http.Server ActiveRequests field available in Go 1.20+
func getActiveRequestCount(server *http.Server) int {
	return int(server.ActiveRequests)
}
