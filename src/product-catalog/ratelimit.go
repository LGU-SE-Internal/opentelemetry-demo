// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type perEndpointRateLimiter struct {
	mu               sync.RWMutex
	limiters         map[string]*rate.Limiter
	defaultLimiter   *rate.Limiter
	exceededCounter  metric.Int64Counter
	currentRPSGauge  metric.Float64Gauge
	requestCounts    map[string]*int64
	countsMutex      sync.Mutex
}

func NewPerEndpointRateLimiter(meter metric.Meter) (*perEndpointRateLimiter, error) {
	rl := &perEndpointRateLimiter{
		limiters:      make(map[string]*rate.Limiter),
		requestCounts: make(map[string]*int64),
	}

	// Initialize metrics
	exceededCounter, err := meter.Int64Counter(
		"product_catalog_rate_limit_exceeded_total",
		metric.WithDescription("Total number of requests rejected due to rate limiting"),
	)
	if err != nil {
		return nil, err
	}
	rl.exceededCounter = exceededCounter

	currentRPSGauge, err := meter.Float64Gauge(
		"product_catalog_rate_limit_current_rps",
		metric.WithDescription("Current measured requests per second for each endpoint"),
	)
	if err != nil {
		return nil, err
	}
	rl.currentRPSGauge = currentRPSGauge

	// Load default limit first
	defaultRPSStr := os.Getenv("PRODUCT_CATALOG_RATELIMIT_DEFAULT_RPS")
	if defaultRPSStr != "" {
		defaultRPS, err := strconv.Atoi(defaultRPSStr)
		if err == nil && defaultRPS > 0 {
			rl.defaultLimiter = rate.NewLimiter(rate.Limit(defaultRPS), defaultRPS)
		}
	}

	// Load endpoint-specific limits
	prefix := "PRODUCT_CATALOG_RATELIMIT_"
	suffix := "_RPS"
	for _, env := range os.Environ() {
		if strings.HasPrefix(env, prefix) && strings.HasSuffix(env, suffix) {
			parts := strings.SplitN(env, "=", 2)
			if len(parts) != 2 {
				continue
			}
			key := parts[0]
			value := parts[1]

			// Parse RPS value
			rps, err := strconv.Atoi(value)
			if err != nil || rps <= 0 {
				continue
			}

			// Extract endpoint name
			endpointKey := strings.TrimSuffix(strings.TrimPrefix(key, prefix), suffix)
			// Convert back from env var format to gRPC method format (underscores to slashes, lowercase)
			endpointName := strings.ReplaceAll(strings.ToLower(endpointKey), "_", "/")
			rl.limiters[endpointName] = rate.NewLimiter(rate.Limit(rps), rps)
			rl.requestCounts[endpointName] = new(int64)
		}
	}

	// If default limiter exists, add its count tracker
	if rl.defaultLimiter != nil {
		rl.requestCounts["default"] = new(int64)
	}

	// Start background goroutine to update RPS gauge every second
	go rl.updateRPSGaugeLoop()

	return rl, nil
}

func (rl *perEndpointRateLimiter) updateRPSGaugeLoop() {
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	// We'll keep a 10-second window of counts
	windowSize := 10
	var window []map[string]int64
	for i := 0; i < windowSize; i++ {
		window = append(window, make(map[string]int64))
	}
	windowIndex := 0

	for range ticker.C {
		rl.countsMutex.Lock()
		// Capture current counts and reset
		currentCounts := make(map[string]int64)
		for endpoint, count := range rl.requestCounts {
			currentCounts[endpoint] = *count
			*count = 0
		}
		rl.countsMutex.Unlock()

		// Update window
		window[windowIndex] = currentCounts
		windowIndex = (windowIndex + 1) % windowSize

		// Calculate average RPS over 10-second window
		avgCounts := make(map[string]float64)
		for _, w := range window {
			for endpoint, cnt := range w {
				avgCounts[endpoint] += float64(cnt)
			}
		}
		for endpoint, total := range avgCounts {
			avg := total / float64(windowSize)
			rl.currentRPSGauge.Record(context.Background(), avg, metric.WithAttributes(
				attribute.String("endpoint", endpoint),
			))
		}
	}
}

func (rl *perEndpointRateLimiter) Allow(endpoint string) bool {
	// Increment request count
	rl.countsMutex.Lock()
	if _, ok := rl.requestCounts[endpoint]; !ok {
		rl.requestCounts[endpoint] = new(int64)
	}
	*rl.requestCounts[endpoint]++
	rl.countsMutex.Unlock()

	rl.mu.RLock()
	defer rl.mu.RUnlock()

	// Check endpoint-specific limiter first
	if limiter, ok := rl.limiters[endpoint]; ok {
		return limiter.Allow()
	}

	// Fall back to default limiter if exists
	if rl.defaultLimiter != nil {
		return rl.defaultLimiter.Allow()
	}

	// No limits, allow all
	return true
}

func RateLimitInterceptor(limiter *perEndpointRateLimiter) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		// If no limiter is configured, just pass through
		if limiter == nil {
			return handler(ctx, req)
		}

		endpoint := info.FullMethod
		if !limiter.Allow(endpoint) {
			// Increment exceeded metric
			limiter.exceededCounter.Add(ctx, 1, metric.WithAttributes(
				attribute.String("endpoint", endpoint),
			))
			return nil, status.Errorf(codes.ResourceExhausted, "rate limit exceeded for endpoint %s", endpoint)
		}

		// Call the handler
		return handler(ctx, req)
	}
}
