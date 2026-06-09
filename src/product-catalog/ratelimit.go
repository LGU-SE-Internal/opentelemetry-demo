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
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	rateLimitExceeded = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "product_catalog_rate_limit_exceeded_total",
		Help: "Total number of requests rejected due to rate limiting",
	}, []string{"endpoint"})

	currentRPS = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "product_catalog_rate_limit_current_rps",
		Help: "Current measured requests per second for each endpoint over last 10s window",
	}, []string{"endpoint"})
)

type perEndpointRateLimiter struct {
	defaultLimit rate.Limit
	limits       map[string]rate.Limit
	limiters     map[string]*rate.Limiter
	mu           sync.RWMutex
	requestTimes map[string][]time.Time
	windowSize   time.Duration
}

func NewPerEndpointRateLimiter() *perEndpointRateLimiter {
	defaultLimit := rate.Inf
	defaultStr := os.Getenv("PRODUCT_CATALOG_RATELIMIT_DEFAULT_RPS")
	if defaultStr != "" {
		n, err := strconv.Atoi(defaultStr)
		if err == nil && n > 0 {
			defaultLimit = rate.Limit(n)
		}
	}

	limits := make(map[string]rate.Limit)
	for _, e := range os.Environ() {
		pair := strings.SplitN(e, "=", 2)
		if len(pair) != 2 {
			continue
		}
		key := pair[0]
		if strings.HasPrefix(key, "PRODUCT_CATALOG_RATELIMIT_") && strings.HasSuffix(key, "_RPS") {
			endpoint := strings.TrimSuffix(strings.TrimPrefix(key, "PRODUCT_CATALOG_RATELIMIT_"), "_RPS")
			n, err := strconv.Atoi(pair[1])
			if err == nil && n > 0 {
				limits[endpoint] = rate.Limit(n)
			}
		}
	}

	rl := &perEndpointRateLimiter{
		defaultLimit: defaultLimit,
		limits:       limits,
		limiters:     make(map[string]*rate.Limiter),
		requestTimes: make(map[string][]time.Time),
		windowSize:   10 * time.Second,
	}

	go rl.updateRPSGaugeLoop()

	return rl
}

func (rl *perEndpointRateLimiter) getLimiter(endpoint string) *rate.Limiter {
	rl.mu.RLock()
	l, exists := rl.limiters[endpoint]
	rl.mu.RUnlock()
	if exists {
		return l
	}

	rl.mu.Lock()
	defer rl.mu.Unlock()
	l, exists = rl.limiters[endpoint]
	if exists {
		return l
	}

	limit, ok := rl.limits[endpoint]
	if !ok {
		limit = rl.defaultLimit
	}
	l = rate.NewLimiter(limit, int(limit))
	rl.limiters[endpoint] = l
	return l
}

func (rl *perEndpointRateLimiter) Allow(endpoint string) bool {
	l := rl.getLimiter(endpoint)
	allowed := l.Allow()

	rl.mu.Lock()
	defer rl.mu.Unlock()
	now := time.Now()
	rl.requestTimes[endpoint] = append(rl.requestTimes[endpoint], now)

	return allowed
}

func (rl *perEndpointRateLimiter) updateRPSGaugeLoop() {
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		rl.mu.Lock()
		now := time.Now()
		for endpoint, times := range rl.requestTimes {
			cutoff := now.Add(-rl.windowSize)
			valid := 0
			for _, t := range times {
				if t.After(cutoff) {
					times[valid] = t
					valid++
				}
			}
			rl.requestTimes[endpoint] = times[:valid]
			rps := float64(valid) / rl.windowSize.Seconds()
			currentRPS.WithLabelValues(endpoint).Set(rps)
		}
		rl.mu.Unlock()
	}
}

func RateLimitInterceptor(limiter *perEndpointRateLimiter) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		endpoint := strings.ToUpper(strings.ReplaceAll(info.FullMethod, "/", "_"))
		if !limiter.Allow(endpoint) {
			rateLimitExceeded.WithLabelValues(endpoint).Inc()
			return nil, status.Error(codes.ResourceExhausted, fmt.Sprintf("rate limit exceeded for endpoint %s", endpoint))
		}
		return handler(ctx, req)
	}
}
