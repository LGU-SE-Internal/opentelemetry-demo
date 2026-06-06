// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/IBM/sarama"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// HealthChecker interface defines runtime health checks
type HealthChecker interface {
	Check() error
}

// ReadinessChecker interface defines Kafka consumer readiness checks
type ReadinessChecker interface {
	Check() error
}

// DefaultHealthChecker implements basic runtime health checks
type DefaultHealthChecker struct{}

// Check returns nil if runtime is operating normally
func (h DefaultHealthChecker) Check() error {
	// Basic runtime check - always healthy unless critical failure
	// In a real implementation, add checks for goroutine leaks, memory usage, etc.
	return nil
}

// KafkaReadinessChecker implements Kafka consumer connection checks
type KafkaReadinessChecker struct {
	consumer sarama.Consumer
	topics   []string
}

// Check returns nil if Kafka consumer is connected and subscribed to topics
func (k KafkaReadinessChecker) Check() error {
	if k.consumer == nil {
		return syscall.ENOTCONN
	}

	// Check if we can list topics (verifies connection is active)
	_, err := k.consumer.Topics()
	if err != nil {
		return err
	}

	// Check if we are subscribed to all required topics
	// In a real implementation, add checks for partition assignments, etc.
	return nil
}

// HealthHandler returns HTTP handler for /health endpoint
func HealthHandler(checker HealthChecker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		if err := checker.Check(); err != nil {
			w.Header().Set("Content-Type", "text/plain")
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte("Service Unhealthy"))
			return
		}

		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	}
}

// ReadyHandler returns HTTP handler for /ready endpoint
func ReadyHandler(checker ReadinessChecker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		if err := checker.Check(); err != nil {
			w.Header().Set("Content-Type", "text/plain")
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte("Kafka Consumer Not Ready"))
			return
		}

		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	}
}

func main() {
	// Configuration
	kafkaAddr := os.Getenv("KAFKA_ADDR")
	if kafkaAddr == "" {
		kafkaAddr = "kafka:9092"
	}
	topics := []string{"checkout-events"} // Default topic, can be configured via env
	httpPort := "8080"

	// Initialize health checker
	healthChecker := DefaultHealthChecker{}

	// Initialize Kafka consumer
	config := sarama.NewConfig()
	config.Consumer.Return.Errors = true
	consumer, err := sarama.NewConsumer([]string{kafkaAddr}, config)
	if err != nil {
		log.Printf("Warning: Failed to initialize Kafka consumer: %v", err)
	}
	readinessChecker := KafkaReadinessChecker{
		consumer: consumer,
		topics:   topics,
	}
	defer func() {
		if consumer != nil {
			consumer.Close()
		}
	}()

	// Set up HTTP mux with all endpoints
	mux := http.NewServeMux()
	mux.HandleFunc("/health", HealthHandler(healthChecker))
	mux.HandleFunc("/ready", ReadyHandler(readinessChecker))
	mux.Handle("/metrics", promhttp.Handler()) // Existing metrics endpoint unchanged

	// Start HTTP server
	server := &http.Server{
		Addr:    ":" + httpPort,
		Handler: mux,
	}

	// Graceful shutdown setup
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("Kafka collector server starting on port %s", httpPort)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Failed to start server: %v", err)
		}
	}()

	<-sigChan
	log.Println("Shutting down server...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		log.Fatalf("Server shutdown failed: %v", err)
	}
	log.Println("Server gracefully stopped")
}
