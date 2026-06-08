import os
import time
import signal
import subprocess
import pytest
import requests
from typing import Tuple

NGINX_PORT = 8080
TEST_LARGE_FILE_PATH = "/large-test-file.txt"
DEFAULT_TIMEOUT = 30
CUSTOM_TIMEOUT = 10

@pytest.fixture(scope="function")
def nginx_container(request):
    """Fixture to start and stop the telemetry-docs Nginx container for tests"""
    env_vars = getattr(request, "param", {})
    env_args = []
    for k, v in env_vars.items():
        env_args.extend(["-e", f"{k}={v}"])
    
    # Build the container first
    subprocess.run(
        ["docker", "build", "-t", "test-telemetry-docs", "src/telemetry-docs/"],
        check=True,
        capture_output=True
    )
    
    # Run container in detached mode, map port
    run_cmd = [
        "docker", "run", "-d",
        "-p", f"{NGINX_PORT}:80",
        *env_args,
        "--name", "test-telemetry-docs-nginx",
        "test-telemetry-docs"
    ]
    container_id = subprocess.check_output(run_cmd, text=True).strip()
    
    # Wait for Nginx to become healthy
    for _ in range(10):
        try:
            requests.get(f"http://localhost:{NGINX_PORT}/", timeout=1)
            break
        except requests.exceptions.RequestException:
            time.sleep(0.5)
    else:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
        raise RuntimeError("Nginx container did not become healthy")
    
    yield container_id
    
    # Cleanup
    subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)

def test_ac1_stop_accepting_new_connections_on_signal(nginx_container):
    """AC-1: Nginx stops accepting new connections immediately after receiving shutdown signal"""
    # First verify we can connect before signal
    response = requests.get(f"http://localhost:{NGINX_PORT}/", timeout=2)
    assert response.status_code == 200, "Can connect to Nginx before signal"
    
    # Send SIGTERM to container
    subprocess.run(["docker", "kill", "-s", "SIGTERM", nginx_container], check=True)
    time.sleep(0.5)  # Allow signal processing
    
    # Try to open new connection, expect connection refused
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://localhost:{NGINX_PORT}/", timeout=1)

def test_ac2_active_requests_complete_within_grace_period(nginx_container):
    """AC-2: Active requests complete successfully if they finish within grace period"""
    # First create a large test file in the running container
    subprocess.run([
        "docker", "exec", nginx_container,
        "dd", "if=/dev/zero", f"of=/usr/share/nginx/html{TEST_LARGE_FILE_PATH}",
        "bs=1M", "count=10"
    ], check=True, capture_output=True)
    
    # Start a long-running download in background thread
    download_start = time.time()
    download_process = subprocess.Popen([
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
        f"http://localhost:{NGINX_PORT}{TEST_LARGE_FILE_PATH}"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Wait 2s for download to start, then send SIGTERM
    time.sleep(2)
    subprocess.run(["docker", "kill", "-s", "SIGTERM", nginx_container], check=True)
    
    # Wait for download to complete
    stdout, stderr = download_process.communicate(timeout=15)
    download_duration = time.time() - download_start
    
    # Verify request succeeded
    assert stdout.strip() == "200", f"Expected 200 OK, got {stdout}. Error: {stderr}"
    assert download_duration < DEFAULT_TIMEOUT, "Download completed within grace period"

@pytest.mark.parametrize("nginx_container", [
    {},  # No env var, default timeout 30s
    {"NGINX_GRACEFUL_SHUTDOWN_TIMEOUT": str(CUSTOM_TIMEOUT)}  # Custom timeout
], indirect=True)
def test_ac3_graceful_shutdown_timeout_configurable(nginx_container, request):
    """AC-3: Graceful shutdown timeout is configurable via environment variable"""
    # Get expected timeout from parameter
    expected_timeout = DEFAULT_TIMEOUT if not request.param else CUSTOM_TIMEOUT
    
    # Create a request that will run longer than timeout
    slow_request = subprocess.Popen([
        "curl", "-s", "-m", str(expected_timeout + 5),
        f"http://localhost:{NGINX_PORT}/slow"  # This endpoint will hang since not implemented
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Wait 1s, send SIGTERM
    time.sleep(1)
    signal_time = time.time()
    subprocess.run(["docker", "kill", "-s", "SIGTERM", nginx_container], check=True)
    
    # Wait for container to exit
    subprocess.run(["docker", "wait", nginx_container], check=True, capture_output=True)
    exit_duration = time.time() - signal_time
    
    # Verify exit happened within expected timeout window
    assert abs(exit_duration - expected_timeout) < 2, f"Expected exit after ~{expected_timeout}s, got {exit_duration}s"
    
    # Cleanup the curl process
    slow_request.terminate()
    slow_request.wait()

def test_ac4_shutdown_logs_generated(nginx_container):
    """AC-4: Nginx logs expected shutdown messages at appropriate stages"""
    # Send SIGTERM to trigger shutdown
    subprocess.run(["docker", "kill", "-s", "SIGTERM", nginx_container], check=True)
    
    # Wait for container to exit
    subprocess.run(["docker", "wait", nginx_container], check=True, capture_output=True)
    
    # Get logs
    logs = subprocess.check_output(["docker", "logs", nginx_container], stderr=subprocess.STDOUT, text=True)
    
    # Check for presence of expected logs
    assert "[shutdown] Initiating graceful shutdown" in logs, "Shutdown initiation log missing"
    assert ("[shutdown] All in-flight requests completed, exiting normally" in logs or
            "[shutdown] Grace period expired, forcing termination" in logs), "Shutdown completion log missing"
    
    # Now test timeout case to see all three logs
    # Start new container with custom timeout
    run_cmd = [
        "docker", "run", "-d",
        "-p", f"{NGINX_PORT+1}:80",
        "-e", f"NGINX_GRACEFUL_SHUTDOWN_TIMEOUT={CUSTOM_TIMEOUT}",
        "--name", "test-telemetry-docs-nginx-timeout",
        "test-telemetry-docs"
    ]
    container_id = subprocess.check_output(run_cmd, text=True).strip()
    try:
        # Wait for healthy
        for _ in range(10):
            try:
                requests.get(f"http://localhost:{NGINX_PORT+1}/", timeout=1)
                break
            except requests.exceptions.RequestException:
                time.sleep(0.5)
        
        # Start hanging request
        slow_request = subprocess.Popen([
            "curl", "-s", f"http://localhost:{NGINX_PORT+1}/slow"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1)
        
        # Send SIGTERM
        subprocess.run(["docker", "kill", "-s", "SIGTERM", container_id], check=True)
        subprocess.run(["docker", "wait", container_id], check=True, capture_output=True)
        
        logs = subprocess.check_output(["docker", "logs", container_id], stderr=subprocess.STDOUT, text=True)
        assert "[shutdown] Initiating graceful shutdown" in logs
        assert "[shutdown] Grace period expired, forcing termination of remaining connections" in logs
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
        slow_request.terminate()
        slow_request.wait()

def test_ac5_exit_immediately_when_no_active_requests(nginx_container):
    """AC-5: Nginx exits immediately with exit code 0 when no active requests"""
    # Verify no active requests
    time.sleep(0.5)
    
    # Send SIGTERM
    signal_time = time.time()
    subprocess.run(["docker", "kill", "-s", "SIGTERM", nginx_container], check=True)
    
    # Wait for exit
    exit_code = subprocess.check_output(["docker", "wait", nginx_container], text=True).strip()
    exit_duration = time.time() - signal_time
    
    assert exit_duration < 2, f"Expected immediate exit, took {exit_duration}s"
    assert exit_code == "0", f"Expected exit code 0, got {exit_code}"

def test_ac6_force_terminate_after_timeout(nginx_container):
    """AC-6: Nginx terminates remaining connections after grace period, exits with code 0"""
    # Start a request that will run longer than default timeout
    long_request = subprocess.Popen([
        "curl", "-v", f"http://localhost:{NGINX_PORT}/slow-endpoint"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Wait 2s, send SIGTERM
    time.sleep(2)
    subprocess.run(["docker", "kill", "-s", "SIGTERM", nginx_container], check=True)
    
    # Wait for container to exit
    exit_code = subprocess.check_output(["docker", "wait", nginx_container], timeout=DEFAULT_TIMEOUT + 5, text=True).strip()
    
    # Check curl output for connection reset
    stdout, stderr = long_request.communicate(timeout=2)
    assert "reset by peer" in stderr.lower() or "connection closed" in stderr.lower(), "Connection was not terminated after timeout"
    
    assert exit_code == "0", f"Expected exit code 0 after force termination, got {exit_code}"
