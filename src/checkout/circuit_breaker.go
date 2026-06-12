package main

import (
	"context"
	"errors"
	"time"

	"github.com/sony/gobreaker"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"log/slog"
)

var ErrCircuitOpen = errors.New("circuit breaker is open")

type CircuitBreakerConfig struct {
	ServiceName             string
	FailureThresholdPercent int
	OpenStateTimeout        time.Duration
	HalfOpenMaxRequests     uint32
	RollingWindowDuration   time.Duration
}

var (
	circuitBreakerStateGauge metric.Int64Gauge
	circuitBreakerEvents     metric.Int64Counter
)

func initMetrics(meter metric.Meter) error {
	var err error
	circuitBreakerStateGauge, err = meter.Int64Gauge(
		"otelcheckout_circuit_breaker_state",
		metric.WithDescription("Current state of the circuit breaker: 0 = Closed, 1 = Open, 2 = Half-Open"),
	)
	if err != nil {
		return err
	}

	circuitBreakerEvents, err = meter.Int64Counter(
		"otelcheckout_circuit_breaker_events_total",
		metric.WithDescription("Total count of circuit breaker state transition events"),
	)
	return err
}

func NewCircuitBreaker(config CircuitBreakerConfig, meter metric.Meter) (*gobreaker.CircuitBreaker, error) {
	if err := initMetrics(meter); err != nil {
		return nil, err
	}

	var cb *gobreaker.CircuitBreaker

	settings := gobreaker.Settings{
		Name:        config.ServiceName,
		MaxRequests: config.HalfOpenMaxRequests,
		Interval:    config.RollingWindowDuration,
		Timeout:     config.OpenStateTimeout,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			total := counts.Requests
			if total == 0 {
				return false
			}
			failureRatio := float64(counts.TotalFailures) / float64(total)
			return failureRatio >= float64(config.FailureThresholdPercent)/100.0
		},
		OnStateChange: func(name string, from gobreaker.State, to gobreaker.State) {
			counts := cb.Counts()
			slog.Info("circuit breaker state transition",
				slog.String("service_name", name),
				slog.String("previous_state", from.String()),
				slog.String("new_state", to.String()),
				slog.Uint64("failure_count", uint64(counts.TotalFailures)),
				slog.Time("timestamp", time.Now()),
			)

			// Update metrics
			stateVal := int64(0)
			eventType := ""
			switch to {
			case gobreaker.StateClosed:
				stateVal = 0
				eventType = "closed"
			case gobreaker.StateOpen:
				stateVal = 1
				eventType = "open"
			case gobreaker.StateHalfOpen:
				stateVal = 2
				eventType = "half_open"
			}

			circuitBreakerStateGauge.Record(context.Background(), stateVal, metric.WithAttributes(
				attribute.String("service", name),
			))

			circuitBreakerEvents.Add(context.Background(), 1, metric.WithAttributes(
				attribute.String("service", name),
				attribute.String("event_type", eventType),
			))
		},
	}

	cb = gobreaker.NewCircuitBreaker(settings)
	return cb, nil
}

func CircuitBreakerClientInterceptor(cb *gobreaker.CircuitBreaker) grpc.UnaryClientInterceptor {
	return func(
		ctx context.Context,
		method string,
		req, reply interface{},
		cc *grpc.ClientConn,
		invoker grpc.UnaryInvoker,
		opts ...grpc.CallOption,
	) error {
		_, err := cb.Execute(func() (interface{}, error) {
			invErr := invoker(ctx, method, req, reply, cc, opts...)
			if invErr != nil {
				// Check if error is a gRPC error we should count as failure
				st, ok := status.FromError(invErr)
				if ok {
					switch st.Code() {
					case codes.DeadlineExceeded, codes.Unavailable, codes.Internal, codes.ResourceExhausted:
						return nil, invErr
					}
				}
				// Don't count other errors (like invalid argument, not found, etc.) as failures
				return nil, nil
			}
			return nil, nil
		})

		if errors.Is(err, gobreaker.ErrOpenState) {
			circuitBreakerEvents.Add(ctx, 1, metric.WithAttributes(
				attribute.String("service", cb.Name()),
				attribute.String("event_type", "failure_rejected"),
			))
			return ErrCircuitOpen
		}

		return err
	}
}

// DefaultCircuitBreakerConfig returns default config for a given service
func DefaultCircuitBreakerConfig(serviceName string) CircuitBreakerConfig {
	return CircuitBreakerConfig{
		ServiceName:             serviceName,
		FailureThresholdPercent: 50,
		OpenStateTimeout:        30 * time.Second,
		HalfOpenMaxRequests:     5,
		RollingWindowDuration:   10 * time.Second,
	}
}
