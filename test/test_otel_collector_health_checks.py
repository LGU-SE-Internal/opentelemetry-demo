#!/usr/bin/env python3
import requests
import pytest
import time

COLLECTOR_HOST = "localhost"
HEALTH_CHECK_PORT = 13133
EXISTING_ENDPOINTS = [
    ("OTLP gRPC", 4317, "/"),
    ("OTLP HTTP", 4318, "/"),
    ("Zipkin", 9411, "/"),
    ("Prometheus", 8889, "/metrics"),
]

def test_ac1_liveness_endpoint_returns_200_when_collector_running():
    """AC-1: Liveness endpoint / on port 13133 returns 200 OK when collector process is running."""
    url = f"http://{COLLECTOR_HOST}:{HEALTH_CHECK_PORT}/"
    try:
        response = requests.get(url, timeout=5)
        assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}"
    except requests.exceptions.ConnectionError:
        pytest.fail("Could not connect to collector health check endpoint (port 13133 not open)")

def test_ac2_readiness_endpoint_returns_200_when_pipelines_ready():
    """AC-2: Readiness endpoint /ready on port 13133 returns 200 OK when pipelines are fully initialized."""
    url = f"http://{COLLECTOR_HOST}:{HEALTH_CHECK_PORT}/ready"
    # Wait up to 30 seconds for collector to initialize
    for _ in range(30):
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                break
            time.sleep(1)
        except requests.exceptions.ConnectionError:
            time.sleep(1)
    else:
        pytest.fail("Readiness endpoint did not return 200 OK within 30 seconds")

def test_ac3_readiness_endpoint_returns_503_during_startup():
    """AC-3: Readiness endpoint /ready returns 503 when collector is starting up and pipelines are not ready."""
    # NOTE: This test assumes collector is restarted before test runs
    url = f"http://{COLLECTOR_HOST}:{HEALTH_CHECK_PORT}/ready"
    # Check immediately after startup (within first 5 seconds)
    try:
        response = requests.get(url, timeout=2)
        assert response.status_code == 503, f"Expected 503 Service Unavailable during startup, got {response.status_code}"
    except requests.exceptions.ConnectionError:
        # Expected if port isn't open yet during very early startup
        pass

@pytest.mark.parametrize("service_name, port, path", EXISTING_ENDPOINTS)
def test_ac4_existing_endpoints_remain_functional(service_name, port, path):
    """AC-4: All existing collector endpoints continue to work as before configuration change."""
    url = f"http://{COLLECTOR_HOST}:{port}{path}"
    try:
        # For gRPC endpoints we just check connectivity, for HTTP endpoints check response
        if service_name == "OTLP gRPC":
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((COLLECTOR_HOST, port))
            assert result == 0, f"Could not connect to {service_name} endpoint on port {port}"
            sock.close()
        else:
            response = requests.get(url, timeout=5)
            # We expect either 200 or 405 (method not allowed for POST endpoints) but not connection errors
            assert response.status_code in (200, 405), f"Unexpected status code {response.status_code} for {service_name} endpoint"
    except requests.exceptions.ConnectionError:
        pytest.fail(f"Could not connect to {service_name} endpoint on port {port}")

def test_ac5_health_check_port_exposed():
    """AC-5: Health check port 13133 is exposed and accessible."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    result = sock.connect_ex((COLLECTOR_HOST, HEALTH_CHECK_PORT))
    assert result == 0, "Health check port 13133 is not accessible"
    sock.close()
