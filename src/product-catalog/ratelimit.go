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
	limiters     map[string]*rate.Limiter
	defaultLimit rate.Limit
	mu           sync.RWMutex
	requestCounts map[string]*count
	countMu      sync.Mutex
}

type count struct {
	value int64
	lastReset time.Time
}

var (
	rateLimitExceededCounter metric.Int64Counter
	rateLimitCurrentRPSGauge metric.Float64Gauge
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

	rateLimitCurrentRPSGauge, err = meter.Float64Gauge(
		"product_catalog_rate_limit_current_rps",
		metric.WithDescription("Current measured requests per second for each endpoint over 10s window"),
	)
	if err != nil {
		return err
	}

	return nil
}

func NewPerEndpointRateLimiter() *perEndpointRateLimiter {
	defaultLimit := rate.Inf
	defaultRPSStr := os.Getenv("PRODUCT_CATALOG_RATELIMIT_DEFAULT_RPS")
	if defaultRPSStr != "" {
		if rps, err := strconv.Atoi(defaultRPSStr); err == nil && rps > 0 {
			defaultLimit = rate.Limit(rps)
		}
	}

	limiter := &perEndpointRateLimiter{
		limiters:      make(map[string]*rate.Limiter),
		defaultLimit:  defaultLimit,
		requestCounts: make(map[string]*count),
	}

	// Start background goroutine to update RPS gauge every second
	go func() {
		ticker := time.NewTicker(1 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			limiter.updateRPSGauges()
		}
	}()

	return limiter
}

func (l *perEndpointRateLimiter) getLimiter(endpoint string) *rate.Limiter {
	l.mu.RLock()
	lim, exists := l.limiters[endpoint]
	l.mu.RUnlock()
	if exists {
		return lim
	}

	l.mu.Lock()
	defer l.mu.Unlock()
	if lim, exists := l.limiters[endpoint]; exists {
		return lim
	}

	// Check for endpoint specific env var
	envKey := fmt.Sprintf("PRODUCT_CATALOG_RATELIMIT_%s_RPS", strings.ToUpper(strings.ReplaceAll(endpoint, "/", "_")))
	limit := l.defaultLimit
	if rpsStr := os.Getenv(envKey); rpsStr != "" {
		if rps, err := strconv.Atoi(rpsStr); err == nil && rps > 0 {
			limit = rate.Limit(rps)
		}
	}

	lim = rate.NewLimiter(limit, int(limit)) // Burst size equal to RPS
	l.limiters[endpoint] = lim
	return lim
}

func (l *perEndpointRateLimiter) Allow(endpoint string) bool {
	lim := l.getLimiter(endpoint)
	allowed := lim.Allow()

	// Track request count for RPS calculation
	l.countMu.Lock()
	defer l.countMu.Unlock()
	c, exists := l.requestCounts[endpoint]
	if !exists {
		c = &count{lastReset: time.Now()}
		l.requestCounts[endpoint] = c
	}
	c.value++

	return allowed
}

func (l *perEndpointRateLimiter) updateRPSGauges() {
	l.countMu.Lock()
	defer l.countMu.Unlock()

	now := time.Now()
	for endpoint, c := range l.requestCounts {
		duration := now.Sub(c.lastReset).Seconds()
		if duration >= 10 {
			rps := float64(c.value) / duration
			rateLimitCurrentRPSGauge.Record(context.Background(), rps, metric.WithAttributes(attribute.String("endpoint", endpoint)))
			// Reset count
			c.value = 0
			c.lastReset = now
		}
	}
}

func RateLimitInterceptor(limiter *perEndpointRateLimiter) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		if limiter.defaultLimit == rate.Inf && len(limiter.limiters) == 0 {
			// No rate limits configured, skip processing
			return handler(ctx, req)
		}

		endpoint := info.FullMethod
		if !limiter.Allow(endpoint) {
			// Increment exceeded counter
			rateLimitExceededCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("endpoint", endpoint)))
			return nil, status.Errorf(codes.ResourceExhausted, "rate limit exceeded for endpoint %s", endpoint)
		}

		return handler(ctx, req)
	}
}
