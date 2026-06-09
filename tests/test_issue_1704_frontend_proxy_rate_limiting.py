import os
import time
import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "http://localhost:8080"  # Default frontend-proxy endpoint


def get_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def test_ac1_default_rate_limit_values_applied():
    """AC-1: Verify default rate limit values (100 RPS, 200 burst) are applied when no env vars are set"""
    session = get_session()
    
    # First check health endpoint is accessible
    health_resp = session.get(f"{BASE_URL}/health", timeout=5)
    assert health_resp.status_code == 200, "Health endpoint not accessible"
    
    # Send 100 requests within 1s, all should succeed
    success_count = 0
    for i in range(100):
        resp = session.get(f"{BASE_URL}/", timeout=2)
        if resp.status_code < 400:
            success_count += 1
    assert success_count == 100, f"Expected 100 successful requests, got {success_count}"
    
    # Next request should be rate limited (101st request over 1s window)
    resp = session.get(f"{BASE_URL}/", timeout=2)
    assert resp.status_code == 429, f"Expected 429 for 101st request, got {resp.status_code}"


def test_ac2_custom_rate_limit_values_applied():
    """AC-2: Verify custom rate limit values from environment variables are used"""
    # Check if custom env vars are set (test env should set these for this test case)
    rps = int(os.environ.get("FRONTEND_PROXY_RATE_LIMIT_RPS", "50"))
    burst = int(os.environ.get("FRONTEND_PROXY_RATE_LIMIT_BURST", "100"))
    
    session = get_session()
    
    # Send up to RPS requests, all should succeed
    success_count = 0
    for i in range(rps):
        resp = session.get(f"{BASE_URL}/", timeout=2)
        if resp.status_code < 400:
            success_count += 1
    assert success_count == rps, f"Expected {rps} successful requests with custom RPS, got {success_count}"
    
    # Next request should be rate limited
    resp = session.get(f"{BASE_URL}/", timeout=2)
    assert resp.status_code == 429, f"Expected 429 for {rps+1}th request with custom RPS, got {resp.status_code}"


def test_ac3_rate_limit_exceeded_returns_429():
    """AC-3: Verify 101st request within 1 second returns 429 with default config"""
    session = get_session()
    
    # Send 100 requests quickly
    for i in range(100):
        session.get(f"{BASE_URL}/", timeout=2)
    
    # 101st request must return 429
    resp = session.get(f"{BASE_URL}/", timeout=2)
    assert resp.status_code == 429, f"Expected 429 status for rate limited request, got {resp.status_code}"
    assert resp.reason == "Too Many Requests", f"Expected 'Too Many Requests' reason phrase, got {resp.reason}"


def test_ac4_health_endpoints_not_rate_limited():
    """AC-4: Verify health endpoints are excluded from rate limits"""
    session = get_session()
    
    health_endpoints = ["/health", "/healthz", "/ready"]
    
    # First exceed rate limit on main endpoint
    for i in range(101):
        session.get(f"{BASE_URL}/", timeout=2)
    
    # Check all health endpoints still return 200 even after rate limit is exceeded
    for endpoint in health_endpoints:
        for i in range(50):  # Send multiple requests to ensure no rate limiting applies
            resp = session.get(f"{BASE_URL}{endpoint}", timeout=2)
            assert resp.status_code == 200, f"Expected 200 for {endpoint}, got {resp.status_code}"


def test_ac5_rate_limit_response_headers_correct():
    """AC-5: Verify 429 responses include all required headers with correct values"""
    session = get_session()
    rps = int(os.environ.get("FRONTEND_PROXY_RATE_LIMIT_RPS", "100"))
    
    # Exceed rate limit
    for i in range(rps + 1):
        resp = session.get(f"{BASE_URL}/", timeout=2)
    
    assert resp.status_code == 429, "Expected rate limited response"
    
    # Check all required headers exist
    required_headers = ["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"]
    for header in required_headers:
        assert header in resp.headers, f"Missing required header {header} in 429 response"
    
    # Validate header values
    assert int(resp.headers["X-RateLimit-Limit"]) == rps, f"X-RateLimit-Limit should match configured RPS {rps}"
    assert int(resp.headers["X-RateLimit-Remaining"]) == 0, "X-RateLimit-Remaining should be 0 when rate limited"
    assert int(resp.headers["Retry-After"]) > 0, "Retry-After should be a positive integer"
    assert int(resp.headers["X-RateLimit-Reset"]) > time.time(), "X-RateLimit-Reset should be a future UNIX timestamp"


def test_ac6_all_non_health_requests_rate_limited():
    """AC-6: Verify all non-health check external requests are subject to rate limits"""
    session = get_session()
    test_paths = ["/", "/api/products", "/cart", "/checkout", "/static/js/main.js"]
    
    # Exceed rate limit
    for i in range(101):
        session.get(f"{BASE_URL}/", timeout=2)
    
    # All non-health paths should return 429 when rate limit is exceeded
    for path in test_paths:
        resp = session.get(f"{BASE_URL}{path}", timeout=2)
        assert resp.status_code == 429, f"Expected 429 for path {path}, got {resp.status_code}"
