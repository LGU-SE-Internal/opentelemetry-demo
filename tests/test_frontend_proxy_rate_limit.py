import pytest
import requests
import concurrent.futures
import time
import os
from prometheus_parser import parse_metrics

FRONTEND_PROXY_URL = os.getenv("FRONTEND_PROXY_URL", "http://localhost:8080")
ENVOY_ADMIN_URL = os.getenv("ENVOY_ADMIN_URL", "http://localhost:8001")

def test_ac1_public_rate_limit_rps_10_burst_20():
    # AC-1: When FRONTEND_PROXY_RATE_LIMIT_PUBLIC_RPS=10 and FRONTEND_PROXY_RATE_LIMIT_PUBLIC_BURST=20 are set,
    # sending 30 concurrent requests to /api/products in 1s returns exactly 10 200s and 20 429s
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_RPS"] = "10"
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_BURST"] = "20"
    
    # Reset any previous state
    requests.post(f"{ENVOY_ADMIN_URL}/reset_counters")
    
    url = f"{FRONTEND_PROXY_URL}/api/products"
    num_requests = 30
    responses = []
    
    def send_request():
        try:
            return requests.get(url, timeout=2).status_code
        except:
            return -1
    
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(send_request) for _ in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            responses.append(future.result())
    duration = time.time() - start_time
    assert duration < 1.0, "Requests took longer than 1 second window"
    
    count_200 = responses.count(200)
    count_429 = responses.count(429)
    
    assert count_200 == 10, f"Expected 10 200 OK responses, got {count_200}"
    assert count_429 == 20, f"Expected 20 429 responses, got {count_429}"
    assert all(r in (200, 429) for r in responses), f"Unexpected status codes found: {set(responses) - {200, 429}}"

def test_ac2_health_check_exempt_from_rate_limit():
    # AC-2: Sending 100 concurrent requests to /healthz in 1s returns all 200 OK, regardless of public limits
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_RPS"] = "10"
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_BURST"] = "20"
    
    # Reset counters
    requests.post(f"{ENVOY_ADMIN_URL}/reset_counters")
    
    url = f"{FRONTEND_PROXY_URL}/healthz"
    num_requests = 100
    responses = []
    
    def send_request():
        try:
            return requests.get(url, timeout=2).status_code
        except:
            return -1
    
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        futures = [executor.submit(send_request) for _ in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            responses.append(future.result())
    duration = time.time() - start_time
    assert duration < 1.0, "Requests took longer than 1 second window"
    
    count_200 = responses.count(200)
    assert count_200 == 100, f"Expected all 100 health check requests to return 200 OK, got {count_200} 200s"
    assert 429 not in responses, "Health check endpoints should be exempt from rate limiting, got 429 responses"

def test_ac3_no_rate_limit_when_env_vars_unset():
    # AC-3: When no rate limit env vars set, all requests return 200 OK even at 1000 RPS
    # Clear any rate limit env vars
    for key in list(os.environ.keys()):
        if key.startswith("FRONTEND_PROXY_RATE_LIMIT_"):
            del os.environ[key]
    
    # Reset counters
    requests.post(f"{ENVOY_ADMIN_URL}/reset_counters")
    
    url = f"{FRONTEND_PROXY_URL}/api/products"
    num_requests = 100
    responses = []
    
    def send_request():
        try:
            return requests.get(url, timeout=2).status_code
        except:
            return -1
    
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        futures = [executor.submit(send_request) for _ in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            responses.append(future.result())
    duration = time.time() - start_time
    assert duration < 1.0, "Requests took longer than 1 second window"
    
    count_200 = responses.count(200)
    assert count_200 == num_requests, f"Expected all {num_requests} requests to return 200 OK when no rate limits set, got {count_200} 200s"
    assert 429 not in responses, "No rate limits configured, should not get 429 responses"

def test_ac4_429_response_format():
    # AC-4: All 429 responses include Content-Type: application/json header and exact response body
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_RPS"] = "1"
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_BURST"] = "0"
    
    # Reset counters
    requests.post(f"{ENVOY_ADMIN_URL}/reset_counters")
    
    url = f"{FRONTEND_PROXY_URL}/api/products"
    # First request to consume the 1 RPS limit
    requests.get(url)
    # Second request should get 429
    response = requests.get(url)
    
    assert response.status_code == 429, f"Expected 429 status code, got {response.status_code}"
    assert response.headers.get("Content-Type") == "application/json", f"Expected Content-Type: application/json header, got {response.headers.get('Content-Type')}"
    
    expected_body = {
        "error": "Too Many Requests",
        "message": "Rate limit exceeded. Try again later."
    }
    assert response.json() == expected_body, f"Response body does not match expected. Got: {response.json()}"

def test_ac5_rate_limit_metrics_tracked():
    # AC-5: When 10 requests denied by public rate limit, envoy_http_local_rate_limiter_denied{group="public"} increments by 10
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_RPS"] = "5"
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_BURST"] = "0"
    
    # Reset counters
    requests.post(f"{ENVOY_ADMIN_URL}/reset_counters")
    
    url = f"{FRONTEND_PROXY_URL}/api/products"
    num_requests = 15
    responses = []
    
    def send_request():
        try:
            return requests.get(url, timeout=2).status_code
        except:
            return -1
    
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(send_request) for _ in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            responses.append(future.result())
    
    count_429 = responses.count(429)
    assert count_429 == 10, f"Expected 10 denied requests for metric test, got {count_429}"
    
    # Scrape metrics
    metrics_response = requests.get(f"{ENVOY_ADMIN_URL}/stats/prometheus")
    metrics = parse_metrics(metrics_response.text)
    
    denied_metric = next((m for m in metrics if m.name == "envoy_http_local_rate_limiter_denied" and m.labels.get("group") == "public"), None)
    assert denied_metric is not None, "envoy_http_local_rate_limiter_denied metric with group=public not found"
    assert denied_metric.value == 10, f"Expected denied metric value of 10, got {denied_metric.value}"
    
    enabled_metric = next((m for m in metrics if m.name == "envoy_http_local_rate_limiter_enabled" and m.labels.get("group") == "public"), None)
    assert enabled_metric is not None, "envoy_http_local_rate_limiter_enabled metric with group=public not found"
    assert enabled_metric.value == 1, "Rate limit enabled metric should be 1 when public rate limit is set"

def test_ac6_custom_group_rate_limit_independent():
    # AC-6: When FRONTEND_PROXY_RATE_LIMIT_CHECKOUT_RPS=5 is set, requests to /api/checkout are rate limited at 5 RPS independent of public group
    os.environ["FRONTEND_PROXY_RATE_LIMIT_PUBLIC_RPS"] = "100"
    os.environ["FRONTEND_PROXY_RATE_LIMIT_CHECKOUT_RPS"] = "5"
    os.environ["FRONTEND_PROXY_RATE_LIMIT_CHECKOUT_BURST"] = "0"
    
    # Reset counters
    requests.post(f"{ENVOY_ADMIN_URL}/reset_counters")
    
    url = f"{FRONTEND_PROXY_URL}/api/checkout"
    num_requests = 20
    responses = []
    
    def send_request():
        try:
            return requests.get(url, timeout=2).status_code
        except:
            return -1
    
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(send_request) for _ in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            responses.append(future.result())
    
    count_200 = responses.count(200)
    count_429 = responses.count(429)
    
    assert count_200 == 5, f"Expected 5 200 OK responses for checkout group, got {count_200}"
    assert count_429 == 15, f"Expected 15 429 responses for checkout group, got {count_429}"
    
    # Verify metrics for custom group
    metrics_response = requests.get(f"{ENVOY_ADMIN_URL}/stats/prometheus")
    metrics = parse_metrics(metrics_response.text)
    
    denied_metric = next((m for m in metrics if m.name == "envoy_http_local_rate_limiter_denied" and m.labels.get("group") == "checkout"), None)
    assert denied_metric is not None, "envoy_http_local_rate_limiter_denied metric with group=checkout not found"
    assert denied_metric.value == 15, f"Expected denied metric value of 15 for checkout group, got {denied_metric.value}"
