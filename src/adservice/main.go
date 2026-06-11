package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/cenkalti/backoff/v4"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"golang.org/x/time/rate"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/log"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/metric"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"

	_ "github.com/lib/pq"
	pb "github.com/open-telemetry/opentelemetry-demo/pb/oteldemo"
)

var isShuttingDown atomic.Bool
var otelLogger = global.GetLoggerProvider().Logger("adservice")
var rateLimitedRequestsCounter metric.Int64Counter

// RetryConfig holds configuration for database retry logic
type RetryConfig struct {
	MaxRetries        int           `default:"3"`
	InitialBackoff    time.Duration `default:"100ms"`
	MaxBackoff        time.Duration `default:"2s"` // Capped backoff to avoid long delays
}

// RetryMetrics defines OTel metrics collected for retry operations
type RetryMetrics struct {
	RetryAttempts metric.Int64Counter // Name: ad_service_db_retry_attempts, Description: Total number of database operation retry attempts
	RetryFailures metric.Int64Counter // Name: ad_service_db_retry_failures, Description: Total number of database operations that failed after all retries
}

// Transient PostgreSQL error codes eligible for retry:
// - Connection errors: 08001 (sqlclient_unable_to_establish_sqlconnection), 08006 (connection_failure), 57P01 (admin_shutdown), 57P02 (crash_shutdown)
// - Timeouts: 57014 (query_canceled), 40001 (serialization_failure), 40P01 (deadlock_detected)
// - Lock waits: 55P03 (lock_not_available)
var TransientPostgresErrorCodes = map[string]bool{
	"08001": true, "08006": true, "57P01": true, "57P02": true,
	"57014": true, "40001": true, "40P01": true, "55P03": true,
}

// DBPool defines the interface for database operations that will be wrapped with retry logic
type DBPool interface {
	Ping(ctx context.Context) error
	Exec(ctx context.Context, query string, args ...interface{}) (pgconn.CommandTag, error)
	Query(ctx context.Context, query string, args ...interface{}) (pgx.Rows, error)
	QueryRow(ctx context.Context, query string, args ...interface{}) pgx.Row
}

// RetryableDB wraps a PostgreSQL database connection/pool to add retry logic
type RetryableDB struct {
	db      DBPool
	config  RetryConfig
	metrics RetryMetrics
}

// NewRetryableDB creates a new RetryableDB instance with the given configuration and metrics
func NewRetryableDB(db DBPool, config RetryConfig, metrics RetryMetrics) *RetryableDB {
	// Set default values if not provided
	if config.MaxRetries < 0 {
		config.MaxRetries = 3
	}
	if config.InitialBackoff <= 0 {
		config.InitialBackoff = 100 * time.Millisecond
	}
	if config.MaxBackoff <= 0 {
		config.MaxBackoff = 2 * time.Second
	}
	return &RetryableDB{
		db:      db,
		config:  config,
		metrics: metrics,
// isTransientError checks if a PostgreSQL error is transient and eligible for retry
func isTransientError(err error) bool {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		return TransientPostgresErrorCodes[pgErr.Code]
	}
	// Also check for context cancellation errors which we handle separately
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	return false
}

// retryWithBackoff executes the given operation with exponential backoff retry logic
func (rdb *RetryableDB) retryWithBackoff(ctx context.Context, operation string, op func() error) error {
	if rdb.config.MaxRetries <= 0 {
		return op()
	}

	// Create exponential backoff
	eb := backoff.NewExponentialBackOff()
	eb.InitialInterval = rdb.config.InitialBackoff
	eb.MaxInterval = rdb.config.MaxBackoff
	eb.Multiplier = 2
	eb.Reset()

	var lastErr error
	for attempt := 0; attempt <= rdb.config.MaxRetries; attempt++ {
		err := op()
		if err == nil {
			return nil
		}

		lastErr = err

		// Check if context is canceled
		if ctx.Err() != nil {
			return ctx.Err()
		}

		// Check if error is transient
		if !isTransientError(err) {
			return err
		}

		// If this is the last attempt, don't backoff
		if attempt == rdb.config.MaxRetries {
			break
		}

		// Increment retry attempts metric
		rdb.metrics.RetryAttempts.Add(ctx, 1,
			metric.WithAttributes(
				attribute.String("error_code", getPgErrorCode(err)),
				attribute.String("operation", operation),
			),
		)

		// Wait for backoff or context cancellation
		nextBackoff := eb.NextBackOff()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(nextBackoff):
			// Continue to next retry
		}
	}

	// Increment failure metric after all retries failed
	rdb.metrics.RetryFailures.Add(ctx, 1,
		metric.WithAttributes(
			attribute.String("error_code", getPgErrorCode(lastErr)),
			attribute.String("operation", operation),
		),
	)

	return lastErr
}

// getPgErrorCode extracts the error code from a PostgreSQL error, returns empty string if not a pg error
func getPgErrorCode(err error) string {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		return pgErr.Code
	}
	return ""
}

// Ping wraps the underlying Ping method with retry logic
func (rdb *RetryableDB) Ping(ctx context.Context) error {
	return rdb.retryWithBackoff(ctx, "ping", func() error {
		return rdb.db.Ping(ctx)
	})
}

// Exec wraps the underlying Exec method with retry logic
func (rdb *RetryableDB) Exec(ctx context.Context, query string, args ...interface{}) (pgconn.CommandTag, error) {
	var result pgconn.CommandTag
	err := rdb.retryWithBackoff(ctx, "exec", func() error {
		var innerErr error
		result, innerErr = rdb.db.Exec(ctx, query, args...)
		return innerErr
	})
	return result, err
}

// Query wraps the underlying Query method with retry logic
func (rdb *RetryableDB) Query(ctx context.Context, query string, args ...interface{}) (pgx.Rows, error) {
	var result pgx.Rows
	err := rdb.retryWithBackoff(ctx, "query", func() error {
		var innerErr error
		result, innerErr = rdb.db.Query(ctx, query, args...)
		return innerErr
	})
	return result, err
}

// QueryRow wraps the underlying QueryRow method with retry logic
func (rdb *RetryableDB) QueryRow(ctx context.Context, query string, args ...interface{}) pgx.Row {
	var result pgx.Row
	// QueryRow never returns nil, so we just capture the result
	_ = rdb.retryWithBackoff(ctx, "queryrow", func() error {
		result = rdb.db.QueryRow(ctx, query, args...)
		// QueryRow doesn't return error immediately, error is returned on Scan
		return nil
	})
	return result
}
