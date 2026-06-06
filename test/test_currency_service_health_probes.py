#!/usr/bin/env python3
import os
import sys
import time
import signal
import subprocess
import requests
import grpc
from typing import Optional
# Import protobuf definitions for currency service
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'pb'))
import demo_pb2
import demo_pb2_grpc

DEFAULT_CURRENCY_SERVICE_ADDR = "localhost:7000"
DEFAULT_HEALTH_PORT = 8081
SERVICE_BINARY = os.path.join(os.path.dirname(__file__), '..', 'src', 'currency', 'server')

def start_currency_service(health_port: Optional[int] = None, wait_for_init: bool = True) -> subprocess.Popen:
    """Start the currency service subprocess with optional health port configuration"""
    env = os.environ.copy()
    env["PORT"] = DEFAULT_CURRENCY_SERVICE_ADDR.split(":")[1]
    if health_port is not None:
        env["CURRENCY_SERVICE_HEALTH_PORT"] = str(health_port)
    proc = subprocess.Popen(
        [SERVICE_BINARY],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for service to start
    time.sleep(1 if not wait_for_init else 3)
    return proc

def get_health_endpoint(port: int, endpoint: str) -> int:
    """Make GET request to health endpoint and return status code, or -1 on connection error"""
    try:
        response = requests.get(f"http://localhost:{port}/{endpoint.lstrip('/')}", timeout=2)
        return response.status_code
    except requests.exceptions.RequestException:
        return -1

def test_ac1_health_server_default_port():
    """AC-1: When CURRENCY_SERVICE_HEALTH_PORT env var is not set, HTTP health server listens on port 8081"""
    proc = start_currency_service(health_port=None, wait_for_init=True)
    try:
        status = get_health_endpoint(DEFAULT_HEALTH_PORT, "health")
        assert status == 200, f"Expected 200 OK on default port 8081, got {status}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac2_health_server_custom_port():
    """AC-2: When CURRENCY_SERVICE_HEALTH_PORT env var is set to valid unused port, server listens on specified port"""
    custom_port = 9876
    proc = start_currency_service(health_port=custom_port, wait_for_init=True)
    try:
        # Check custom port works
        status_custom = get_health_endpoint(custom_port, "health")
        assert status_custom == 200, f"Expected 200 OK on custom port {custom_port}, got {status_custom}"
        # Check default port does NOT work
        status_default = get_health_endpoint(DEFAULT_HEALTH_PORT, "health")
        assert status_default == -1, f"Expected no server on default port 8081 when custom port is set, got {status_default}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac3_health_endpoint_immediate_success():
    """AC-3: Immediately after service process starts, GET /health returns 200 OK"""
    proc = start_currency_service(wait_for_init=False)
    try:
        # Check health immediately after process starts (1s wait only)
        status = get_health_endpoint(DEFAULT_HEALTH_PORT, "health")
        assert status == 200, f"Expected 200 OK on /health immediately after startup, got {status}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac4_ready_endpoint_before_init_returns_503():
    """AC-4: Before service completes initialization, GET /ready returns 503 Service Unavailable"""
    proc = start_currency_service(wait_for_init=False)
    try:
        # Check ready immediately after process starts, before initialization completes
        status = get_health_endpoint(DEFAULT_HEALTH_PORT, "ready")
        assert status == 503, f"Expected 503 Service Unavailable on /ready before initialization, got {status}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac5_ready_endpoint_after_init_returns_200():
    """AC-5: After service completes initialization successfully, GET /ready returns 200 OK"""
    proc = start_currency_service(wait_for_init=True)
    try:
        status = get_health_endpoint(DEFAULT_HEALTH_PORT, "ready")
        assert status == 200, f"Expected 200 OK on /ready after initialization, got {status}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac6_health_endpoint_unhealthy_state_returns_503():
    """AC-6: If service enters unhealthy state at runtime, GET /health returns 503 Service Unavailable"""
    proc = start_currency_service(wait_for_init=True)
    try:
        # Verify healthy first
        status_healthy = get_health_endpoint(DEFAULT_HEALTH_PORT, "health")
        assert status_healthy == 200, "Expected 200 OK on /health when service is healthy"
        
        # Send SIGTERM to trigger shutdown/unhealthy state
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        
        # Verify health returns 503 during shutdown
        status_unhealthy = get_health_endpoint(DEFAULT_HEALTH_PORT, "health")
        assert status_unhealthy == 503, f"Expected 503 Service Unavailable on /health during shutdown, got {status_unhealthy}"
    finally:
        proc.wait(timeout=5)

def test_ac7_ready_endpoint_runtime_unavailable_returns_503():
    """AC-7: If service is unable to serve conversion requests at runtime, GET /ready returns 503 Service Unavailable"""
    proc = start_currency_service(wait_for_init=True)
    try:
        # Verify ready first
        status_ready = get_health_endpoint(DEFAULT_HEALTH_PORT, "ready")
        assert status_ready == 200, "Expected 200 OK on /ready when service is healthy"
        
        # Send SIGTERM to trigger shutdown state where service can't serve requests
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        
        status_not_ready = get_health_endpoint(DEFAULT_HEALTH_PORT, "ready")
        assert status_not_ready == 503, f"Expected 503 Service Unavailable on /ready when service can't serve requests, got {status_not_ready}"
    finally:
        proc.wait(timeout=5)

def test_ac8_grpc_functionality_unchanged():
    """AC-8: Existing gRPC service functionality and gRPC health check endpoint continue to work correctly"""
    proc = start_currency_service(wait_for_init=True)
    try:
        # Test gRPC conversion request works
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            response = stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD",
                to_currency="EUR",
                units=100,
                nanos=0
            ))
            assert response is not None and response.units > 0, "gRPC conversion request failed"
        
            # Test gRPC health check works
            health_stub = demo_pb2_grpc.HealthStub(channel)
            health_response = health_stub.Check(demo_pb2.HealthCheckRequest(service=""))
            assert health_response.status == demo_pb2.HealthCheckResponse.SERVING, "gRPC health check failed"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
