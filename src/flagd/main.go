package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.opentelemetry.io/otel/exporters/prometheus"
	"go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
)

func main() {
	// Initialize OpenTelemetry Metrics SDK with Prometheus exporter
	res, err := resource.New(context.Background(),
		resource.WithAttributes(
			semconv.ServiceNameKey.String("flagd"),
			semconv.ServiceVersionKey.String("1.0.0"),
		),
	)
	if err != nil {
		log.Fatalf("failed to create resource: %v", err)
	}

	exporter, err := prometheus.New()
	if err != nil {
		log.Fatalf("failed to create prometheus exporter: %v", err)
	}

	provider := metric.NewMeterProvider(
		metric.WithResource(res),
		metric.WithReader(exporter),
	)
	defer func() {
		if err := provider.Shutdown(context.Background()); err != nil {
			log.Fatalf("failed to shutdown meter provider: %v", err)
		}
	}()

	// Initialize metrics
	meter := provider.Meter("flagd")
	metrics, err := NewMetrics(meter)
	if err != nil {
		log.Fatalf("failed to initialize metrics: %v", err)
	}

	// Load feature flag configurations
	config, err := loadFlagConfig("./demo.flagd.json")
	if err != nil {
		log.Fatalf("failed to load flag config: %v", err)
	}
	metrics.ActiveConfigsGauge.Set(context.Background(), int64(len(config.Flags)))

	// Create evaluation service
	evalService := NewEvaluationService(metrics, config)

	// Create main server (port 8080)
	mainMux := http.NewServeMux()
	mainMux.HandleFunc("POST /flags/v1/evaluate", evalService.EvaluateHandler)

	// Apply metrics middleware to main server
	mainServer := &http.Server{
		Addr:    ":8080",
		Handler: MetricsMiddleware(metrics, mainMux),
	}

	// Create metrics server (port 8016)
	metricsMux := http.NewServeMux()
	metricsMux.Handle("/metrics", exporter)
	// Apply metrics middleware to metrics server as well
	metricsServer := &http.Server{
		Addr:    ":8016",
		Handler: MetricsMiddleware(metrics, metricsMux),
	}

	// Start servers in goroutines
	go func() {
		log.Printf("Starting main server on :8080")
		if err := mainServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("main server failed: %v", err)
		}
	}()

	go func() {
		log.Printf("Starting metrics server on :8016")
		if err := metricsServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("metrics server failed: %v", err)
		}
	}()

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	<-sigChan

	log.Println("Shutting down servers...")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if err := mainServer.Shutdown(ctx); err != nil {
		log.Fatalf("main server shutdown failed: %v", err)
	}
	if err := metricsServer.Shutdown(ctx); err != nil {
		log.Fatalf("metrics server shutdown failed: %v", err)
	}

	log.Println("Servers shutdown successfully")
}

type FlagConfig struct {
	Flags map[string]interface{} `json:"flags"`
}

func loadFlagConfig(path string) (*FlagConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var config FlagConfig
	if err := json.Unmarshal(data, &config); err != nil {
		return nil, err
	}
	return &config, nil
}
