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
	// For RPS calculation
	requestCounts map[string]*[10]int64
	countsMutex   sync.Mutex
}

var (
	rateLimitExceededCounter metric.Int64Counter
	rateLimitCurrentRPSGauge metric.Float64Gauge
)

func initRateLimitMetrics(meter metric.Meter) error {
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
		metric.WithDescription("Current measured requests per second for each endpoint over last 10 seconds"),
	)
	if err != nil {
		return err
	}

	return nil
}

func LoadRateLimitConfigFromEnv() map[string]int {
	config := make(map[string]int)
	prefix := "PRODUCT_CATALOG_RATELIMIT_"
	suffix := "_RPS"

	for _, e := range os.Environ() {
		pair := strings.SplitN(e, "=", 2)
		if len(pair) != 2 {
			continue
		}
		key := pair[0]
		value := pair[1]

		if strings.HasPrefix(key, prefix) && strings.HasSuffix(key, suffix) {
			limit, err := strconv.Atoi(value)
			if err != nil || limit <= 0 {
				continue
			}
			endpointName := strings.TrimSuffix(strings.TrimPrefix(key, prefix), suffix)
			config[endpointName] = limit
		}
	}

	return config
}

func NewRateLimiter(config map[string]int, defaultLimit int) *perEndpointRateLimiter {
	limiter := &perEndpointRateLimiter{
		limiters:      make(map[string]*rate.Limiter),
		defaultLimit:  rate.Limit(defaultLimit),
		requestCounts: make(map[string]*[10]int64),
	}

	for endpoint, limit := range config {
		if limit > 0 {
			limiter.limiters[endpoint] = rate.NewLimiter(rate.Limit(limit), limit)
		}
	}

	// Start RPS calculation goroutine
	go limiter.runRPSCalculator()

	return limiter
}

// NewPerEndpointRateLimiter is an alias for compatibility with main.go
func NewPerEndpointRateLimiter(config map[string]int, defaultLimit int) *perEndpointRateLimiter {
	return NewRateLimiter(config, defaultLimit)
}

func (l *perEndpointRateLimiter) getLimiter(endpoint string) *rate.Limiter {
	l.mu.RLock()
	lim, ok := l.limiters[endpoint]
	l.mu.RUnlock()
	if ok {
		return lim
	}

	if l.defaultLimit <= 0 {
		return nil
	}

	l.mu.Lock()
	defer l.mu.Unlock()
	// Double check after lock
	if lim, ok := l.limiters[endpoint]; ok {
		return lim
	}
	lim = rate.NewLimiter(l.defaultLimit, int(l.defaultLimit))
	l.limiters[endpoint] = lim
	return lim
}

func (l *perEndpointRateLimiter) recordRequest(endpoint string) {
	l.countsMutex.Lock()
	defer l.countsMutex.Unlock()

	if _, ok := l.requestCounts[endpoint]; !ok {
		l.requestCounts[endpoint] = &[10]int64{}
	}
	// Increment current second bucket (index 0 is current second)
	l.requestCounts[endpoint][0]++
}

func (l *perEndpointRateLimiter) runRPSCalculator() {
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		l.countsMutex.Lock()
		for endpoint, counts := range l.requestCounts {
			// Shift buckets
			for i := 9; i > 0; i-- {
				counts[i] = counts[i-1]
			}
			counts[0] = 0

			// Calculate average RPS over last 10 seconds
			var total int64
			for i := 1; i < 10; i++ {
				total += counts[i]
			}
			avgRPS := float64(total) / 9.0

			rateLimitCurrentRPSGauge.Record(context.Background(), avgRPS, metric.WithAttributes(attribute.String("endpoint", endpoint)))
		}
		l.countsMutex.Unlock()
	}
}

func RateLimitInterceptor(limiter *perEndpointRateLimiter) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		if limiter == nil {
			return handler(ctx, req)
		}

		// Convert method name to env var format
		endpointName := strings.ReplaceAll(strings.ToUpper(info.FullMethod), "/", "_")
		endpointLimiter := limiter.getLimiter(endpointName)

		// Record request for RPS calculation
		limiter.recordRequest(endpointName)

		if endpointLimiter == nil {
			// No rate limit applied
			return handler(ctx, req)
		}

		if !endpointLimiter.Allow() {
			// Increment rejected counter
			rateLimitExceededCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("endpoint", endpointName)))
			return nil, status.Errorf(codes.ResourceExhausted, "rate limit exceeded for endpoint %s", info.FullMethod)
		}

		return handler(ctx, req)
	}
}
