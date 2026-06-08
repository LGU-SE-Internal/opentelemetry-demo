#!/usr/bin/env python3
import os
import sys
import time
import signal
import subprocess
import grpc
import concurrent.futures
from typing import List
# Import protobuf definitions for currency service
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'pb'))
import demo_pb2
import demo_pb2_grpc

CURRENCY_SERVICE_ADDR = "localhost:7000"
SERVICE_BINARY = os.path.join(os.path.dirname(__file__), '..', 'src', 'currency', 'server')
TEST_CONCURRENCY = 15
REQUEST_DURATION_SHORT = 1  # seconds, completes within 10s timeout
REQUEST_DURATION_LONG = 12  # seconds, exceeds 10s timeout

def start_currency_service() -> subprocess.Popen:
    """Start the currency service subprocess"""
    env = os.environ.copy()
    env["PORT"] = CURRENCY_SERVICE_ADDR.split(":")[1]
    proc = subprocess.Popen(
        [SERVICE_BINARY],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for service to start
    time.sleep(3)
    return proc

def make_currency_request(delay: int = 0) -> bool:
    """Make a currency conversion request, optionally with simulated processing delay"""
    try:
        with grpc.insecure_channel(CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            # Add delay if requested (simulate long running request)
            if delay > 0:
                time.sleep(delay)
            response = stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD",
                to_currency="EUR",
                units=100,
                nanos=0
            ))
            return response is not None and response.units > 0
    except Exception as e:
        return False

def test_ac1_sigint_triggers_graceful_shutdown_logging_and_delay():
    """AC-1: When SIGINT is sent to running currency service, it logs shutdown start immediately, stops accepting new connections, and does not exit for at least 10s if active requests exist"""
    proc = start_currency_service()
    # Start a long running request to keep service active
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(make_currency_request, REQUEST_DURATION_LONG)
        time.sleep(0.5)  # Wait for request to start
        start_time = time.time()
        # Send SIGINT
        proc.send_signal(signal.SIGINT)
        # Check log for shutdown start immediately
        time.sleep(0.2)
        stdout_lines = []
        while proc.poll() is None and len(stdout_lines) < 10:
            line = proc.stdout.readline()
            if line:
                stdout_lines.append(line.strip())
        shutdown_start_log = any("INFO: Graceful shutdown initiated, waiting up to 10s for in-flight requests to complete" in l for l in stdout_lines)
        assert shutdown_start_log == True, "Shutdown start log missing"
        # Verify service does not exit before 10s
        exit_code = None
        try:
            exit_code = proc.wait(timeout=9)
        except subprocess.TimeoutExpired:
            # Expected, should not exit before 10s
            pass
        assert exit_code is None, "Service exited before 10s grace period with active requests"
        # Verify new connections are rejected
        new_conn_success = make_currency_request()
        assert new_conn_success == False, "New connections accepted after shutdown signal"
        # Wait for exit and verify logs
        exit_code = proc.wait(timeout=3)
        shutdown_duration = time.time() - start_time
        assert shutdown_duration >= 10, f"Shutdown took only {shutdown_duration}s, expected at least 10s with active requests"
        assert exit_code == 0

def test_ac2_sigterm_in_flight_requests_complete_successfully():
    """AC-2: When SIGTERM is sent, all in-flight requests started before signal complete successfully within 10s grace period"""
    proc = start_currency_service()
    successful_requests = []
    # Start multiple short requests that will complete within timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_currency_request, REQUEST_DURATION_SHORT) for _ in range(10)]
        time.sleep(0.5)  # Wait for all requests to start processing
        # Send SIGTERM
        proc.send_signal(signal.SIGTERM)
        # Collect results
        for future in concurrent.futures.as_completed(futures):
            try:
                successful_requests.append(future.result(timeout=5))
            except Exception:
                successful_requests.append(False)
    # All requests started before signal should succeed
    assert all(successful_requests) == True, "Some in-flight requests failed after SIGTERM"
    # Verify process exited cleanly
    exit_code = proc.wait(timeout=5)
    assert exit_code == 0

def test_ac3_early_exit_when_all_requests_complete():
    """AC-3: If all in-flight requests complete before 10s grace period, service exits immediately and logs shutdown complete"""
    proc = start_currency_service()
    # Start a short request that completes quickly
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(make_currency_request, REQUEST_DURATION_SHORT)
        time.sleep(0.5)
        start_time = time.time()
        proc.send_signal(signal.SIGTERM)
        # Wait for request to complete
        future.result(timeout=2)
        # Wait for process exit
        exit_code = proc.wait(timeout=3)
        shutdown_duration = time.time() - start_time
    # Service should exit much faster than 10s
    assert shutdown_duration < 5, f"Service took {shutdown_duration}s to exit, should exit early after all requests complete"
    assert exit_code == 0
    # Check logs for shutdown complete
    stdout_lines = proc.stdout.readlines() + proc.stderr.readlines()
    shutdown_complete_log = any("INFO: Graceful shutdown completed, all in-flight requests processed" in l for l in stdout_lines)
    assert shutdown_complete_log == True, "Shutdown complete log missing"

def test_ac4_shutdown_timeout_logged_after_10s():
    """AC-4: If in-flight requests remain after 10s, service terminates immediately and logs timeout warning"""
    proc = start_currency_service()
    # Start long requests that exceed 10s timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(make_currency_request, REQUEST_DURATION_LONG) for _ in range(2)]
        time.sleep(0.5)
        start_time = time.time()
        proc.send_signal(signal.SIGTERM)
        # Wait for process to exit
        exit_code = proc.wait(timeout=12)
        shutdown_duration = time.time() - start_time
    # Verify timeout occurred around 10s
    assert abs(shutdown_duration - 10) < 2, f"Shutdown took {shutdown_duration}s, expected ~10s timeout"
    assert exit_code == 0
    # Check logs for timeout warning
    stdout_lines = proc.stdout.readlines() + proc.stderr.readlines()
    timeout_log = any("WARNING: Graceful shutdown timed out after 10s, terminating with pending requests" in l for l in stdout_lines)
    assert timeout_log == True, "Shutdown timeout log missing"
    # Verify requests failed as expected
    for future in futures:
        try:
            result = future.result(timeout=1)
            assert result == False, "Long running request succeeded after timeout"
        except Exception:
            pass

def test_ac5_new_connections_rejected_after_shutdown():
    """AC-5: All new gRPC connection attempts after shutdown signal are rejected immediately"""
    proc = start_currency_service()
    # Send shutdown signal
    proc.send_signal(signal.SIGTERM)
    time.sleep(0.2)  # Wait for shutdown to start
    # Try multiple new connection attempts
    connection_results = [make_currency_request() for _ in range(5)]
    assert all(res == False for res in connection_results) == True, "New connection accepted after shutdown signal"
    # Cleanup
    proc.wait(timeout=10)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
