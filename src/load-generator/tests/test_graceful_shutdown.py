import signal
import subprocess
import time
import pytest
import requests
from typing import List, Dict
import os
import json

@pytest.fixture
def load_generator_process():
    """Fixture to start load generator process in headless mode with low user count"""
    env = os.environ.copy()
    env["LOCUST_HOST"] = "http://localhost:8080"
    env["LOCUST_USERS"] = "2"
    env["LOCUST_SPAWN_RATE"] = "2"
    env["LOCUST_RUN_TIME"] = "1m"
    env["LOCUST_HEADLESS"] = "1"
    
    proc = subprocess.Popen(
        ["locust", "-f", "locustfile.py"],
        cwd="./src/load-generator",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    # Wait for process to start up
    time.sleep(3)
    yield proc
    # Cleanup if process is still running
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac1_sigint_triggers_shutdown_log(load_generator_process):
    """AC-1: SIGINT triggers shutdown log within 100ms"""
    start_time = time.time()
    load_generator_process.send_signal(signal.SIGINT)
    
    log_found = False
    while time.time() - start_time < 0.1:
        line = load_generator_process.stdout.readline()
        if "Starting graceful shutdown (10s timeout)..." in line:
            log_found = True
            break
        time.sleep(0.001)
    
    assert log_found, "Shutdown start log not emitted within 100ms of SIGINT"

def test_ac1_sigterm_triggers_shutdown_log(load_generator_process):
    """AC-1: SIGTERM triggers shutdown log within 100ms"""
    start_time = time.time()
    load_generator_process.send_signal(signal.SIGTERM)
    
    log_found = False
    while time.time() - start_time < 0.1:
        line = load_generator_process.stdout.readline()
        if "Starting graceful shutdown (10s timeout)..." in line:
            log_found = True
            break
        time.sleep(0.001)
    
    assert log_found, "Shutdown start log not emitted within 100ms of SIGTERM"

def test_ac2_no_new_requests_after_signal(load_generator_process, monkeypatch):
    """AC-2: No new HTTP requests spawned after signal received"""
    # Track request timestamps
    request_times: List[float] = []
    original_request = requests.Session.request
    
    def track_request_time(*args, **kwargs):
        request_times.append(time.time())
        # Return dummy response
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b"{}"
        return resp
    
    monkeypatch.setattr(requests.Session, "request", track_request_time)
    
    # Wait some time for requests to be sent
    time.sleep(2)
    requests_before_signal = len(request_times)
    
    # Send SIGTERM
    load_generator_process.send_signal(signal.SIGTERM)
    signal_time = time.time()
    
    # Wait for 2 seconds for any remaining requests
    time.sleep(2)
    
    requests_after_signal = [t for t in request_times if t > signal_time]
    assert len(requests_after_signal) == 0, f"Found {len(requests_after_signal)} new requests after signal received"

def test_ac3_in_flight_requests_complete_before_exit():
    """AC-3: In-flight requests complete before exit when under 10s timeout"""
    # Start process with custom endpoint that has 2s delay
    env = os.environ.copy()
    env["LOCUST_HOST"] = "http://httpbin.org"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_RUN_TIME"] = "1m"
    env["LOCUST_HEADLESS"] = "1"
    
    # Modify locust task to hit /delay/2 endpoint
    proc = subprocess.Popen(
        ["python", "-c", """
import time
from locust import HttpUser, task, between

class TestUser(HttpUser):
    wait_time = between(0.1, 0.5)
    
    @task
    def slow_request(self):
        self.client.get("/delay/2")
"""],
        cwd="./src/load-generator",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Wait for process to start and send the slow request
    time.sleep(3)
    
    # Send SIGTERM right after request is sent
    start_shutdown = time.time()
    proc.send_signal(signal.SIGTERM)
    
    # Wait for process to exit
    exit_code = proc.wait(timeout=11)
    
    # Check that process took at least 2s to exit (allowing slow request to complete)
    shutdown_duration = time.time() - start_shutdown
    assert shutdown_duration >= 2.0, f"Process exited too quickly ({shutdown_duration}s), in-flight request likely canceled"
    assert exit_code == 0, f"Expected exit code 0, got {exit_code}"
    
    # Check logs for successful completion message
    logs = proc.stdout.read()
    assert "GET /delay/2" in logs, "Slow request not found in logs"
    assert "200 OK" in logs, "Slow request did not complete successfully"

def test_ac4_otel_data_flushed_before_exit(monkeypatch):
    """AC-4: All pending OTel traces/metrics flushed before exit when under 10s timeout"""
    flush_called = False
    
    def mock_force_flush(timeout_millis):
        nonlocal flush_called
        flush_called = True
        return True
    
    # Mock OTel force_flush methods
    monkeypatch.setattr("opentelemetry.sdk.trace.TracerProvider.force_flush", mock_force_flush)
    monkeypatch.setattr("opentelemetry.sdk.metrics.MeterProvider.force_flush", mock_force_flush)
    
    # Start load generator
    env = os.environ.copy()
    env["LOCUST_HOST"] = "http://localhost:8080"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_RUN_TIME"] = "1m"
    env["LOCUST_HEADLESS"] = "1"
    
    proc = subprocess.Popen(
        ["locust", "-f", "locustfile.py"],
        cwd="./src/load-generator",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(2)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=11)
    
    assert flush_called, "OTel force_flush was not called during shutdown"

def test_ac5_successful_shutdown_log_and_exit_code(load_generator_process):
    """AC-5: Successful shutdown logs completion message and exits with code 0"""
    load_generator_process.send_signal(signal.SIGTERM)
    exit_code = load_generator_process.wait(timeout=11)
    
    logs = load_generator_process.stdout.read()
    assert "Graceful shutdown completed successfully" in logs, "Shutdown completion log not found"
    assert exit_code == 0, f"Expected exit code 0, got {exit_code}"

def test_ac6_shutdown_timeout_log_and_exit_code(monkeypatch):
    """AC-6: Shutdown timeout logs warning and exits with code 1"""
    # Mock a never-returning request to trigger timeout
    def mock_request(*args, **kwargs):
        time.sleep(15)
        resp = requests.Response()
        resp.status_code = 200
        return resp
    
    monkeypatch.setattr(requests.Session, "request", mock_request)
    
    env = os.environ.copy()
    env["LOCUST_HOST"] = "http://localhost:8080"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_RUN_TIME"] = "1m"
    env["LOCUST_HEADLESS"] = "1"
    
    proc = subprocess.Popen(
        ["locust", "-f", "locustfile.py"],
        cwd="./src/load-generator",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(2)
    start_shutdown = time.time()
    proc.send_signal(signal.SIGTERM)
    
    exit_code = proc.wait(timeout=15)
    shutdown_duration = time.time() - start_shutdown
    
    # Should exit after ~10s timeout
    assert shutdown_duration >= 10.0 and shutdown_duration < 12.0, f"Shutdown took {shutdown_duration}s, expected ~10s timeout"
    
    logs = proc.stdout.read()
    assert "Graceful shutdown timed out after 10s, forcing exit" in logs, "Timeout warning log not found"
    assert exit_code == 1, f"Expected exit code 1, got {exit_code}"
