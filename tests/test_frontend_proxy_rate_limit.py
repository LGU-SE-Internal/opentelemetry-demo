import os
import time
import requests
import pytest

FRONTEND_PROXY_URL = "http://localhost:8080"
ENVOY_ADMIN_URL = "http://localhost:9901"

@pytest.fixture(autouse=True)
def reset_env_var():
    """Reset rate limit env var before each test"""
    if "FRONTEND_PROXY_RATE_LIMIT_RPS" in os.environ:
        del os.environ["FRONTEND_PROXY_RATE_LIMIT_RPS"]

def test_ac1_exceed_rate_limit_returns_429_with_header():
    """AC1: Requests over threshold return 429 with x-envoy-ratelimited header"""
    # Set low RPS for testing
    os.environ["FRONTEND_PROXY_RATE_LIMIT_RPS"] = "10"
    # Restart proxy would be needed here, but for test failing now we just check
    success_count = 0
    denied_count = 0
    start = time.time()
    while time.time() - start < 1.0:
        try:
            resp = requests.get(f"{FRONTEND_PROXY_URL}/", timeout=0.1)
            if resp.status_code == 200:
                success_count +=1
            elif resp.status_code == 429:
                denied_count +=1
                assert "x-envoy-ratelimited" in resp.headers
                assert resp.headers["x-envoy-ratelimited"] == "true"
        except Exception:
            pass
    # Should have some denied requests when threshold is exceeded
    assert denied_count > 0, "No requests were rate limited even after exceeding threshold"

def test_ac2_default_rate_limit_is_100_rps():
    """AC2: Default rate limit is 100 RPS when env var is not set"""
    # Ensure env var is not set
    assert "FRONTEND_PROXY_RATE_LIMIT_RPS" not in os.environ
    # Send 150 requests in 1 second
    success_count = 0
    denied_count = 0
    start = time.time()
    for i in range(150):
        try:
            resp = requests.get(f"{FRONTEND_PROXY_URL}/", timeout=0.1)
            if resp.status_code == 200:
                success_count +=1
            elif resp.status_code == 429:
                denied_count +=1
        except Exception:
            pass
    # Should deny ~50 requests if limit is 100
    assert abs(success_count - 100) < 10, f"Expected ~100 successful requests, got {success_count}"
    assert denied_count > 0, "No requests denied with default 100 RPS limit when sending 150 requests"

def test_ac3_custom_rate_limit_value_applied():
    """AC3: Custom FRONTEND_PROXY_RATE_LIMIT_RPS value is used as threshold"""
    custom_rps = 50
    os.environ["FRONTEND_PROXY_RATE_LIMIT_RPS"] = str(custom_rps)
    # Send 75 requests in 1 second
    success_count = 0
    denied_count = 0
    start = time.time()
    for i in range(75):
        try:
            resp = requests.get(f"{FRONTEND_PROXY_URL}/", timeout=0.1)
            if resp.status_code == 200:
                success_count +=1
            elif resp.status_code == 429:
                denied_count +=1
        except Exception:
            pass
    # Should deny ~25 requests if limit is 50
    assert abs(success_count - custom_rps) < 5, f"Expected ~{custom_rps} successful requests with custom RPS, got {success_count}"
    assert denied_count > 0, "No requests denied with custom RPS limit"

def test_ac4_rate_limit_metrics_exposed():
    """AC4: Rate limit metrics are available on Envoy admin endpoint"""
    resp = requests.get(f"{ENVOY_ADMIN_URL}/stats/prometheus", timeout=5)
    assert resp.status_code == 200
    metrics = resp.text
    assert "envoy_http_local_rate_limit_enabled" in metrics, "Missing enabled metric"
    assert "envoy_http_local_rate_limit_denied" in metrics, "Missing denied metric"
    assert "envoy_http_local_rate_limit_ok" in metrics, "Missing ok metric"
    
    # Check enabled metric is 1
    for line in metrics.splitlines():
        if line.startswith("envoy_http_local_rate_limit_enabled"):
            assert line.strip().endswith("1"), "Rate limit is not enabled"
            break
    else:
        pytest.fail("Enabled metric not found")

def test_ac5_all_endpoints_and_methods_rate_limited():
    """AC5: All ingress HTTP requests (any path, any method) are rate limited"""
    os.environ["FRONTEND_PROXY_RATE_LIMIT_RPS"] = "5"
    test_endpoints = [
        ("/", "GET"),
        ("/api/products", "GET"),
        ("/api/cart", "POST"),
        ("/api/checkout", "PUT"),
        ("/invalid-path", "DELETE"),
    ]
    for path, method in test_endpoints:
        success_count = 0
        denied_count = 0
        start = time.time()
        while time.time() - start < 0.5:
            try:
                req_method = getattr(requests, method.lower())
                resp = req_method(f"{FRONTEND_PROXY_URL}{path}", timeout=0.1, json={} if method in ["POST", "PUT"] else None)
                if resp.status_code not in [404, 405, 200, 201]:
                    if resp.status_code == 429:
                        denied_count +=1
                    continue
                success_count +=1
            except Exception:
                pass
        assert denied_count > 0, f"{method} {path} was not rate limited"
