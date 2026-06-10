#!/usr/bin/env python3
import requests
import os
from datetime import datetime, timedelta
import pytest

# Get service base URLs from env or use defaults
SERVICE_HTTP = os.getenv("IMAGE_PROVIDER_HTTP", "http://localhost:80")
SERVICE_HTTPS = os.getenv("IMAGE_PROVIDER_HTTPS", "https://localhost:443")

# Test assets (assumed to exist per standard demo setup)
IMAGE_ASSETS = [
    "/static/products/OLJCESPC7Z/1.jpg",
    "/static/products/66VCHSJNUP/2.png",
    "/static/products/L9ECAV7KIM/3.webp",
    "/static/icons/cart.svg",
    "/static/error.gif"
]
CSS_ASSETS = ["/static/styles.css"]
JS_ASSETS = ["/static/app.js"]
HTML_ASSETS = ["/static/index.html"]
MONITORING_ENDPOINTS = ["/health", "/ready", "/status"]

# Helper to check Expires header is correct delta
def assert_expires_header(response, expected_delta_days: int):
    expires_str = response.headers.get("Expires")
    assert expires_str is not None, "Expires header missing"
    # Parse Expires date (format: Wed, 21 Oct 2015 07:28:00 GMT)
    expires_date = datetime.strptime(expires_str, "%a, %d %b %Y %H:%M:%S %Z")
    now = datetime.utcnow()
    delta = expires_date - now
    # Allow 10 second tolerance for request latency
    assert abs(delta.total_seconds() - expected_delta_days * 86400) < 10, \
        f"Expires header incorrect, expected {expected_delta_days} days from now"

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("asset_path", IMAGE_ASSETS)
def test_ac1_image_cache_control_header(base_url, asset_path):
    """AC-1: Image assets have correct Cache-Control header"""
    response = requests.get(f"{base_url}{asset_path}", verify=False)
    assert response.status_code in [200, 304], f"Asset not found: {asset_path}"
    cache_control = response.headers.get("Cache-Control")
    assert cache_control is not None, "Cache-Control header missing"
    assert cache_control == "public, max-age=31536000, immutable", \
        f"Cache-Control value incorrect: {cache_control}"

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("asset_path", IMAGE_ASSETS)
def test_ac2_image_expires_header(base_url, asset_path):
    """AC-2: Image assets have Expires header set to 1 year from response time"""
    response = requests.get(f"{base_url}{asset_path}", verify=False)
    assert response.status_code in [200, 304], f"Asset not found: {asset_path}"
    assert_expires_header(response, expected_delta_days=365)

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("endpoint_path", MONITORING_ENDPOINTS)
def test_ac4_monitoring_endpoints_cache_headers(base_url, endpoint_path):
    """AC-4: Monitoring endpoints have no-cache headers"""
    response = requests.get(f"{base_url}{endpoint_path}", verify=False)
    assert response.status_code in [200, 503], f"Monitoring endpoint failed: {endpoint_path}"
    cache_control = response.headers.get("Cache-Control")
    assert cache_control is not None, "Cache-Control header missing for monitoring endpoint"
    assert cache_control == "no-cache, no-store, must-revalidate", \
        f"Cache-Control value incorrect for monitoring: {cache_control}"
    expires = response.headers.get("Expires")
    assert expires == "0", f"Expires header incorrect for monitoring: {expires}"

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("asset_path", CSS_ASSETS + JS_ASSETS)
def test_ac5_css_js_cache_control_header(base_url, asset_path):
    """AC-5: CSS/JS assets have correct Cache-Control header"""
    response = requests.get(f"{base_url}{asset_path}", verify=False)
    assert response.status_code in [200, 304], f"Asset not found: {asset_path}"
    cache_control = response.headers.get("Cache-Control")
    assert cache_control is not None, "Cache-Control header missing"
    assert cache_control == "public, max-age=86400", \
        f"Cache-Control value incorrect for CSS/JS: {cache_control}"

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("asset_path", HTML_ASSETS)
def test_ac6_html_cache_control_header(base_url, asset_path):
    """AC-6: HTML assets have correct Cache-Control header"""
    response = requests.get(f"{base_url}{asset_path}", verify=False)
    assert response.status_code in [200, 304], f"Asset not found: {asset_path}"
    cache_control = response.headers.get("Cache-Control")
    assert cache_control is not None, "Cache-Control header missing"
    assert cache_control == "public, max-age=300", \
        f"Cache-Control value incorrect for HTML: {cache_control}"

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("asset_path", IMAGE_ASSETS + CSS_ASSETS + JS_ASSETS + HTML_ASSETS)
def test_ac7_assets_served_correctly(base_url, asset_path):
    """AC-7: All assets are served correctly with 200/304 status codes"""
    response = requests.get(f"{base_url}{asset_path}", verify=False)
    assert response.status_code in [200, 304], f"Asset not served correctly: {asset_path}, status: {response.status_code}"
    assert len(response.content) > 0, f"Empty response for asset: {asset_path}"

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("endpoint_path", MONITORING_ENDPOINTS)
def test_ac7_monitoring_works_correctly(base_url, endpoint_path):
    """AC-7: Monitoring endpoints return correct status responses"""
    response = requests.get(f"{base_url}{endpoint_path}", verify=False)
    # Health endpoints return 200 if healthy, 503 if not, both are valid
    assert response.status_code in [200, 503], f"Monitoring endpoint failed: {endpoint_path}, status: {response.status_code}"

@pytest.mark.parametrize("base_url", [SERVICE_HTTP, SERVICE_HTTPS])
@pytest.mark.parametrize("asset_path", IMAGE_ASSETS + CSS_ASSETS + JS_ASSETS + HTML_ASSETS)
def test_ac8_no_assets_blocked(base_url, asset_path):
    """AC-8: No assets are blocked from being served due to header config"""
    response = requests.get(f"{base_url}{asset_path}", verify=False)
    # 403/404 would indicate blocked or missing
    assert response.status_code not in [403, 404], f"Asset blocked or missing: {asset_path}, status: {response.status_code}"
