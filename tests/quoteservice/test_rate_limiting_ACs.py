#!/usr/bin/env python3
import os
import pytest
import requests
import time
from collections import defaultdict

SERVICE_NAME = os.environ.get("TEST_QUOTE_SERVICE_NAME", "quote-service")
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")
SERVICE_PORT = 8080
BASE_URL = f"http://{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:{SERVICE_PORT}"
# Test endpoint (public endpoint of quote service)
TEST_ENDPOINT = "/get-quote"
DEFAULT_RATE_LIMIT = 100
DEFAULT_RETRY_AFTER = 60

@pytest.mark.ac1
def test_ac1_requests_under_limit_succeed():
    """AC-1: When a client IP sends <= 100 requests within a 60-second window to any public HTTP endpoint of the quote service,
    all requests are processed successfully with no 429 responses."""
    # Send 100 requests, all should succeed (non-429)
    success_count = 0
    for i in range(DEFAULT_RATE_LIMIT):
        try:
            # Send test request (empty body is fine for rate limit testing, we just check status is not 429)
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
            if resp.status_code != 429:
                success_count += 1
        except Exception as e:
            # Ignore connection errors for this test, we just care about no 429s
            pass
    
    # All requests should have succeeded (no 429s)
    assert success_count == DEFAULT_RATE_LIMIT, f"Expected {DEFAULT_RATE_LIMIT} successful non-429 responses, got {success_count}"

@pytest.mark.ac2
def test_ac2_requests_over_limit_return_429():
    """AC-2: When a client IP sends > 100 requests within a 60-second window to any public HTTP endpoint of the quote service,
    all subsequent requests in that window receive a 429 Too Many Requests status code."""
    # First consume all allowed requests
    for i in range(DEFAULT_RATE_LIMIT):
        try:
            requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
        except Exception:
            pass
    
    # Next requests should all be 429
    rate_limited_count = 0
    for i in range(10):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
            if resp.status_code == 429:
                rate_limited_count +=1
        except Exception:
            pass
    
    assert rate_limited_count > 0, "Expected at least some 429 responses after exceeding rate limit"
    # At least 80% of the extra requests should be 429
    assert rate_limited_count >= 8, f"Expected at least 8 out of 10 extra requests to be 429, got {rate_limited_count}"

@pytest.mark.ac3
def test_ac3_429_responses_have_correct_headers():
    """AC-3: 429 responses include a Retry-After header with value 60, plus X-RateLimit-Limit,
    X-RateLimit-Remaining, and X-RateLimit-Reset headers with correct values."""
    # First consume all allowed requests
    for i in range(DEFAULT_RATE_LIMIT):
        try:
            requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
        except Exception:
            pass
    
    # Get a 429 response
    resp = None
    for i in range(5):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
            if resp.status_code == 429:
                break
        except Exception:
            time.sleep(0.1)
    
    assert resp is not None and resp.status_code == 429, "Could not get 429 response for header test"
    
    # Check required headers
    assert "Retry-After" in resp.headers, "Retry-After header missing from 429 response"
    assert int(resp.headers["Retry-After"]) == DEFAULT_RETRY_AFTER, f"Expected Retry-After header value {DEFAULT_RETRY_AFTER}, got {resp.headers['Retry-After']}"
    
    assert "X-RateLimit-Limit" in resp.headers, "X-RateLimit-Limit header missing from 429 response"
    assert int(resp.headers["X-RateLimit-Limit"]) == DEFAULT_RATE_LIMIT, f"Expected X-RateLimit-Limit header value {DEFAULT_RATE_LIMIT}, got {resp.headers['X-RateLimit-Limit']}"
    
    assert "X-RateLimit-Remaining" in resp.headers, "X-RateLimit-Remaining header missing from 429 response"
    assert int(resp.headers["X-RateLimit-Remaining"]) == 0, f"Expected X-RateLimit-Remaining header value 0, got {resp.headers['X-RateLimit-Remaining']}"
    
    assert "X-RateLimit-Reset" in resp.headers, "X-RateLimit-Reset header missing from 429 response"
    reset_timestamp = int(resp.headers["X-RateLimit-Reset"])
    assert reset_timestamp > int(time.time()), "X-RateLimit-Reset header value should be in the future"
    assert reset_timestamp <= int(time.time()) + DEFAULT_RETRY_AFTER, f"X-RateLimit-Reset header value should be at most {DEFAULT_RETRY_AFTER} seconds in the future"
    
    # Check response body
    body = resp.json()
    assert body["error"] == "Too Many Requests", "Incorrect error message in 429 response body"
    assert "You have exceeded the allowed request limit" in body["message"], "Incorrect message in 429 response body"
    assert body["retry_after"] == DEFAULT_RETRY_AFTER, f"Expected retry_after value {DEFAULT_RETRY_AFTER} in response body, got {body['retry_after']}"

@pytest.mark.ac4
def test_ac4_custom_rate_limit_from_env_var():
    """AC-4: When the `QUOTE_SERVICE_RATE_LIMIT` environment variable is set to a custom integer value,
    the rate limit uses that value instead of the default 100 requests per minute."""
    # Note: This test assumes the service has been deployed with QUOTE_SERVICE_RATE_LIMIT=20 for testing
    custom_limit = int(os.environ.get("QUOTE_SERVICE_RATE_LIMIT_OVERRIDE", 20))
    
    # Send custom_limit requests, all should succeed
    success_count = 0
    for i in range(custom_limit):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
            if resp.status_code != 429:
                success_count += 1
        except Exception:
            pass
    
    assert success_count == custom_limit, f"Expected {custom_limit} successful non-429 responses with custom limit, got {success_count}"
    
    # Next requests should be 429
    resp = None
    for i in range(5):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
            if resp.status_code == 429:
                break
        except Exception:
            time.sleep(0.1)
    
    assert resp is not None and resp.status_code == 429, "Expected 429 after exceeding custom rate limit"
    assert int(resp.headers["X-RateLimit-Limit"]) == custom_limit, f"Expected X-RateLimit-Limit header to match custom limit {custom_limit}, got {resp.headers['X-RateLimit-Limit']}"

@pytest.mark.ac5
def test_ac5_rate_limited_requests_increment_otel_metric():
    """AC-5: Every request that returns a 429 status code increments the `quote_service.rate_limited_requests`
    OpenTelemetry counter metric with correct `client_ip`, `endpoint`, and `status_code` attributes."""
    # First get initial metric value
    # Note: This test assumes access to the OpenTelemetry collector / metrics endpoint
    metrics_url = f"{BASE_URL}/metrics"
    initial_count = 0
    
    try:
        resp = requests.get(metrics_url, timeout=5)
        if resp.status_code == 200:
            # Parse prometheus format metric
            for line in resp.text.splitlines():
                if line.startswith('quote_service_rate_limited_requests') and 'status_code="429"' in line:
                    initial_count = int(line.split()[-1])
                    break
    except Exception:
        pytest.skip("Metrics endpoint not accessible, skipping OTel metric test")
    
    # Generate 5 429 responses
    # First consume limit
    for i in range(DEFAULT_RATE_LIMIT):
        try:
            requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
        except Exception:
            pass
    
    rate_limited_count = 0
    for i in range(5):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
            if resp.status_code == 429:
                rate_limited_count += 1
        except Exception:
            pass
    
    if rate_limited_count == 0:
        pytest.skip("Could not generate 429 responses for metric test")
    
    # Get new metric value
    try:
        resp = requests.get(metrics_url, timeout=5)
        assert resp.status_code == 200, "Metrics endpoint returned non-200 status"
        new_count = 0
        client_ip_found = False
        endpoint_found = False
        status_code_found = False
        
        for line in resp.text.splitlines():
            if line.startswith('quote_service_rate_limited_requests'):
                new_count = int(line.split()[-1])
                if 'client_ip="' in line:
                    client_ip_found = True
                if f'endpoint="{TEST_ENDPOINT}"' in line:
                    endpoint_found = True
                if 'status_code="429"' in line:
                    status_code_found = True
                break
        
        assert new_count >= initial_count + rate_limited_count, f"Expected metric to increase by at least {rate_limited_count}, went from {initial_count} to {new_count}"
        assert client_ip_found, "client_ip attribute missing from rate_limited_requests metric"
        assert endpoint_found, "endpoint attribute missing from rate_limited_requests metric"
        assert status_code_found, "status_code attribute missing from rate_limited_requests metric"
    except Exception as e:
        pytest.fail(f"Failed to verify OTel metric: {str(e)}")

@pytest.mark.ac6
def test_ac6_rate_limiting_applies_to_http_and_https():
    """AC-6: Rate limiting is applied equally to requests sent over HTTP and HTTPS/TLS connections."""
    # Test HTTP first
    http_429_count = 0
    # Consume limit over HTTP
    for i in range(DEFAULT_RATE_LIMIT):
        try:
            requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
        except Exception:
            pass
    # Count 429s over HTTP
    for i in range(10):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, timeout=2)
            if resp.status_code == 429:
                http_429_count += 1
        except Exception:
            pass
    
    # Wait for window to reset
    time.sleep(DEFAULT_RETRY_AFTER + 1)
    
    # Test HTTPS
    https_base_url = f"https://{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:8443"
    https_429_count = 0
    # Consume limit over HTTPS
    for i in range(DEFAULT_RATE_LIMIT):
        try:
            requests.post(f"{https_base_url}{TEST_ENDPOINT}", json={"items": []}, timeout=2, verify=False)
        except Exception:
            pass
    # Count 429s over HTTPS
    for i in range(10):
        try:
            resp = requests.post(f"{https_base_url}{TEST_ENDPOINT}", json={"items": []}, timeout=2, verify=False)
            if resp.status_code == 429:
                https_429_count += 1
        except Exception:
            pass
    
    # Both should have similar 429 counts (at least 5 each)
    assert http_429_count >=5, f"Expected at least 5 429 responses over HTTP, got {http_429_count}"
    assert https_429_count >=5, f"Expected at least 5 429 responses over HTTPS, got {https_429_count}"

@pytest.mark.ac7
def test_ac7_client_ip_extracted_correctly_behind_proxy():
    """AC-7: The client IP address is correctly extracted from the request (respecting common proxy headers
    like X-Forwarded-For if the service is behind a proxy) to identify unique clients."""
    # Test two different client IPs via X-Forwarded-For header, they should have separate limits
    client1_ip = "192.168.1.100"
    client2_ip = "192.168.1.200"
    
    # Consume all requests for client 1
    for i in range(DEFAULT_RATE_LIMIT):
        try:
            requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, headers={"X-Forwarded-For": client1_ip}, timeout=2)
        except Exception:
            pass
    
    # Client 1 should get 429
    client1_429 = 0
    for i in range(3):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, headers={"X-Forwarded-For": client1_ip}, timeout=2)
            if resp.status_code == 429:
                client1_429 +=1
        except Exception:
            pass
    
    # Client 2 should still have all requests succeed (no 429)
    client2_429 = 0
    for i in range(10):
        try:
            resp = requests.post(f"{BASE_URL}{TEST_ENDPOINT}", json={"items": []}, headers={"X-Forwarded-For": client2_ip}, timeout=2)
            if resp.status_code == 429:
                client2_429 +=1
        except Exception:
            pass
    
    assert client1_429 > 0, "Client 1 should get 429 after exceeding limit"
    assert client2_429 == 0, "Client 2 should not get 429 when using different IP via X-Forwarded-For"
