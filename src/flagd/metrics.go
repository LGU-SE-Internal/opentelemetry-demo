package main

import (
	"context"
	"net/http"
	"strconv"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

type Metrics struct {
	EvaluationRequestsTotal metric.Int64Counter
	EvaluationErrorsTotal   metric.Int64Counter
	EvaluationDuration      metric.Float64Histogram
	ActiveConfigsGauge      metric.Int64Gauge
	HTTPRequestsTotal       metric.Int64Counter
}

func NewMetrics(meter metric.Meter) (*Metrics, error) {
	evaluationRequestsTotal, err := meter.Int64Counter(
		"feature_flag.evaluation.requests.total",
		metric.WithDescription("Total number of feature flag evaluation requests (success + failure)"),
		metric.WithUnit("{request}"),
	)
	if err != nil {
		return nil, err
	}

	evaluationErrorsTotal, err := meter.Int64Counter(
		"feature_flag.evaluation.errors.total",
		metric.WithDescription("Total number of failed feature flag evaluation requests, grouped by error type"),
		metric.WithUnit("{error}"),
	)
	if err != nil {
		return nil, err
	}

	evaluationDuration, err := meter.Float64Histogram(
		"feature_flag.evaluation.duration",
		metric.WithDescription("Distribution of time taken to complete feature flag evaluations"),
		metric.WithUnit("ms"),
	)
	if err != nil {
		return nil, err
	}

	activeConfigsGauge, err := meter.Int64Gauge(
		"feature_flag.configs.active",
		metric.WithDescription("Number of currently loaded active feature flag configurations"),
		metric.WithUnit("{config}"),
	)
	if err != nil {
		return nil, err
	}

	httpRequestsTotal, err := meter.Int64Counter(
		"http.server.requests.total",
		metric.WithDescription("Total number of HTTP requests received by all service endpoints"),
		metric.WithUnit("{request}"),
	)
	if err != nil {
		return nil, err
	}

	return &Metrics{
		EvaluationRequestsTotal: evaluationRequestsTotal,
		EvaluationErrorsTotal:   evaluationErrorsTotal,
		EvaluationDuration:      evaluationDuration,
		ActiveConfigsGauge:      activeConfigsGauge,
		HTTPRequestsTotal:       httpRequestsTotal,
	}, nil
}

// MetricsMiddleware counts all HTTP requests with method, path, status_code labels
func MetricsMiddleware(metrics *Metrics, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		lrw := &loggingResponseWriter{ResponseWriter: w, statusCode: http.StatusOK}

		next.ServeHTTP(lrw, r)

		duration := time.Since(start)

		// Count HTTP request
		metrics.HTTPRequestsTotal.Add(r.Context(), 1, metric.WithAttributes(
			attribute.String("method", r.Method),
			attribute.String("path", r.URL.Path),
			attribute.String("status_code", strconv.Itoa(lrw.statusCode)),
		))

		// If this is an evaluation request, record evaluation metrics
		if r.Method == http.MethodPost && r.URL.Path == "/flags/v1/evaluate" {
			metrics.EvaluationRequestsTotal.Add(r.Context(), 1)
			metrics.EvaluationDuration.Record(r.Context(), float64(duration.Milliseconds()))
		}
	})
}

type loggingResponseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (lrw *loggingResponseWriter) WriteHeader(code int) {
	lrw.statusCode = code
	lrw.ResponseWriter.WriteHeader(code)
}
