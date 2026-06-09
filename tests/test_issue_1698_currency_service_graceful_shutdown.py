import os
import time
import subprocess
import threading
import signal
import pytest
import grpc
from pb import demo_pb2
from pb import demo_pb2_grpc

CURRENCY_SERVICE_PORT = 9091
GRACE_PERIOD = 10

@pytest.fixture(scope="function")
def currency_service():
    """Fixture to start and stop the currency service"""
    # Start currency service process
    proc = subprocess.Popen(
        ["/app/currency_service"],
        cwd="/workspace/src/currency",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Wait for service to start
    time.sleep(3)
    
    # Create gRPC channel and stub
    channel = grpc.insecure_channel(f"localhost:{CURRENCY_SERVICE_PORT}")
    stub = demo_pb2_grpc.CurrencyServiceStub(channel)
    
    yield proc, stub
    
    # Cleanup
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except:
        proc.kill()

def test_ac1_sigterm_rejects_new_requests(currency_service):
    """AC-1: SIGTERM stops accepting new requests, returns UNAVAILABLE"""
    proc, stub = currency_service
    
    # Send SIGTERM to service
    proc.send_signal(signal.SIGTERM)
    
    # Wait a short time for shutdown to initiate
    time.sleep(0.5)
    
    # Try to send new request
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.Convert(demo_pb2.CurrencyConversionRequest(
            from_currency="USD",
            to_currency="EUR",
            units=1,
            nanos=0
        ), timeout=2)
    
    assert excinfo.value.code() == grpc.StatusCode.UNAVAILABLE, f"Expected UNAVAILABLE status, got {excinfo.value.code()}"

def test_ac2_sigterm_waits_for_in_flight_requests(currency_service):
    """AC-2: SIGTERM waits up to 10s for active requests to complete"""
    proc, stub = currency_service
    
    # Create a request that takes ~5s to complete (simulate long processing)
    request_completed = False
    request_success = False
    
    def run_long_request():
        nonlocal request_completed, request_success
        try:
            # We will use a dummy request that takes time, adjust if needed
            stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD",
                to_currency="EUR",
                units=1,
                nanos=0
            ), timeout=8)
            request_success = True
        except:
            request_success = False
        finally:
            request_completed = True
    
    # Start request in background
    thread = threading.Thread(target=run_long_request)
    thread.start()
    
    # Wait 1s for request to be in flight
    time.sleep(1)
    
    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)
    
    # Wait up to 7s for request to complete (total <10s grace period)
    thread.join(timeout=7)
    
    assert request_completed == True, "In-flight request should complete within grace period"
    assert request_success == True, "In-flight request should return success"
    
    # Check process exited within grace period
    exit_code = proc.wait(timeout=3)
    assert exit_code == 0, "Service should exit with success after all requests complete"

def test_ac3_shutdown_early_when_all_requests_done(currency_service):
    """AC-3: Service exits immediately after last request completes before grace period ends"""
    proc, stub = currency_service
    
    # Start a short request
    request_completed = False
    def run_short_request():
        nonlocal request_completed
        stub.Convert(demo_pb2.CurrencyConversionRequest(
            from_currency="USD",
            to_currency="EUR",
            units=1,
            nanos=0
        ), timeout=2)
        request_completed = True
    
    thread = threading.Thread(target=run_short_request)
    thread.start()
    
    # Wait 0.5s for request to start
    time.sleep(0.5)
    
    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)
    
    # Measure time to process exit
    start_time = time.time()
    exit_code = proc.wait(timeout=GRACE_PERIOD)
    elapsed = time.time() - start_time
    
    assert exit_code == 0, "Service should exit with success"
    assert elapsed < GRACE_PERIOD - 1, f"Service should exit early before {GRACE_PERIOD}s, took {elapsed}s"
    assert request_completed == True, "Request should have completed"

def test_ac4_terminate_after_grace_period_expires(currency_service):
    """AC-4: Service force terminates requests after 10s grace period"""
    proc, stub = currency_service
    
    # Create a request that would take longer than 10s
    request_failed = False
    def run_long_request():
        nonlocal request_failed
        try:
            stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD",
                to_currency="EUR",
                units=1,
                nanos=0
            ), timeout=15)
        except grpc.RpcError:
            request_failed = True
    
    thread = threading.Thread(target=run_long_request)
    thread.start()
    
    # Wait 1s for request to be in flight
    time.sleep(1)
    
    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)
    
    # Wait 11s (longer than grace period)
    time.sleep(11)
    
    assert request_failed == True, "Long request should be terminated after grace period"
    
    # Check process has exited
    assert proc.poll() is not None, "Service should have exited after grace period"

def test_ac5_shutdown_initiated_log(currency_service):
    """AC-5: Service logs 'Shutdown initiated, waiting up to 10s for in-flight requests to complete' on signal"""
    proc, stub = currency_service
    
    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)
    
    # Wait for log to appear
    time.sleep(1)
    
    # Read stderr/stdout for log message
    proc.terminate()
    stdout, stderr = proc.communicate(timeout=5)
    
    log_content = stdout + stderr
    assert "Shutdown initiated, waiting up to 10s for in-flight requests to complete" in log_content, "Shutdown initiated log message missing"

def test_ac6_shutdown_completed_log(currency_service):
    """AC-6: Service logs 'Shutdown completed, exiting' when shutdown finishes"""
    proc, stub = currency_service
    
    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)
    
    # Wait for process to exit
    exit_code = proc.wait(timeout=GRACE_PERIOD + 2)
    assert exit_code == 0
    
    stdout, stderr = proc.communicate()
    log_content = stdout + stderr
    
    assert "Shutdown completed, exiting" in log_content, "Shutdown completed log message missing"

def test_ac7_sigint_behaves_same_as_sigterm(currency_service):
    """AC-7: SIGINT behaves identically to SIGTERM for all shutdown behaviors"""
    proc, stub = currency_service
    
    # Test same as AC-2 but with SIGINT
    request_completed = False
    request_success = False
    
    def run_request():
        nonlocal request_completed, request_success
        try:
            stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD",
                to_currency="EUR",
                units=1,
                nanos=0
            ), timeout=5)
            request_success = True
        except:
            request_success = False
        finally:
            request_completed = True
    
    thread = threading.Thread(target=run_request)
    thread.start()
    
    time.sleep(0.5)
    
    # Send SIGINT instead of SIGTERM
    proc.send_signal(signal.SIGINT)
    
    thread.join(timeout=7)
    assert request_completed == True, "Request should complete after SIGINT"
    assert request_success == True, "Request should succeed after SIGINT"
    
    # Check logs for both messages
    stdout, stderr = proc.communicate(timeout=5)
    log_content = stdout + stderr
    
    assert "Shutdown initiated, waiting up to 10s for in-flight requests to complete" in log_content
    assert "Shutdown completed, exiting" in log_content
    
    # Also test new requests are rejected after SIGINT
    time.sleep(0.5)
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.Convert(demo_pb2.CurrencyConversionRequest(
            from_currency="USD",
            to_currency="EUR",
            units=1,
            nanos=0
        ), timeout=2)
    
    assert excinfo.value.code() == grpc.StatusCode.UNAVAILABLE

def test_ac8_grace_period_requests_return_ok(currency_service):
    """AC-8: Requests completing during grace period return OK status"""
    proc, stub = currency_service
    
    # Send multiple requests, some before shutdown, some right after
    responses = []
    
    def send_request():
        try:
            resp = stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD",
                to_currency="EUR",
                units=10,
                nanos=0
            ), timeout=3)
            responses.append((True, resp))
        except grpc.RpcError as e:
            responses.append((False, e.code()))
    
    # Start 3 requests before shutdown
    threads = []
    for _ in range(3):
        t = threading.Thread(target=send_request)
        t.start()
        threads.append(t)
        time.sleep(0.2)
    
    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)
    
    # Wait for all threads to complete
    for t in threads:
        t.join(timeout=5)
    
    # All 3 requests should have succeeded
    assert len(responses) == 3
    for success, resp in responses:
        assert success == True, "All requests started before shutdown should succeed"
        assert resp is not None, "Response should be returned"
