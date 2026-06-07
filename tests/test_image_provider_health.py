#!/usr/bin/env python3
import subprocess
import time
import pytest
import requests

IMAGE_PROVIDER_SERVICE_NAME = "imageprovider"
IMAGE_PROVIDER_PORT = 8080
BASE_URL = f"http://localhost:{IMAGE_PROVIDER_PORT}"
DOCKER_COMPOSE_CMD = ["docker", "compose"]


def run_cmd(cmd, shell=False, check=True):
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=check)


def wait_for_service_start(timeout=10):
    """Wait for image provider service to be reachable"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Just check if port is open
            requests.get(f"{BASE_URL}/status", timeout=1)
            return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            time.sleep(0.5)
    return False


@pytest.fixture(scope="function")
def image_provider_service():
    """Fixture to start/stop image provider service for each test"""
    # Cleanup any existing instance
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", IMAGE_PROVIDER_SERVICE_NAME], check=False)
    # Start service detached
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", IMAGE_PROVIDER_SERVICE_NAME])
    # Wait for service to start
    assert wait_for_service_start(timeout=15), "Image provider service failed to start within 15s"
    
    yield
    
    # Cleanup after test
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", IMAGE_PROVIDER_SERVICE_NAME], check=False)


@pytest.mark.ac1
def test_ac1_health_endpoint_returns_200_ok_when_nginx_running(image_provider_service):
    """AC-1: When Nginx process is running, unauthenticated GET /health returns 200 OK with body 'OK'"""
    response = requests.get(f"{BASE_URL}/health", timeout=2)
    assert response.status_code == 200
    assert response.text.strip() == "OK"
    assert response.headers["Content-Type"] == "text/plain"


@pytest.mark.ac2
def test_ac2_health_endpoint_never_returns_auth_errors(image_provider_service):
    """AC-2: GET /health never returns 401 Unauthorized or 403 Forbidden status codes"""
    # Test without any auth
    response = requests.get(f"{BASE_URL}/health", timeout=2)
    assert response.status_code not in [401, 403]
    
    # Test with invalid auth to ensure it still doesn't return auth errors
    response = requests.get(
        f"{BASE_URL}/health",
        headers={"Authorization": "Bearer invalid_token"},
        timeout=2
    )
    assert response.status_code not in [401, 403]


@pytest.mark.ac3
def test_ac3_ready_endpoint_returns_200_ready_when_static_dir_accessible(image_provider_service):
    """AC-3: When /static directory exists and is readable, GET /ready returns 200 OK with body 'READY'"""
    response = requests.get(f"{BASE_URL}/ready", timeout=2)
    assert response.status_code == 200
    assert response.text.strip() == "READY"
    assert response.headers["Content-Type"] == "text/plain"


@pytest.mark.ac4
def test_ac4_ready_endpoint_returns_503_when_static_dir_not_accessible(image_provider_service):
    """AC-4: When /static directory is not readable/missing, GET /ready returns 503 with body 'NOT_READY'"""
    # Make /static directory unreadable
    run_cmd([
        "docker", "exec", IMAGE_PROVIDER_SERVICE_NAME,
        "chmod", "000", "/static"
    ])
    time.sleep(1)  # Give Nginx time to pick up change?
    try:
        response = requests.get(f"{BASE_URL}/ready", timeout=2)
        assert response.status_code == 503
        assert response.text.strip() == "NOT_READY"
        assert response.headers["Content-Type"] == "text/plain"
    finally:
        # Revert permission change
        run_cmd([
            "docker", "exec", IMAGE_PROVIDER_SERVICE_NAME,
            "chmod", "755", "/static"
        ], check=False)


@pytest.mark.ac5
def test_ac5_ready_endpoint_never_returns_auth_errors(image_provider_service):
    """AC-5: GET /ready never returns 401 Unauthorized or 403 Forbidden status codes"""
    # Test without any auth
    response = requests.get(f"{BASE_URL}/ready", timeout=2)
    assert response.status_code not in [401, 403]
    
    # Test with invalid auth to ensure it still doesn't return auth errors
    response = requests.get(
        f"{BASE_URL}/ready",
        headers={"Authorization": "Bearer invalid_token"},
        timeout=2
    )
    assert response.status_code not in [401, 403]


@pytest.mark.ac6
def test_ac6_status_endpoint_remains_functional(image_provider_service):
    """AC-6: Existing /status stub metrics endpoint remains fully functional"""
    response = requests.get(f"{BASE_URL}/status", timeout=2)
    assert response.status_code == 200
    assert "Active connections:" in response.text
    assert "server accepts handled requests" in response.text
