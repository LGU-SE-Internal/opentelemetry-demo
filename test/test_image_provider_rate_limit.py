import pytest
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

IMAGE_PROVIDER_BASE_URL = "http://localhost:8080"  # Default image provider port
TEST_IMAGE_PATHS = ["/test.png", "/test.jpg", "/test.jpeg", "/test.gif", "/test.webp", "/test.svg"]
NON_IMAGE_PATHS = ["/test.css", "/test.js", "/test.html", "/test.txt"]
STATUS_PATH = "/status"

def test_ac1_rate_limit_exceeded_returns_429():
    """AC-1: >10 image requests per second per IP return 429 with correct headers and body"""
    # Send 11 requests in quick succession for an image asset
    urls = [f"{IMAGE_PROVIDER_BASE_URL}{path}" for path in TEST_IMAGE_PATHS[:1]] * 11
    
    responses = []
    with ThreadPoolExecutor(max_workers=11) as executor:
        futures = [executor.submit(requests.get, url) for url in urls]
        for future in as_completed(futures):
            responses.append(future.result())
    
    # Count 429 responses
    status_codes = [r.status_code for r in responses]
    assert 429 in status_codes, "Expected at least one 429 response when exceeding rate limit"
    
    # Validate 429 response details
    for r in responses:
        if r.status_code == 429:
            assert "Retry-After" in r.headers
            assert r.headers["Retry-After"] == "1"
            assert r.text.strip() == "Too Many Requests - Please try again later"
            assert r.headers["Content-Type"] == "text/plain; charset=utf-8"
            break

def test_ac2_status_endpoint_exempt_from_rate_limit():
    """AC-2: /status endpoint never returns 429 even at high request rates"""
    # Send 100 requests to /status endpoint quickly
    urls = [f"{IMAGE_PROVIDER_BASE_URL}{STATUS_PATH}"] * 100
    
    responses = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(requests.get, url) for url in urls]
        for future in as_completed(futures):
            responses.append(future.result())
    
    # Verify no 429 responses, all 200 (assuming service is healthy)
    status_codes = [r.status_code for r in responses]
    assert 429 not in status_codes, "/status endpoint should be exempt from rate limits"
    assert all(sc == 200 for sc in status_codes), "/status endpoint should return 200 when healthy"

def test_ac3_rate_limit_tunable_via_environment_variable():
    """AC-3: NGINX_RATE_LIMIT_RPS env var adjusts rate limit value"""
    # This test assumes environment variable is set to 5 before service start
    # Test by sending 6 requests in 1 second, expecting at least one 429
    urls = [f"{IMAGE_PROVIDER_BASE_URL}{TEST_IMAGE_PATHS[0]}"] * 6
    
    responses = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(requests.get, url) for url in urls]
        for future in as_completed(futures):
            responses.append(future.result())
    
    status_codes = [r.status_code for r in responses]
    assert 429 in status_codes, "Expected 429 when exceeding rate limit set via environment variable"

def test_ac4_rate_limits_isolated_per_client_ip():
    """AC-4: Rate limits are isolated per client IP address"""
    # First IP: send 20 requests, should get 429s for excess
    urls_ip_a = [f"{IMAGE_PROVIDER_BASE_URL}{TEST_IMAGE_PATHS[0]}"] * 20
    # Simulate different IP via X-Forwarded-For header
    headers_ip_a = {"X-Forwarded-For": "192.168.1.100"}
    headers_ip_b = {"X-Forwarded-For": "192.168.1.101"}
    
    responses_a = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(requests.get, url, headers=headers_ip_a) for url in urls_ip_a]
        for future in as_completed(futures):
            responses_a.append(future.result())
    
    # Second IP: send 10 requests, should all get 200
    urls_ip_b = [f"{IMAGE_PROVIDER_BASE_URL}{TEST_IMAGE_PATHS[0]}"] * 10
    responses_b = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(requests.get, url, headers=headers_ip_b) for url in urls_ip_b]
        for future in as_completed(futures):
            responses_b.append(future.result())
    
    # Verify IP A got 429s
    assert 429 in [r.status_code for r in responses_a], "IP A should receive 429 for exceeding rate limit"
    # Verify IP B got all 200s
    assert all(r.status_code == 200 for r in responses_b), "IP B should not be affected by IP A's rate limit"

def test_ac5_non_image_assets_not_rate_limited():
    """AC-5: Non-image assets are not subject to rate limits"""
    # Send 50 requests for non-image assets quickly
    urls = [f"{IMAGE_PROVIDER_BASE_URL}{path}" for path in NON_IMAGE_PATHS] * 10
    
    responses = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(requests.get, url) for url in urls]
        for future in as_completed(futures):
            responses.append(future.result())
    
    # Verify no 429 responses
    status_codes = [r.status_code for r in responses]
    assert 429 not in status_codes, "Non-image assets should not be rate limited"
