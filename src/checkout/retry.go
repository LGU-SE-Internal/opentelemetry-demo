package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// RetryMetrics holds prometheus counters for retry tracking
type RetryMetrics struct {
	// Attempts counts total retry attempts per downstream service and status code
	Attempts *prometheus.CounterVec
	// Failures counts calls that failed after all retry attempts per downstream service and final status code
	Failures *prometheus.CounterVec
}

// NewRetryMetrics initializes and registers retry metrics with the provided prometheus registry
func NewRetryMetrics(registry *prometheus.Registry) *RetryMetrics {
	m := &RetryMetrics{
		Attempts: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: "otelcol_demo_checkout_retry_attempts",
				Help: "Total retry attempts per downstream service and status code",
			},
			[]string{"service_name", "status_code"},
		),
		Failures: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: "otelcol_demo_checkout_retry_failures",
				Help: "Calls that failed after all retry attempts per downstream service and final status code",
			},
			[]string{"service_name", "final_status_code"},
		),
	}

	registry.MustRegister(m.Attempts)
	registry.MustRegister(m.Failures)

	return m
}

// GenerateIdempotencyKey returns a UUIDv4 string to use as idempotency key for write operations
func GenerateIdempotencyKey() string {
	return uuid.NewString()
}

// NewRetryInterceptor creates a gRPC unary client interceptor that implements retry logic for transient errors
// Parameters:
//   - maxRetries: maximum number of retry attempts per call (excluding initial call)
//   - backoffBase: initial delay for exponential backoff
//   - retriableCodes: list of gRPC status codes that should trigger a retry
//   - metrics: (optional) RetryMetrics instance for tracking retries. If nil, metrics are disabled.
//
// Returns: configured gRPC UnaryClientInterceptor
func NewRetryInterceptor(maxRetries int, backoffBase time.Duration, retriableCodes []codes.Code, metrics ...*RetryMetrics) grpc.UnaryClientInterceptor {
	retriableCodeMap := make(map[codes.Code]bool)
	for _, code := range retriableCodes {
		retriableCodeMap[code] = true
	}

	var retryMetrics *RetryMetrics
	if len(metrics) > 0 {
		retryMetrics = metrics[0]
	}

	return func(
		ctx context.Context,
		method string,
		req, reply interface{},
		cc *grpc.ClientConn,
		invoker grpc.UnaryInvoker,
		opts ...grpc.CallOption,
	) error {
		// Extract service name from method path (format: /package.Service/Method)
		parts := strings.Split(method, "/")
		serviceName := "unknown"
		if len(parts) >= 2 {
			serviceParts := strings.Split(parts[1], ".")
			serviceName = serviceParts[len(serviceParts)-1]
		}
		var lastErr error

		// Check if idempotency key is already present in the context metadata
		md, ok := metadata.FromOutgoingContext(ctx)
		if !ok {
			md = metadata.New(nil)
		}
		idempotencyKeys := md.Get("idempotency-key")
		var idempotencyKey string
		if len(idempotencyKeys) > 0 {
			idempotencyKey = idempotencyKeys[0]
		} else {
			// Generate a new idempotency key for write operations if not present
			// This ensures all retries of the same request use the same key
			idempotencyKey = GenerateIdempotencyKey()
			md.Set("idempotency-key", idempotencyKey)
			ctx = metadata.NewOutgoingContext(ctx, md)
		}

		for attempt := 0; attempt <= maxRetries; attempt++ {
			if attempt > 0 {
				// Calculate exponential backoff
				delay := backoffBase * time.Duration(1<<attempt)
				if delay > 1*time.Second {
					delay = 1 * time.Second
				}
				// Wait before retrying
				select {
				case <-ctx.Done():
					return ctx.Err()
				case <-time.After(delay):
				}
			}

			// Make the call
			err := invoker(ctx, method, req, reply, cc, opts...)
			if err == nil {
				// Success, return immediately
				return nil
			}

			lastErr = err
			st, ok := status.FromError(err)
			if !ok {
				// Not a gRPC status error, don't retry
				break
			}

			// Increment retry attempts metric if this is a retry (after first attempt)
			if attempt > 0 && retryMetrics != nil {
				retryMetrics.Attempts.WithLabelValues(serviceName, st.Code().String()).Inc()
			}

			// Check if this status code is retriable
			if !retriableCodeMap[st.Code()] {
				break
			}
		}

		// If we got here, all attempts failed
		st, ok := status.FromError(lastErr)
		finalStatusCode := "unknown"
		if ok {
			finalStatusCode = st.Code().String()
		}
		if retryMetrics != nil {
			retryMetrics.Failures.WithLabelValues(serviceName, finalStatusCode).Inc()
		}

		return fmt.Errorf("failed after %d attempts: %w", maxRetries+1, lastErr)
	}
}
