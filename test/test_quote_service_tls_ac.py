#!/usr/bin/env python3
"""
Integration tests for Quote Service TLS/mTLS implementation.
All tests correspond to Acceptance Criteria from the spec.
"""
import os
import time
import requests
import subprocess
import pytest
from pathlib import Path

QUOTE_SERVICE_PORT = 8080
SERVICE_URL = f"http://localhost:{QUOTE_SERVICE_PORT}"
SERVICE_URL_HTTPS = f"https://localhost:{QUOTE_SERVICE_PORT}"

# Test certificates directory (will be created during test setup)
TEST_CERTS_DIR = Path(__file__).parent / "test_certs"
TEST_CERTS_DIR.mkdir(exist_ok=True)

# Test cert paths
SERVER_CERT = TEST_CERTS_DIR / "server.crt"
SERVER_KEY = TEST_CERTS_DIR / "server.key"
CA_CERT = TEST_CERTS_DIR / "ca.crt"
CLIENT_CERT = TEST_CERTS_DIR / "client.crt"
CLIENT_KEY = TEST_CERTS_DIR / "client.key"
INVALID_CERT = TEST_CERTS_DIR / "invalid.crt"

def generate_test_certs():
    """Generate self-signed test certificates for TLS/mTLS tests"""
    # Clean up old certs
    for f in TEST_CERTS_DIR.glob("*"):
        f.unlink()

    # Generate CA cert
    subprocess.run([
        "openssl", "req", "-x509", "-sha256", "-newkey", "rsa:4096",
        "-days", "1", "-nodes", "-keyout", str(CA_CERT),
        "-out", str(CA_CERT), "-subj", "/CN=Test CA",
        "-addext", "keyUsage = critical, keyCertSign, cRLSign",
        "-addext", "basicConstraints = critical, CA:TRUE"
    ], check=True, capture_output=True)

    # Generate server cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-nodes",
        "-keyout", str(SERVER_KEY), "-out", str(TEST_CERTS_DIR / "server.csr"),
        "-subj", "/CN=localhost"
    ], check=True, capture_output=True)

    subprocess.run([
        "openssl", "x509", "-req", "-in", str(TEST_CERTS_DIR / "server.csr"),
        "-CA", str(CA_CERT), "-CAkey", str(CA_CERT), "-CAcreateserial",
        "-out", str(SERVER_CERT), "-days", "1", "-sha256",
        "-extfile", "/dev/stdin"
    ], input="subjectAltName = DNS:localhost, IP:127.0.0.1", check=True, capture_output=True, text=True)

    # Generate client cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-nodes",
        "-keyout", str(CLIENT_KEY), "-out", str(TEST_CERTS_DIR / "client.csr"),
        "-subj", "/CN=test-client"
    ], check=True, capture_output=True)

    subprocess.run([
        "openssl", "x509", "-req", "-in", str(TEST_CERTS_DIR / "client.csr"),
        "-CA", str(CA_CERT), "-CAkey", str(CA_CERT), "-CAcreateserial",
        "-out", str(CLIENT_CERT), "-days", "1", "-sha256"
    ], check=True, capture_output=True)

    # Create invalid cert
    with open(INVALID_CERT, "w") as f:
        f.write("invalid pem data")

@pytest.fixture(scope="module", autouse=True)
def setup_test_certs():
    generate_test_certs()
    yield
    # Cleanup
    for f in TEST_CERTS_DIR.glob("*"):
        f.unlink()
    TEST_CERTS_DIR.rmdir()

def run_quote_service(env_vars, timeout=5):
    """Run quote service with given env vars, return process and output"""
    env = os.environ.copy()
    env.update(env_vars)
    # Use the quote service entrypoint (adjust path as needed for your setup)
    cmd = ["php", "-S", f"0.0.0.0:{QUOTE_SERVICE_PORT}", "-t", "../src/quote/public"]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Service started successfully and is running
        return proc, None, None
    # Service exited before timeout
    proc.wait()
    return proc, stdout, stderr

def test_ac1_plain_http_no_tls_config():
    """AC-1: No TLS env vars set, service starts successfully and serves plain HTTP"""
    proc, _, _ = run_quote_service({})
    try:
        # Wait for service to start
        time.sleep(2)
        # Test health endpoint
        resp = requests.get(f"{SERVICE_URL}/health", timeout=2)
        assert resp.status_code == 200
        # Test get-quote endpoint
        resp = requests.post(f"{SERVICE_URL}/get-quote", json={"items": []}, timeout=2)
        assert resp.status_code in [200, 400] # 400 is expected for empty items
    finally:
        proc.terminate()
        proc.wait()

def test_ac2_https_valid_tls_config():
    """AC-2: Valid TLS cert/key provided, service serves HTTPS traffic"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY)
    }
    proc, _, _ = run_quote_service(env)
    try:
        time.sleep(2)
        # HTTP should fail
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get(f"{SERVICE_URL}/health", timeout=2)
        # HTTPS should work
        resp = requests.get(f"{SERVICE_URL_HTTPS}/health", verify=str(CA_CERT), timeout=2)
        assert resp.status_code == 200
    finally:
        proc.terminate()
        proc.wait()

def test_ac3_only_tls_cert_provided():
    """AC-3: Only TLS cert path provided, service fails to start with correct error"""
    env = {"QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT)}
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "TLS key path must be provided when TLS cert path is configured" in output

def test_ac3_only_tls_key_provided():
    """AC-3: Only TLS key path provided, service fails to start with correct error"""
    env = {"QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY)}
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "TLS cert path must be provided when TLS key path is configured" in output

def test_ac4_tls_cert_file_not_found():
    """AC-4: TLS cert path points to non-existent file, service fails to start"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": "/nonexistent/cert.crt",
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY)
    }
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "TLS certificate file not found or unreadable" in output

def test_ac4_tls_key_file_not_found():
    """AC-4: TLS key path points to non-existent file, service fails to start"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": "/nonexistent/key.key"
    }
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "TLS private key file not found or unreadable" in output

def test_ac4_invalid_tls_cert():
    """AC-4: Invalid TLS cert provided, service fails to start"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(INVALID_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY)
    }
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "Invalid TLS certificate/key pair" in output

def test_ac5_mtls_enabled_reject_requests_without_client_cert():
    """AC-5: mTLS enabled, requests without client cert are rejected with 403"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY),
        "QUOTESVC_MTLS_ENABLED": "true",
        "QUOTESVC_MTLS_CA_CERT_PATH": str(CA_CERT)
    }
    proc, _, _ = run_quote_service(env)
    try:
        time.sleep(2)
        # Request without client cert should fail with 403 or SSL error
        with pytest.raises((requests.exceptions.SSLError, requests.exceptions.HTTPError)) as excinfo:
            resp = requests.get(f"{SERVICE_URL_HTTPS}/health", verify=str(CA_CERT), timeout=2)
            resp.raise_for_status()
        # Check for 403 if we got a response
        if hasattr(excinfo.value, 'response') and excinfo.value.response is not None:
            assert excinfo.value.response.status_code == 403
    finally:
        proc.terminate()
        proc.wait()

def test_ac5_mtls_enabled_accept_requests_with_valid_client_cert():
    """AC-5: mTLS enabled, requests with valid client cert are accepted"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY),
        "QUOTESVC_MTLS_ENABLED": "true",
        "QUOTESVC_MTLS_CA_CERT_PATH": str(CA_CERT)
    }
    proc, _, _ = run_quote_service(env)
    try:
        time.sleep(2)
        # Request with valid client cert should succeed
        resp = requests.get(f"{SERVICE_URL_HTTPS}/health", 
                            verify=str(CA_CERT), 
                            cert=(str(CLIENT_CERT), str(CLIENT_KEY)),
                            timeout=2)
        assert resp.status_code == 200
    finally:
        proc.terminate()
        proc.wait()

def test_ac6_mtls_enabled_no_ca_path():
    """AC-6: mTLS enabled but no CA cert path provided, service fails to start"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY),
        "QUOTESVC_MTLS_ENABLED": "true"
    }
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "mTLS CA cert path must be provided when mTLS is enabled" in output

def test_ac6_mtls_ca_path_nonexistent():
    """AC-6: mTLS CA cert path points to non-existent file, service fails to start"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY),
        "QUOTESVC_MTLS_ENABLED": "true",
        "QUOTESVC_MTLS_CA_CERT_PATH": "/nonexistent/ca.crt"
    }
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "mTLS CA certificate file not found or unreadable" in output

def test_ac6_mtls_ca_invalid():
    """AC-6: Invalid mTLS CA cert provided, service fails to start"""
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY),
        "QUOTESVC_MTLS_ENABLED": "true",
        "QUOTESVC_MTLS_CA_CERT_PATH": str(INVALID_CERT)
    }
    proc, stdout, stderr = run_quote_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "Invalid mTLS CA certificate" in output

def test_ac7_existing_functionality_over_https():
    """AC-7: Existing functionality behaves identically over HTTPS as HTTP"""
    # First test plain HTTP behavior
    proc_http, _, _ = run_quote_service({})
    try:
        time.sleep(2)
        http_health = requests.get(f"{SERVICE_URL}/health", timeout=2).json()
        http_quote = requests.post(f"{SERVICE_URL}/get-quote", json={"items": [{"price": 100, "quantity": 2}]}, timeout=2).json()
    finally:
        proc_http.terminate()
        proc_http.wait()
    
    # Test HTTPS behavior
    env = {
        "QUOTESVC_TLS_CERT_PATH": str(SERVER_CERT),
        "QUOTESVC_TLS_KEY_PATH": str(SERVER_KEY)
    }
    proc_https, _, _ = run_quote_service(env)
    try:
        time.sleep(2)
        https_health = requests.get(f"{SERVICE_URL_HTTPS}/health", verify=str(CA_CERT), timeout=2).json()
        https_quote = requests.post(f"{SERVICE_URL_HTTPS}/get-quote", json={"items": [{"price": 100, "quantity": 2}]}, verify=str(CA_CERT), timeout=2).json()
    finally:
        proc_https.terminate()
        proc_https.wait()
    
    # Compare responses
    assert http_health == https_health
    assert http_quote == https_quote

def test_ac8_no_tls_config_backward_compatibility():
    """AC-8: No TLS config, no breaking changes or performance degradation"""
    # This test verifies that without any TLS settings, the service behaves exactly as before
    proc, _, _ = run_quote_service({})
    try:
        time.sleep(2)
        # All existing endpoints work
        resp = requests.get(f"{SERVICE_URL}/health", timeout=2)
        assert resp.status_code == 200
        resp = requests.post(f"{SERVICE_URL}/get-quote", json={"items": [{"price": 10, "quantity": 1}]}, timeout=2)
        assert resp.status_code == 200
        # Test rate limiting if applicable (adjust based on existing rate limit rules)
        for _ in range(10):
            requests.post(f"{SERVICE_URL}/get-quote", json={"items": [{"price": 10, "quantity": 1}]}, timeout=2)
        # 11th request should be rate limited if that's the existing rule
        resp = requests.post(f"{SERVICE_URL}/get-quote", json={"items": [{"price": 10, "quantity": 1}]}, timeout=2)
        assert resp.status_code in [200, 429] # 429 if rate limited
    finally:
        proc.terminate()
        proc.wait()
EOF && ls -la ./test/test_quote_service_tls_ac.py
