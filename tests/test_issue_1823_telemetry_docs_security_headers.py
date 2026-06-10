#!/usr/bin/env python3
import pytest
import requests

# Default telemetry-docs service endpoint for integration tests
SERVICE_URL = "http://telemetry-docs:8080"
HTTPS_SERVICE_URL = "https://telemetry-docs:8443"

def test_ac1_x_frame_options_header():
    """AC-1: Verify X-Frame-Options: DENY header is present on all responses"""
    # Test root path (200 response)
    resp = requests.get(SERVICE_URL, verify=False)
    assert "X-Frame-Options" in resp.headers, "X-Frame-Options header missing on 200 response"
    assert resp.headers["X-Frame-Options"] == "DENY", "X-Frame-Options header value incorrect"
    
    # Test non-existent path (404 response)
    resp = requests.get(f"{SERVICE_URL}/non/existent/path", verify=False)
    assert "X-Frame-Options" in resp.headers, "X-Frame-Options header missing on 404 response"
    assert resp.headers["X-Frame-Options"] == "DENY", "X-Frame-Options header value incorrect on 404"


def test_ac2_x_content_type_options_header():
    """AC-2: Verify X-Content-Type-Options: nosniff header is present on all responses"""
    # Test static asset response
    resp = requests.get(f"{SERVICE_URL}/", verify=False)
    assert "X-Content-Type-Options" in resp.headers, "X-Content-Type-Options header missing on HTML response"
    assert resp.headers["X-Content-Type-Options"] == "nosniff", "X-Content-Type-Options header value incorrect"
    
    # Test redirect response (3xx)
    # Assume /docs redirects to /docs/
    resp = requests.get(f"{SERVICE_URL}/docs", allow_redirects=False, verify=False)
    if 300 <= resp.status_code < 400:
        assert "X-Content-Type-Options" in resp.headers, "X-Content-Type-Options header missing on 3xx response"
        assert resp.headers["X-Content-Type-Options"] == "nosniff", "X-Content-Type-Options header value incorrect on redirect"


def test_ac3_x_xss_protection_header():
    """AC-3: Verify X-XSS-Protection: 1; mode=block header is present on all responses"""
    resp = requests.get(SERVICE_URL, verify=False)
    assert "X-XSS-Protection" in resp.headers, "X-XSS-Protection header missing"
    assert resp.headers["X-XSS-Protection"] == "1; mode=block", "X-XSS-Protection header value incorrect"
    
    # Test server error response (5xx) - force a bad request if possible
    resp = requests.get(f"{SERVICE_URL}/%invalid_url", verify=False)
    if 500 <= resp.status_code < 600:
        assert "X-XSS-Protection" in resp.headers, "X-XSS-Protection header missing on 5xx response"


def test_ac4_strict_transport_security_header_https():
    """AC-4: Verify HSTS header is present on HTTPS responses only"""
    # Test HTTPS endpoint
    try:
        resp = requests.get(HTTPS_SERVICE_URL, verify=False)
        assert "Strict-Transport-Security" in resp.headers, "HSTS header missing on HTTPS response"
        assert resp.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains", "HSTS header value incorrect"
    except requests.exceptions.ConnectionError:
        pytest.skip("HTTPS endpoint not available for HSTS test")
    
    # Test HTTP endpoint - should NOT have HSTS header
    resp = requests.get(SERVICE_URL, verify=False)
    assert "Strict-Transport-Security" not in resp.headers, "HSTS header should NOT be present on HTTP responses"


def test_ac5_content_security_policy_header():
    """AC-5: Verify Content-Security-Policy header is configured correctly for static docs"""
    resp = requests.get(SERVICE_URL, verify=False)
    assert "Content-Security-Policy" in resp.headers, "Content-Security-Policy header missing"
    csp = resp.headers["Content-Security-Policy"]
    
    # Verify required CSP directives are present
    assert "default-src 'self'" in csp, "CSP missing default-src 'self' directive"
    assert "script-src 'self' 'unsafe-inline'" in csp, "CSP missing valid script-src directive for docs"
    assert "style-src 'self' 'unsafe-inline'" in csp, "CSP missing valid style-src directive for docs"
    assert "img-src 'self' data:" in csp, "CSP missing valid img-src directive for docs"
    assert "frame-src 'none'" in csp, "CSP missing frame-src 'none' directive"
    assert "object-src 'none'" in csp, "CSP missing object-src 'none' directive"


def test_ac6_headers_applied_to_all_status_codes():
    """AC-6: Verify security headers are applied to all HTTP status code responses"""
    test_paths = [
        ("/", 200),
        ("/non-existent-path-1234", 404),
        ("/docs", 301 if requests.get(f"{SERVICE_URL}/docs", allow_redirects=False, verify=False).status_code == 301 else 200)
    ]
    
    required_headers = [
        "X-Frame-Options",
        "X-Content-Type-Options",
        "X-XSS-Protection",
        "Content-Security-Policy"
    ]
    
    for path, expected_status in test_paths:
        resp = requests.get(f"{SERVICE_URL}{path}", allow_redirects=False, verify=False)
        for header in required_headers:
            assert header in resp.headers, f"Header {header} missing on {resp.status_code} response for path {path}"


def test_ac7_existing_functionality_unchanged():
    """AC-7: Verify existing static content serving functionality still works"""
    # Test root HTML content is served
    resp = requests.get(SERVICE_URL, verify=False)
    assert resp.status_code == 200, "Root path should return 200 OK"
    assert "text/html" in resp.headers["Content-Type"], "Root path should return HTML content"
    
    # Test static assets are served
    # Try common static asset paths used by docs sites
    for asset_path in ["/assets/styles.css", "/assets/main.js", "/favicon.ico"]:
        resp = requests.get(f"{SERVICE_URL}{asset_path}", verify=False)
        if resp.status_code == 200:
            # Verify content type is appropriate for asset
            if asset_path.endswith(".css"):
                assert "text/css" in resp.headers["Content-Type"]
            elif asset_path.endswith(".js"):
                assert "application/javascript" in resp.headers["Content-Type"]
