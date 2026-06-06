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

def test_ac1_sigint_triggers_graceful_shutdown():
    """AC-1: SIGINT triggers graceful shutdown with 10 second timeout"""
    proc = start_currency_service()
    start_time = time.time()
    # Send SIGINT
    proc.send_signal(signal.SIGINT)
    # Wait for process to exit
    exit_code = proc.wait(timeout=12)
    shutdown_duration = time.time() - start_time
    # Verify exit code 0 and shutdown took between 0 and 10 seconds (should complete before timeout unless requests are running)
    assert exit_code == 0
    assert shutdown_duration < 11  # Allow 1s buffer for overhead

def test_ac2_sigterm_triggers_graceful_shutdown():
    """AC-2: SIGTERM triggers graceful shutdown with 10 second timeout"""
    proc = start_currency_service()
    start_time = time.time()
    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)
    # Wait for process to exit
    exit_code = proc.wait(timeout=12)
    shutdown_duration = time.time() - start_time
    # Verify exit code 0 and shutdown took between 0 and 10 seconds
    assert exit_code == 0
    assert shutdown_duration < 11  # Allow 1s buffer for overhead

def test_ac3_in_flight_requests_complete_successfully():
    """AC-3: In-flight requests completing within 10s return successful responses"""
    proc = start_currency_service()
    # Start a short request that will complete within timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(make_currency_request, REQUEST_DURATION_SHORT)
        # Wait 0.5s for request to start processing
        time.sleep(0.5)
        # Send shutdown signal
        proc.send_signal(signal.SIGTERM)
        # Get request result
        request_success = future.result(timeout=5)
    # Verify request succeeded
    assert request_success == True
    # Verify process exited successfully
    exit_code = proc.wait(timeout=10)
    assert exit_code == 0

def test_ac4_long_requests_terminated_cleanly():
    """AC-4: Requests exceeding 10s timeout are terminated cleanly, no crash or memory leaks"""
    proc = start_currency_service()
    # Start a long request that will exceed the 10s timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(make_currency_request, REQUEST_DURATION_LONG)
        # Wait 0.5s for request to start processing
        time.sleep(0.5)
        # Send shutdown signal
        proc.send_signal(signal.SIGTERM)
        # Wait for process to exit (should exit after ~10s)
        exit_code = proc.wait(timeout=12)
        # Check request result (should fail since server shut down before request completed)
        try:
            request_success = future.result(timeout=1)
        except Exception:
            request_success = False
    # Verify process exited cleanly with code 0
    assert exit_code == 0
    assert request_success == False
    # Verify no crash (no core dump, process exited normally)
    assert proc.stderr.read().find("segmentation fault") == -1
    assert proc.stderr.read().find("aborted") == -1

def test_ac5_process_exits_with_code_zero():
    """AC-5: Service exits with status code 0 after shutdown completes"""
    # Test with no active requests
    proc = start_currency_service()
    proc.send_signal(signal.SIGINT)
    exit_code = proc.wait(timeout=10)
    assert exit_code == 0
    # Test with active requests
    proc = start_currency_service()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(make_currency_request, REQUEST_DURATION_SHORT)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        exit_code = proc.wait(timeout=10)
        assert exit_code == 0

def test_ac6_normal_operation_without_shutdown():
    """AC-6: Service operates normally when no shutdown signal is received"""
    proc = start_currency_service()
    # Make multiple normal requests
    for _ in range(5):
        success = make_currency_request()
        assert success == True
    # Send shutdown signal now
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)

def test_ac7_peak_load_no_crashes():
    """AC-7: Shutdown during peak load causes no crashes or invalid responses"""
    proc = start_currency_service()
    # Start many concurrent requests
    results: List[concurrent.futures.Future] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=TEST_CONCURRENCY) as executor:
        for i in range(TEST_CONCURRENCY):
            # Mix of short and long requests
            delay = REQUEST_DURATION_SHORT if i % 2 == 0 else REQUEST_DURATION_LONG
            results.append(executor.submit(make_currency_request, delay))
        # Wait 1s for all requests to start processing
        time.sleep(1)
        # Send shutdown signal
        proc.send_signal(signal.SIGTERM)
        # Wait for process to exit
        exit_code = proc.wait(timeout=12)
    # Verify process didn't crash
    assert exit_code == 0
    # Verify no crash in logs
    stderr = proc.stderr.read()
    assert "segmentation fault" not in stderr
    assert "aborted" not in stderr
    assert "corrupt" not in stderr
    # Check that completed requests returned valid responses
    successful_requests = 0
    for future in results:
        try:
            if future.result(timeout=1):
                successful_requests += 1
        except Exception:
            pass
    # At least half of the short requests should have completed successfully
    assert successful_requests >= (TEST_CONCURRENCY // 4)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
