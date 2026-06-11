import os
import time
import pytest
import requests
from subprocess import Popen, PIPE
from typing import Tuple

@pytest.fixture(scope="module")
def load_generator_process() -> Tuple[Popen, int]:
    """Fixture to start load generator process with test config"""
    port = os.getenv("HEALTH_CHECK_PORT", "8090")
    env = os.environ.copy()
    env["HEALTH_CHECK_PORT"] = port
    
    # Start load generator
    proc = Popen(
        ["python3", "locustfile.py", "--headless", "-u", "1", "-r", "1", "-t", "1m"],
        cwd="/workspace/src/load-generator",
        env=env,
        stdout=PIPE,
        stderr=PIPE,
        text=True
    )
    
    # Give time to start
    time.sleep(5)
    
    yield proc, int(port)
    
    # Cleanup
    proc.terminate()
    proc.wait(timeout=10)

def test_ac1_liveness_endpoint_returns_200_when_process_running(load_generator_process):
    """AC-1: When load-generator process is running, GET /health/liveness returns 200 OK with {"status": "ok"}"""
    proc, port = load_generator_process
    assert proc.poll() is None, "Load generator process is not running"
    
    response = requests.get(f"http://localhost:{port}/health/liveness", timeout=5)
    
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {"status": "ok"}

def test_ac2_readiness_returns_503_when_runner_not_initialized():
    """AC-2: When Locust test runner is not initialized, GET /health/readiness returns 503 with {"status": "unavailable"}"""
    # Start with config that delays runner initialization
    port = "8091"
    env = os.environ.copy()
    env["HEALTH_CHECK_PORT"] = port
    env["LOCUST_DELAY_INIT"] = "10"  # Hypothetical env var to delay init for test
    
    proc = Popen(
        ["python3", "locustfile.py", "--headless", "-u", "1", "-r", "1", "-t", "1m"],
        cwd="/workspace/src/load-generator",
        env=env,
        stdout=PIPE,
        stderr=PIPE,
        text=True
    )
    
    try:
        # Check immediately after start before runner is initialized
        time.sleep(2)
        assert proc.poll() is None, "Load generator process is not running"
        
        response = requests.get(f"http://localhost:{port}/health/readiness", timeout=5)
        
        assert response.status_code == 503
        assert response.headers["Content-Type"] == "application/json"
        assert response.json() == {"status": "unavailable"}
    finally:
        proc.terminate()
        proc.wait(timeout=10)

def test_ac3_readiness_returns_200_when_runner_initialized(load_generator_process):
    """AC-3: When Locust test runner is fully initialized, GET /health/readiness returns 200 with {"status": "ok"}"""
    proc, port = load_generator_process
    assert proc.poll() is None, "Load generator process is not running"
    
    # Wait for runner to initialize
    time.sleep(10)
    
    response = requests.get(f"http://localhost:{port}/health/readiness", timeout=5)
    
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {"status": "ok"}

def test_ac4_default_port_8090_when_no_env_var():
    """AC-4: Health check server listens on port 8090 when no HEALTH_CHECK_PORT environment variable is set"""
    # Clear any existing HEALTH_CHECK_PORT env var
    env = os.environ.copy()
    env.pop("HEALTH_CHECK_PORT", None)
    
    proc = Popen(
        ["python3", "locustfile.py", "--headless", "-u", "1", "-r", "1", "-t", "1m"],
        cwd="/workspace/src/load-generator",
        env=env,
        stdout=PIPE,
        stderr=PIPE,
        text=True
    )
    
    try:
        time.sleep(5)
        assert proc.poll() is None, "Load generator process is not running"
        
        # Test port 8090 is accessible
        response = requests.get("http://localhost:8090/health/liveness", timeout=5)
        assert response.status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)

def test_ac5_custom_port_configurable_via_env_var():
    """AC-5: When HEALTH_CHECK_PORT is set to valid integer, health check server listens on specified port"""
    test_port = "9999"
    env = os.environ.copy()
    env["HEALTH_CHECK_PORT"] = test_port
    
    proc = Popen(
        ["python3", "locustfile.py", "--headless", "-u", "1", "-r", "1", "-t", "1m"],
        cwd="/workspace/src/load-generator",
        env=env,
        stdout=PIPE,
        stderr=PIPE,
        text=True
    )
    
    try:
        time.sleep(5)
        assert proc.poll() is None, "Load generator process is not running"
        
        # Test custom port is accessible
        response = requests.get(f"http://localhost:{test_port}/health/liveness", timeout=5)
        assert response.status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)

def test_ac6_locust_default_endpoints_still_work(load_generator_process):
    """AC-6: All existing Locust endpoints on port 8089 continue to work as expected"""
    proc, port = load_generator_process
    assert proc.poll() is None, "Load generator process is not running"
    
    # Give Locust time to start its default interface
    time.sleep(10)
    
    # Test default Locust web UI endpoint
    response = requests.get("http://localhost:8089/", timeout=5)
    assert response.status_code == 200

def test_ac7_health_endpoints_no_authentication_required(load_generator_process):
    """AC-7: Health check endpoints do not require any authentication to access"""
    proc, port = load_generator_process
    assert proc.poll() is None, "Load generator process is not running"
    
    # Test without any auth headers
    liveness_response = requests.get(f"http://localhost:{port}/health/liveness", timeout=5)
    assert liveness_response.status_code == 200
    
    readiness_response = requests.get(f"http://localhost:{port}/health/readiness", timeout=5)
    assert readiness_response.status_code in [200, 503]  # Either is acceptable depending on init state

def test_ac8_health_responses_have_correct_content_type(load_generator_process):
    """AC-8: All health check responses have Content-Type: application/json header set"""
    proc, port = load_generator_process
    assert proc.poll() is None, "Load generator process is not running"
    
    # Test liveness endpoint
    liveness_response = requests.get(f"http://localhost:{port}/health/liveness", timeout=5)
    assert liveness_response.headers["Content-Type"] == "application/json"
    
    # Test readiness endpoint
    readiness_response = requests.get(f"http://localhost:{port}/health/readiness", timeout=5)
    assert readiness_response.headers["Content-Type"] == "application/json"
    
    # Test 404 endpoint
    invalid_response = requests.get(f"http://localhost:{port}/invalid/path", timeout=5)
    assert invalid_response.headers["Content-Type"] == "application/json"
