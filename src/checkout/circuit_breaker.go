// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/sony/gobreaker"
	"google.golang.org/grpc"
)

// ErrCircuitOpen is returned when a request is made while the circuit is in open state
var ErrCircuitOpen = gobreaker.ErrOpenState

// CircuitBreakerConfig defines circuit breaker settings per downstream service
type CircuitBreakerConfig struct {
	ServiceName             string
	FailureThresholdPercent int
	OpenStateTimeout        time.Duration
	HalfOpenMaxRequests     uint32
	RollingWindowDuration   time.Duration
}

var (
	circuitBreakerStateGauge = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "otelcheckout_circuit_breaker_state",
			Help: "Current state of the circuit breaker (0=Closed,1=Open,2=Half-Open)",
		},
		[]string{"service"},
	)

	circuitBreakerEventsCounter = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "otelcheckout_circuit_breaker_events_total",
			Help: "Total number of circuit breaker state transition events",
		},
		[]string{"service", "event_type"},
	)
)

func init() {
	prometheus.MustRegister(circuitBreakerStateGauge, circuitBreakerEventsCounter)
}

// NewCircuitBreaker creates a new circuit breaker with the given configuration
func NewCircuitBreaker(cfg CircuitBreakerConfig) *gobreaker.CircuitBreaker {
	return gobreaker.NewCircuitBreaker(gobreaker.Settings{
		Name:        cfg.ServiceName,
		MaxRequests: cfg.HalfOpenMaxRequests,
		Interval:    cfg.RollingWindowDuration,
		Timeout:     cfg.OpenStateTimeout,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			failureRatio := float64(counts.TotalFailures) / float64(counts.Requests)
			return counts.Requests >= 2 && failureRatio >= float64(cfg.FailureThresholdPercent)/100
		},
		OnStateChange: func(name string, from gobreaker.State, to gobreaker.State) {
			// Log structured JSON entry
			logger.Info(
				"circuit breaker state transition",
				"service_name", name,
				"previous_state", from.String(),
				"new_state", to.String(),
				"failure_count", gobreaker.NewCircuitBreaker(gobreaker.Settings{Name: name}).Counts().TotalFailures,
				"timestamp", time.Now().Format(time.RFC3339),
			)

			// Update prometheus metrics
			circuitBreakerStateGauge.WithLabelValues(name).Set(float64(to))

			var eventType string
			switch to {
			case gobreaker.StateClosed:
				eventType = "closed"
			case gobreaker.StateOpen:
				eventType = "open"
			case gobreaker.StateHalfOpen:
				eventType = "half_open"
			}
			circuitBreakerEventsCounter.WithLabelValues(name, eventType).Inc()
		},
	})
}

// CircuitBreakerClientInterceptor returns a gRPC unary client interceptor that wraps outgoing calls with circuit breaker protection
func CircuitBreakerClientInterceptor(cb *gobreaker.CircuitBreaker) grpc.UnaryClientInterceptor {
	return func(
		ctx context.Context,
		method string,
		req interface{},
		reply interface{},
		cc *grpc.ClientConn,
		invoker grpc.UnaryInvoker,
		opts ...grpc.CallOption,
	) error {
		_, err := cb.Execute(func() (interface{}, error) {
			err := invoker(ctx, method, req, reply, cc, opts...)
			return nil, err
		})

		if err == gobreaker.ErrOpenState {
			circuitBreakerEventsCounter.WithLabelValues(cb.Name(), "failure_rejected").Inc()
			return ErrCircuitOpen
		}

		return err
	}
}

// Global gRPC clients map for test access
var grpcClients = make(map[string]*grpc.ClientConn)

// Global circuit breakers per service
var (
	circuitBreakers = make(map[string]*gobreaker.CircuitBreaker)
	cbMu            sync.RWMutex
)

// GetAllGRPCClients returns all gRPC clients used by checkout service for test verification
func GetAllGRPCClients() map[string]*grpc.ClientConn {
	return grpcClients
}

// GetCircuitBreakerForService returns the circuit breaker instance for a given service, creating it with default config if it doesn't exist
func GetCircuitBreakerForService(svcName string) *gobreaker.CircuitBreaker {
	cbMu.RLock()
	cb, exists := circuitBreakers[svcName]
	cbMu.RUnlock()
	if exists {
		return cb
	}

	cbMu.Lock()
	defer cbMu.Unlock()
	// Double check in case it was created while waiting for lock
	if cb, exists := circuitBreakers[svcName]; exists {
		return cb
	}

	// Default configuration as per spec
	cfg := CircuitBreakerConfig{
		ServiceName:             svcName,
		FailureThresholdPercent: 50,
		OpenStateTimeout:        30 * time.Second,
		HalfOpenMaxRequests:     5,
		RollingWindowDuration:   10 * time.Second,
	}
	cb = NewCircuitBreaker(cfg)
	circuitBreakers[svcName] = cb
	return cb
}

// circuitBreakerUnaryInterceptor returns a gRPC unary client interceptor for the specified service
func circuitBreakerUnaryInterceptor(svcName string) grpc.UnaryClientInterceptor {
	cb := GetCircuitBreakerForService(svcName)
	return CircuitBreakerClientInterceptor(cb)
}
