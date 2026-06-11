import pytest
import requests
import time
import os
from typing import Dict

# Constants from spec
PUBLIC_ROUTES = ["/api/products", "/api/categories", "/api/search"]
HEALTH_ROUTES = ["/health", "/ready", "/live"]
CHECKOUT_ROUTES = ["/api/cart", "/api/checkout", "/api/payment"]

RATE_LIMIT_HEADERS = [
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset"
]

def get_base_url() -> str:
    """Get base URL of frontend proxy from environment"""
    return os.environ.get("FRONTEND_PROXY_URL", "http://localhost:8080")

def send_request(route: str, headers: Dict = None) -> requests.Response:
    """Send a request to the given route on frontend proxy"""
    url = f"{get_base_url()}{route}"
    return requests.get(url, headers=headers or {})

@pytest.mark.ac1
def test_ac1_default_rate_limits_enforced_when_enabled():
    """AC-1: When RATE_LIMIT_ENABLED=true, default rate limits are active for all route groups"""
    # Test public routes default limit 60 RPM
    route = PUBLIC_ROUTES[0]
    responses = []
    for i in range(61):
        resp = send_request(route)
        responses.append(resp)
        if i < 60:
            assert resp.status_code == 200, f"Request {i+1} should succeed"
    
    # 61st request should return 429
    last_resp = responses[-1]
    assert last_resp.status_code == 429, "61st request should be rate limited"
    
    # Verify headers present
    for header in RATE_LIMIT_HEADERS:
        assert header in last_resp.headers, f"Missing header {header}"
    assert last_resp.headers["X-RateLimit-Limit"] == "60", "Limit should be 60 for public routes"

@pytest.mark.ac2
def test_ac2_custom_rate_limit_overrides_default():
    """AC-2: Custom rate limit variables override default values for route groups"""
    # Assume RATE_LIMIT_PUBLIC_ROUTES_RPM=10 is set in test environment
    route = PUBLIC_ROUTES[0]
    responses = []
    for i in range(11):
        resp = send_request(route)
        responses.append(resp)
        if i < 10:
            assert resp.status_code == 200, f"Request {i+1} should succeed"
    
    # 11th request should return 429
    assert responses[-1].status_code == 429, "11th request should be rate limited with custom limit 10"

@pytest.mark.ac3
def test_ac3_rate_limits_disabled_when_global_toggle_off():
    """AC-3: When RATE_LIMIT_ENABLED=false, no rate limits are enforced"""
    # Assume RATE_LIMIT_ENABLED=false and RATE_LIMIT_PUBLIC_ROUTES_RPM=1 set in test environment
    route = PUBLIC_ROUTES[0]
    responses = []
    for i in range(10):
        resp = send_request(route)
        responses.append(resp)
        assert resp.status_code == 200, f"All 10 requests should succeed when rate limits are disabled"
        
        # Verify no rate limit headers present
        for header in RATE_LIMIT_HEADERS:
            assert header not in resp.headers, f"Header {header} should not be present when rate limits are disabled"

@pytest.mark.ac4
def test_ac4_rate_limit_headers_present_on_all_limited_responses():
    """AC-4: All rate limited responses include full set of X-RateLimit headers"""
    # Send requests until rate limited
    route = PUBLIC_ROUTES[0]
    resp = send_request(route)
    while resp.status_code != 429:
        resp = send_request(route)
    
    # Verify all headers are present
    for header in RATE_LIMIT_HEADERS:
        assert header in resp.headers, f"Missing required header {header}"
    
    # Verify header values are correct format
    assert resp.headers["X-RateLimit-Limit"].isdigit(), "Limit should be integer"
    assert resp.headers["X-RateLimit-Remaining"].isdigit(), "Remaining should be integer"
    assert resp.headers["X-RateLimit-Reset"].isdigit(), "Reset should be unix timestamp integer"
    assert int(resp.headers["X-RateLimit-Reset"]) > int(time.time()), "Reset timestamp should be in future"

@pytest.mark.ac5
def test_ac5_rate_limit_exceeded_response_format():
    """AC-5: Exceeded rate limit returns 429, Retry-After header, and correct body"""
    # Send requests until rate limited
    route = CHECKOUT_ROUTES[0]
    resp = send_request(route)
    while resp.status_code != 429:
        resp = send_request(route)
    
    # Verify status code
    assert resp.status_code == 429, "Should return 429 Too Many Requests"
    
    # Verify Retry-After header exists and is valid
    assert "Retry-After" in resp.headers, "Missing Retry-After header"
    retry_after = int(resp.headers["Retry-After"])
    assert retry_after > 0 and retry_after <= 60, "Retry-After should be between 1 and 60 seconds"
    
    # Verify response body
    body = resp.json()
    assert "error" in body, "Body should have error field"
    assert body["error"] == "Too many requests", "Error message mismatch"
    assert "retry_after" in body, "Body should have retry_after field"
    assert body["retry_after"] == retry_after, "retry_after value should match Retry-After header"

@pytest.mark.ac6
def test_ac6_health_route_rate_limits_independent():
    """AC-6: Health check route rate limits function independently from other groups"""
    # Assume RATE_LIMIT_HEALTH_ROUTES_RPM=5 set in test environment
    # First exhaust health route limit
    health_route = HEALTH_ROUTES[0]
    public_route = PUBLIC_ROUTES[0]
    
    # Send 6 requests to health route
    for i in range(6):
        resp = send_request(health_route)
        if i < 5:
            assert resp.status_code == 200, f"Health request {i+1} should succeed"
    
    # 6th health request should be 429
    health_resp = send_request(health_route)
    assert health_resp.status_code == 429, "Health route should be rate limited"
    
    # Public route request should still succeed (not rate limited)
    public_resp = send_request(public_route)
    assert public_resp.status_code == 200, "Public route should not be rate limited when health route is exhausted"

@pytest.mark.ac7
def test_ac7_rate_limit_env_vars_documented_in_readme():
    """AC-7: All rate limit environment variables are documented in frontend-proxy README.md"""
    readme_path = os.path.join(os.path.dirname(__file__), "../../src/frontend-proxy/README.md")
    assert os.path.exists(readme_path), "Frontend proxy README.md not found"
    
    with open(readme_path, "r") as f:
        readme_content = f.read()
    
    # Verify all environment variables from spec are documented
    expected_vars = [
        "RATE_LIMIT_PUBLIC_ROUTES_RPM",
        "RATE_LIMIT_HEALTH_ROUTES_RPM",
        "RATE_LIMIT_CHECKOUT_ROUTES_RPM",
        "RATE_LIMIT_ENABLED"
    ]
    
    for var in expected_vars:
        assert var in readme_content, f"Environment variable {var} not documented in README"
