#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import grpc
import json
from typing import Optional, Tuple
from collections import defaultdict

# Import protobuf definitions for currency service
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'pb'))
import demo_pb2
import demo_pb2_grpc

DEFAULT_CURRENCY_SERVICE_ADDR = "localhost:7000"
SERVICE_BINARY = os.path.join(os.path.dirname(__file__), '..', 'src', 'currency', 'server')
RATE_LIMIT_EXCEEDED_STATUS_CODE = grpc.StatusCode.RESOURCE_EXHAUSTED
EXPECTED_ERROR_MESSAGE = "Rate limit exceeded. Try again later."

def start_currency_service(env_overrides: Optional[dict] = None, wait_for_init: bool = True) -> Tuple[subprocess.Popen, str]:
    """Start the currency service subprocess with optional environment overrides"""
    env = os.environ.copy()
    env["PORT"] = DEFAULT_CURRENCY_SERVICE_ADDR.split(":")[1]
    if env_overrides:
        env.update(env_overrides)
    
    # Capture stderr for log verification
    proc = subprocess.Popen(
        [SERVICE_BINARY],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for service to start
    time.sleep(3 if wait_for_init else 1)
    return proc, DEFAULT_CURRENCY_SERVICE_ADDR

def run_convert_request(channel: grpc.Channel) -> Tuple[bool, Optional[grpc.RpcError]]:
    """Run a single Convert gRPC request, return (success, error)"""
    stub = demo_pb2_grpc.CurrencyServiceStub(channel)
    try:
        response = stub.Convert(demo_pb2.CurrencyConversionRequest(
            from_currency="USD",
            to_currency="EUR",
            units=100,
            nanos=0
        ))
        return True, None
    except grpc.RpcError as e:
        return False, e

def run_get_supported_currencies_request(channel: grpc.Channel) -> Tuple[bool, Optional[grpc.RpcError]]:
    """Run a single GetSupportedCurrencies gRPC request, return (success, error)"""
    stub = demo_pb2_grpc.CurrencyServiceStub(channel)
    try:
        response = stub.GetSupportedCurrencies(demo_pb2.GetSupportedCurrenciesRequest())
        return True, None
    except grpc.RpcError as e:
        return False, e

def test_ac1_requests_within_limit_succeed():
    """AC-1: When a single client IP sends ≤ CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE requests (default 100) within a 1-minute window to any currencyservice gRPC endpoint, all requests are processed successfully with no rate limit errors."""
    proc, addr = start_currency_service()
    try:
        with grpc.insecure_channel(addr) as channel:
            # Send 100 requests (default limit)
            success_count = 0
            for _ in range(100):
                success, err = run_convert_request(channel)
                if success:
                    success_count +=1
                else:
                    assert err.code() != RATE_LIMIT_EXCEEDED_STATUS_CODE, f"Unexpected rate limit error within limit: {err}"
            
            assert success_count == 100, f"Expected 100 successful requests, got {success_count}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac2_requests_exceed_limit_return_resource_exhausted():
    """AC-2: When a single client IP sends > CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE requests within a 1-minute window to any currencyservice gRPC endpoint, all excess requests return gRPC RESOURCE_EXHAUSTED status code with message 'Rate limit exceeded. Try again later.'"""
    proc, addr = start_currency_service(env_overrides={"CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE": "10"})
    try:
        with grpc.insecure_channel(addr) as channel:
            # Send 11 requests
            success_count = 0
            rate_limit_errors = 0
            for _ in range(11):
                success, err = run_convert_request(channel)
                if success:
                    success_count +=1
                else:
                    if err.code() == RATE_LIMIT_EXCEEDED_STATUS_CODE:
                        rate_limit_errors +=1
                        assert err.details() == EXPECTED_ERROR_MESSAGE, f"Wrong error message: {err.details()}"
            
            assert success_count == 10, f"Expected 10 successful requests, got {success_count}"
            assert rate_limit_errors == 1, f"Expected 1 rate limit error, got {rate_limit_errors}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac3_custom_rate_limit_value_applied():
    """AC-3: When CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE is set to a custom positive integer value (e.g. 200), the rate limit is adjusted to this custom value instead of the default 100."""
    custom_limit = 200
    proc, addr = start_currency_service(env_overrides={"CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE": str(custom_limit)})
    try:
        with grpc.insecure_channel(addr) as channel:
            # Send 200 requests, should all succeed
            success_count = 0
            for _ in range(custom_limit):
                success, err = run_convert_request(channel)
                if success:
                    success_count +=1
                else:
                    assert err.code() != RATE_LIMIT_EXCEEDED_STATUS_CODE, f"Unexpected rate limit error within custom limit: {err}"
            
            assert success_count == custom_limit, f"Expected {custom_limit} successful requests, got {success_count}"
            
            # Send one more, should fail
            success, err = run_convert_request(channel)
            assert not success, "Expected excess request to fail"
            assert err.code() == RATE_LIMIT_EXCEEDED_STATUS_CODE, "Expected RESOURCE_EXHAUSTED status code for excess request"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac4_rate_limits_per_client_ip():
    """AC-4: Rate limits are enforced per-client IP: two different client IP addresses can each send up to the configured limit per minute without impacting each other's allowed request count."""
    # For integration test purposes, simulate two different client IPs by running service in a way that allows X-Forwarded-For testing
    # Or use two separate connections with different forwarded IPs (depending on implementation)
    limit = 10
    proc, addr = start_currency_service(env_overrides={"CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE": str(limit)})
    try:
        # First client sends all 10 requests, should succeed
        with grpc.insecure_channel(addr) as channel1:
            # Simulate client IP 1 (implementation specific header)
            metadata = (('x-forwarded-for', '192.168.1.1'),)
            success_count1 = 0
            for _ in range(limit):
                try:
                    stub = demo_pb2_grpc.CurrencyServiceStub(channel1)
                    stub.Convert(demo_pb2.CurrencyConversionRequest(
                        from_currency="USD",
                        to_currency="EUR",
                        units=100,
                        nanos=0
                    ), metadata=metadata)
                    success_count1 +=1
                except grpc.RpcError as e:
                    assert e.code() != RATE_LIMIT_EXCEEDED_STATUS_CODE, f"Client 1 unexpected rate limit: {e}"
            
            assert success_count1 == limit, f"Client 1 expected {limit} successes, got {success_count1}"
            
            # Client 1 sends 11th request, should fail
            try:
                stub.Convert(demo_pb2.CurrencyConversionRequest(
                    from_currency="USD",
                    to_currency="EUR",
                    units=100,
                    nanos=0
                ), metadata=metadata)
                assert False, "Client 1 11th request should have failed"
            except grpc.RpcError as e:
                assert e.code() == RATE_LIMIT_EXCEEDED_STATUS_CODE, "Client 1 expected RESOURCE_EXHAUSTED"
        
        # Second client sends 10 requests, should all succeed
        with grpc.insecure_channel(addr) as channel2:
            metadata = (('x-forwarded-for', '192.168.1.2'),)
            success_count2 = 0
            for _ in range(limit):
                try:
                    stub = demo_pb2_grpc.CurrencyServiceStub(channel2)
                    stub.Convert(demo_pb2.CurrencyConversionRequest(
                        from_currency="USD",
                        to_currency="EUR",
                        units=100,
                        nanos=0
                    ), metadata=metadata)
                    success_count2 +=1
                except grpc.RpcError as e:
                    assert e.code() != RATE_LIMIT_EXCEEDED_STATUS_CODE, f"Client 2 unexpected rate limit: {e}"
            
            assert success_count2 == limit, f"Client 2 expected {limit} successes, got {success_count2}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac5_rate_limit_violation_logged():
    """AC-5: When a rate limit violation occurs, a structured log entry is written containing all required fields: client_ip, endpoint, trace_id, event type, and applied limit."""
    limit = 10
    proc, addr = start_currency_service(env_overrides={"CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE": str(limit)})
    try:
        with grpc.insecure_channel(addr) as channel:
            # Send 11 requests to trigger violation
            for i in range(11):
                run_convert_request(channel)
        
        # Stop process to get all stderr output
        proc.terminate()
        stderr_output = proc.stderr.read()
        
        # Find rate limit violation log entry
        violation_found = False
        for line in stderr_output.splitlines():
            try:
                log_entry = json.loads(line)
                if log_entry.get("event") == "rate_limit_violation":
                    violation_found = True
                    # Verify all required fields exist
                    assert "client_ip" in log_entry, "Missing client_ip in violation log"
                    assert "endpoint" in log_entry, "Missing endpoint in violation log"
                    assert "trace_id" in log_entry, "Missing trace_id in violation log"
                    assert "limit_applied" in log_entry, "Missing limit_applied in violation log"
                    assert "timestamp" in log_entry, "Missing timestamp in violation log"
                    assert log_entry["endpoint"] == "/oteldemo.CurrencyService/Convert", f"Wrong endpoint: {log_entry['endpoint']}"
                    assert log_entry["limit_applied"] == limit, f"Wrong limit applied: {log_entry['limit_applied']}"
                    break
            except json.JSONDecodeError:
                continue
        
        assert violation_found, "No rate limit violation log entry found"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

def test_ac6_rate_limit_applied_to_both_endpoints():
    """AC-6: Rate limiting policy is applied uniformly to both gRPC endpoints: Convert and GetSupportedCurrencies."""
    limit = 10
    proc, addr = start_currency_service(env_overrides={"CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE": str(limit)})
    try:
        with grpc.insecure_channel(addr) as channel:
            # Mix requests between both endpoints, total 11, should have 1 rate limit error
            requests = [
                run_convert_request,
                run_get_supported_currencies_request
            ]
            success_count = 0
            rate_limit_errors = 0
            for i in range(11):
                req_func = requests[i % 2]
                success, err = req_func(channel)
                if success:
                    success_count +=1
                else:
                    if err.code() == RATE_LIMIT_EXCEEDED_STATUS_CODE:
                        rate_limit_errors +=1
            
            assert success_count == 10, f"Expected 10 successful requests across both endpoints, got {success_count}"
            assert rate_limit_errors == 1, f"Expected 1 rate limit error across both endpoints, got {rate_limit_errors}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac7_invalid_rate_limit_falls_back_to_default():
    """AC-7: If CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE is set to an invalid value (non-integer, zero, or negative number), the service falls back to the default 100 requests per minute limit."""
    test_cases = [
        "not-a-number",
        "0",
        "-50",
        "10.5",
        ""
    ]
    default_limit = 100
    
    for invalid_value in test_cases:
        proc, addr = start_currency_service(env_overrides={"CURRENCY_SERVICE_RATE_LIMIT_PER_MINUTE": invalid_value})
        try:
            with grpc.insecure_channel(addr) as channel:
                # Send 100 requests, should all succeed (default limit)
                success_count = 0
                for _ in range(default_limit):
                    success, err = run_convert_request(channel)
                    if success:
                        success_count +=1
                    else:
                        assert err.code() != RATE_LIMIT_EXCEEDED_STATUS_CODE, f"Unexpected rate limit error with invalid value '{invalid_value}': {err}"
                
                assert success_count == default_limit, f"Expected {default_limit} successes with invalid value '{invalid_value}', got {success_count}"
                
                # Send one more, should fail
                success, err = run_convert_request(channel)
                assert not success, f"Expected excess request to fail with invalid value '{invalid_value}'"
                assert err.code() == RATE_LIMIT_EXCEEDED_STATUS_CODE, f"Expected RESOURCE_EXHAUSTED with invalid value '{invalid_value}'"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
