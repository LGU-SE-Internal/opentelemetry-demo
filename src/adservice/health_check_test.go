package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/credentials/insecure"
)

const (
	defaultGRPCPort = 9555 // Default adservice gRPC port from existing config
)

func TestAC1_HealthEndpointReturnsOKWhenRunning(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to start
	time.Sleep(2 * time.Second)

	// Call /health endpoint
	resp, err := http.Get("http://localhost:8080/health")
	if err != nil {
		t.Fatalf("Failed to call /health endpoint: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Expected status 200 OK, got %d", resp.StatusCode)
	}

	var body map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("Failed to decode response body: %v", err)
	}

	if body["status"] != "ok" {
		t.Fatalf("Expected status 'ok' in response body, got '%s'", body["status"])
	}
}

func TestAC2_HealthEndpointReturns503WhenShuttingDown(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to start
	time.Sleep(2 * time.Second)

	// Trigger shutdown (simulate SIGTERM)
	// For test purposes, we can cancel context or send signal
	// Then wait for shutdown to start
	time.Sleep(1 * time.Second)

	// Call /health endpoint
	resp, err := http.Get("http://localhost:8080/health")
	if err != nil {
		t.Fatalf("Failed to call /health endpoint: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("Expected status 503 Service Unavailable, got %d", resp.StatusCode)
	}
}

func TestAC3_ReadinessEndpointReturnsOKWhenDBHealthy(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Ensure database is running and accessible (test assumes DB is up per test env)
	os.Setenv("AD_SERVICE_DB_HOST", "postgres")
	os.Setenv("AD_SERVICE_DB_PORT", "5432")

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to initialize DB connection
	time.Sleep(3 * time.Second)

	// Call /readiness endpoint
	resp, err := http.Get("http://localhost:8080/readiness")
	if err != nil {
		t.Fatalf("Failed to call /readiness endpoint: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Expected status 200 OK, got %d", resp.StatusCode)
	}

	var body map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("Failed to decode response body: %v", err)
	}

	if body["status"] != "ready" {
		t.Fatalf("Expected status 'ready' in response body, got '%s'", body["status"])
	}
	if body["database"] != "connected" {
		t.Fatalf("Expected database 'connected' in response body, got '%s'", body["database"])
	}
}

func TestAC4_ReadinessEndpointReturns503WhenDBDown(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Set invalid DB config to simulate DB down
	os.Setenv("AD_SERVICE_DB_HOST", "invalid-host")
	os.Setenv("AD_SERVICE_DB_PORT", "9999")

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to attempt DB connection
	time.Sleep(3 * time.Second)

	// Call /readiness endpoint
	resp, err := http.Get("http://localhost:8080/readiness")
	if err != nil {
		t.Fatalf("Failed to call /readiness endpoint: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("Expected status 503 Service Unavailable, got %d", resp.StatusCode)
	}

	var body map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("Failed to decode response body: %v", err)
	}

	if body["status"] != "not_ready" {
		t.Fatalf("Expected status 'not_ready' in response body, got '%s'", body["status"])
	}
	if body["database"] != "disconnected" {
		t.Fatalf("Expected database 'disconnected' in response body, got '%s'", body["database"])
	}
}

func TestAC5_HealthEndpointUsesDefaultPortWhenNoEnvVar(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Unset any existing AD_SERVICE_HEALTH_PORT env var
	os.Unsetenv("AD_SERVICE_HEALTH_PORT")

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to start
	time.Sleep(2 * time.Second)

	// Check if port 8080 is listening
	l, err := net.Listen("tcp", ":8080")
	if err == nil {
		l.Close()
		t.Fatalf("Port 8080 is not listening, expected health endpoints on default port 8080")
	}

	// Verify we can call /health on port 8080
	resp, err := http.Get("http://localhost:8080/health")
	if err != nil {
		t.Fatalf("Failed to call /health on default port 8080: %v", err)
	}
	resp.Body.Close()
}

func TestAC6_HealthEndpointUsesCustomPortFromEnvVar(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Set custom health port
	customPort := 18080
	os.Setenv("AD_SERVICE_HEALTH_PORT", fmt.Sprintf("%d", customPort))

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to start
	time.Sleep(2 * time.Second)

	// Check if custom port is listening
	l, err := net.Listen("tcp", fmt.Sprintf(":%d", customPort))
	if err == nil {
		l.Close()
		t.Fatalf("Port %d is not listening, expected health endpoints on custom port %d", customPort, customPort)
	}

	// Verify we can call /health on custom port
	resp, err := http.Get(fmt.Sprintf("http://localhost:%d/health", customPort))
	if err != nil {
		t.Fatalf("Failed to call /health on custom port %d: %v", customPort, err)
	}
	resp.Body.Close()
}

func TestAC7_GRPCHealthCheckReturnsServingWhenDBHealthy(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Ensure database is running and accessible
	os.Setenv("AD_SERVICE_DB_HOST", "postgres")
	os.Setenv("AD_SERVICE_DB_PORT", "5432")

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to start
	time.Sleep(3 * time.Second)

	// Connect to gRPC server on standard adservice port
	conn, err := grpc.DialContext(ctx, fmt.Sprintf("localhost:%d", defaultGRPCPort),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock())
	if err != nil {
		t.Fatalf("Failed to connect to gRPC server: %v", err)
	}
	defer conn.Close()

	healthClient := grpc_health_v1.NewHealthClient(conn)
	resp, err := healthClient.Check(ctx, &grpc_health_v1.HealthCheckRequest{})
	if err != nil {
		t.Fatalf("gRPC health check failed: %v", err)
	}

	if resp.Status != grpc_health_v1.HealthCheckResponse_SERVING {
		t.Fatalf("Expected gRPC health status SERVING, got %s", resp.Status.String())
	}
}

func TestAC8_GRPCHealthCheckReturnsNotServingWhenDBDown(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Set invalid DB config to simulate DB down
	os.Setenv("AD_SERVICE_DB_HOST", "invalid-host")
	os.Setenv("AD_SERVICE_DB_PORT", "9999")

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to attempt DB connection
	time.Sleep(3 * time.Second)

	// Connect to gRPC server on standard adservice port
	conn, err := grpc.DialContext(ctx, fmt.Sprintf("localhost:%d", defaultGRPCPort),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock())
	if err != nil {
		t.Fatalf("Failed to connect to gRPC server: %v", err)
	}
	defer conn.Close()

	healthClient := grpc_health_v1.NewHealthClient(conn)
	resp, err := healthClient.Check(ctx, &grpc_health_v1.HealthCheckRequest{})
	if err != nil {
		t.Fatalf("gRPC health check failed: %v", err)
	}

	if resp.Status != grpc_health_v1.HealthCheckResponse_NOT_SERVING {
		t.Fatalf("Expected gRPC health status NOT_SERVING, got %s", resp.Status.String())
	}
}

func TestAC9_GRPCHealthCheckOnExistingGRPCPort(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start adservice in background
	go func() {
		main()
	}()

	// Wait for service to start
	time.Sleep(2 * time.Second)

	// Verify gRPC health service is available on standard gRPC port, not health port
	conn, err := grpc.DialContext(ctx, fmt.Sprintf("localhost:%d", defaultGRPCPort),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock())
	if err != nil {
		t.Fatalf("Failed to connect to gRPC server on port %d: %v", defaultGRPCPort, err)
	}
	defer conn.Close()

	// List services to confirm health service is registered
	services, err := grpc.Invoke(ctx, "/grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo", nil, nil, conn)
	if err != nil {
		// If reflection is not enabled, just try calling health check directly
		healthClient := grpc_health_v1.NewHealthClient(conn)
		_, err := healthClient.Check(ctx, &grpc_health_v1.HealthCheckRequest{})
		if err != nil {
			t.Fatalf("gRPC health service not found on standard gRPC port %d: %v", defaultGRPCPort, err)
		}
	}

	// Verify health service is NOT available on HTTP health port
	healthPortConn, err := grpc.DialContext(ctx, "localhost:8080",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock(),
		grpc.WithTimeout(1*time.Second))
	if err == nil {
		defer healthPortConn.Close()
		healthClient := grpc_health_v1.NewHealthClient(healthPortConn)
		_, err := healthClient.Check(ctx, &grpc_health_v1.HealthCheckRequest{})
		if err == nil {
			t.Fatalf("gRPC health service should NOT be exposed on HTTP health port 8080")
		}
	}
}
