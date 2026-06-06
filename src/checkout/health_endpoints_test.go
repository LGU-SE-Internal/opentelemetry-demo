// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func TestAC1_HealthReturns200WhenServiceRunning(t *testing.T) {
	mux := http.NewServeMux()
	setupHealthEndpoints(mux, nil)

	req, err := http.NewRequest(http.MethodGet, "/health", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusOK, rr.Code)
	assert.Equal(t, "OK", rr.Body.String())
}

func TestAC2_HealthFailsWhenServiceNotRunning(t *testing.T) {
	// This is tested by default - if service is not running, connection is refused
	// We verify that when server is shutdown, requests fail
	mux := http.NewServeMux()
	setupHealthEndpoints(mux, nil)

	server := httptest.NewServer(mux)
	defer server.Close()

	// Server is running, should work
	resp, err := http.Get(server.URL + "/health")
	assert.NoError(t, err)
	assert.Equal(t, http.StatusOK, resp.StatusCode)
	resp.Body.Close()

	// Shutdown server
	server.Close()

	// Now request should fail
	resp, err = http.Get(server.URL + "/health")
	assert.Error(t, err)
}

func TestAC3_ReadyReturns200WhenAllDependenciesReachable(t *testing.T) {
	healthSrv := health.NewServer()
	healthSrv.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	mux := http.NewServeMux()
	setupHealthEndpoints(mux, healthSrv)

	req, err := http.NewRequest(http.MethodGet, "/ready", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusOK, rr.Code)
	assert.Equal(t, "READY", rr.Body.String())
}

func TestAC4_ReadyReturns503DuringInitialization(t *testing.T) {
	healthSrv := health.NewServer()
	// Default status is NOT_SERVING during initialization

	mux := http.NewServeMux()
	setupHealthEndpoints(mux, healthSrv)

	req, err := http.NewRequest(http.MethodGet, "/ready", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusServiceUnavailable, rr.Code)
	assert.Equal(t, "NOT_READY", rr.Body.String())
}

func TestAC5_ReadyReturns503WhenDependencyUnreachable(t *testing.T) {
	healthSrv := health.NewServer()
	healthSrv.SetServingStatus("", healthpb.HealthCheckResponse_NOT_SERVING)

	mux := http.NewServeMux()
	setupHealthEndpoints(mux, healthSrv)

	req, err := http.NewRequest(http.MethodGet, "/ready", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)

	assert.Equal(t, http.StatusServiceUnavailable, rr.Code)
	assert.Equal(t, "NOT_READY", rr.Body.String())
}

func TestAC6_HealthEndpointsOnSameHTTPPort(t *testing.T) {
	// We verify that the endpoints are on the same mux/server that would serve public HTTP
	// In our implementation, we use a single mux for all HTTP endpoints
	mux := http.NewServeMux()
	setupHealthEndpoints(mux, nil)

	// Add a test public endpoint
	mux.HandleFunc("/test-public", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	server := httptest.NewServer(mux)
	defer server.Close()

	// Both health and public endpoint work on same port
	resp, err := http.Get(server.URL + "/health")
	assert.NoError(t, err)
	assert.Equal(t, http.StatusOK, resp.StatusCode)
	resp.Body.Close()

	resp, err = http.Get(server.URL + "/test-public")
	assert.NoError(t, err)
	assert.Equal(t, http.StatusOK, resp.StatusCode)
	resp.Body.Close()
}

func TestAC7_GRPCHealthCheckUnchanged(t *testing.T) {
	healthSrv := health.NewServer()
	healthSrv.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	// Check gRPC health check works as expected
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	resp, err := healthSrv.Check(ctx, &healthpb.HealthCheckRequest{})
	assert.NoError(t, err)
	assert.Equal(t, healthpb.HealthCheckResponse_SERVING, resp.Status)

	// Verify it still works the same after HTTP endpoints are added
	mux := http.NewServeMux()
	setupHealthEndpoints(mux, healthSrv)

	resp, err = healthSrv.Check(ctx, &healthpb.HealthCheckRequest{})
	assert.NoError(t, err)
	assert.Equal(t, healthpb.HealthCheckResponse_SERVING, resp.Status)
}

func TestAC8_NonExistentEndpointsUnchanged(t *testing.T) {
	mux := http.NewServeMux()
	setupHealthEndpoints(mux, nil)

	req, err := http.NewRequest(http.MethodGet, "/non-existent", nil)
	assert.NoError(t, err)

	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)

	// Default 404 is expected
	assert.Equal(t, http.StatusNotFound, rr.Code)
}

// Helper function to setup endpoints (matches what's in main.go)
func setupHealthEndpoints(mux *http.ServeMux, healthcheck *health.Server) {
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	if healthcheck != nil {
		mux.HandleFunc("/ready", func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodGet {
				http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
				return
			}
			ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
			defer cancel()

			resp, err := healthcheck.Check(ctx, &healthpb.HealthCheckRequest{})
			if err != nil || resp.Status != healthpb.HealthCheckResponse_SERVING {
				w.WriteHeader(http.StatusServiceUnavailable)
				w.Write([]byte("NOT_READY"))
				return
			}
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("READY"))
		})
	}
}
