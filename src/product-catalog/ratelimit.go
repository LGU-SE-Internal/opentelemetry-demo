// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

type perEndpointRateLimiter struct {
	defaultLimit rate.Limit
	limiters     map[string]*rate.Limiter
	mu           sync.RWMutex
	// For RPS tracking
	requestCounts map[string]int64
	countMu       sync.Mutex
	lastReset     time.Time
}

var (
	rateLimitExceededCounter metric.Int64Counter
	currentRPSGauge          metric.Float64Gauge
)

func initMetrics(meter metric.Meter) error {
	var err error
	rateLimitExceededCounter, err = meter.Int64Counter(
		"product_catalog_rate_limit_exceeded_total",
		metric.WithDescription("Total number of requests rejected due to rate limiting"),
	)
	if err != nil {
		return err
	}

	currentRPSGauge, err = meter.Float64Gauge(
		"product_catalog_rate_limit_current_rps",
		metric.WithDescription("Current measured requests per second for each endpoint over 10s window"),
	)
	if err != nil {
		return err
	}

	return nil
}

func NewRateLimiterFromEnv() (*perEndpointRateLimiter, error) {
	defaultRPS := 0
	defaultRPSStr := os.Getenv("PRODUCT_CATALOG_RATELIMIT_DEFAULT_RPS")
	if defaultRPSStr != "" {
		val, err := strconv.Atoi(defaultRPSStr)
		if err != nil {
			return nil, fmt.Errorf("invalid default rate limit value: %w", err)
		}
		defaultRPS = val
	}

	limiter := &perEndpointRateLimiter{
		defaultLimit:  rate.Limit(defaultRPS),
		limiters:      make(map[string]*rate.Limiter),
		requestCounts: make(map[string]int64),
		lastReset:     time.Now(),
	}

	// Load endpoint-specific limits
	for _, e := range os.Environ() {
		pair := strings.SplitN(e, "=", 2)
		if len(pair) != 2 {
			continue
		}
		key := pair[0]
		value := pair[1]

		if strings.HasPrefix(key, "PRODUCT_CATALOG_RATELIMIT_") && strings.HasSuffix(key, "_RPS") {
			if key == "PRODUCT_CATALOG_RATELIMIT_DEFAULT_RPS" {
				continue
			}

			endpointName := strings.TrimSuffix(strings.TrimPrefix(key, "PRODUCT_CATALOG_RATELIMIT_"), "_RPS")
			endpointName = strings.ReplaceAll(endpointName, "_", "/")

			rps, err := strconv.Atoi(value)
			if err != nil {
				return nil, fmt.Errorf("invalid rate limit for endpoint %s: %w", endpointName, err)
			}
			if rps <= 0 {
				continue
			}

			limiter.limiters[endpointName] = rate.NewLimiter(rate.Limit(rps), rps)
		}
	}

	// Start RPS calculation goroutine
	go limiter.calculateRPSLoop()

	return limiter, nil
}

func (l *perEndpointRateLimiter) Allow(endpoint string) bool {
	l.mu.RLock()
	lim, ok := l.limiters[endpoint]
	l.mu.RUnlock()

	if !ok {
		if l.defaultLimit <= 0 {
			// No limit configured, allow all
			l.recordRequest(endpoint)
			return true
		}
		// Create new limiter for this endpoint with default limit
		l.mu.Lock()
		lim = rate.NewLimiter(l.defaultLimit, int(l.defaultLimit))
		l.limiters[endpoint] = lim
		l.mu.Unlock()
	}

	allowed := lim.Allow()
	if allowed {
		l.recordRequest(endpoint)
	}
	return allowed
}

func (l *perEndpointRateLimiter) recordRequest(endpoint string) {
	l.countMu.Lock()
	defer l.countMu.Unlock()
	l.requestCounts[endpoint]++
}

func (l *perEndpointRateLimiter) calculateRPSLoop() {
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	window := 10 * time.Second
	var windows []map[string]int64

	for range ticker.C {
		l.countMu.Lock()
		currentCounts := make(map[string]int64, len(l.requestCounts))
		for k, v := range l.requestCounts {
			currentCounts[k] = v
		}
		l.requestCounts = make(map[string]int64)
		l.countMu.Unlock()

		// Add current window
		windows = append(windows, currentCounts)
		// Keep only last 10 windows
		if len(windows) > 10 {
			windows = windows[1:]
		}

		// Calculate total over window
		totals := make(map[string]int64)
		for _, w := range windows {
			for k, v := range w {
				totals[k] += v
			}
		}

		// Update gauge
		for endpoint, total := range totals {
			rps := float64(total) / window.Seconds()
			currentRPSGauge.Record(context.Background(), rps, metric.WithAttributes(attribute.String("endpoint", endpoint)))
		}
	}
}

func RateLimitInterceptor(limiter *perEndpointRateLimiter) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		if limiter == nil {
			// No limiter configured, pass through
			return handler(ctx, req)
		}

		endpoint := info.FullMethod
		if !limiter.Allow(endpoint) {
			// Increment counter
			rateLimitExceededCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("endpoint", endpoint)))
			return nil, status.Errorf(codes.ResourceExhausted, "rate limit exceeded for endpoint %s", endpoint)
		}

		return handler(ctx, req)
	}
}
