"""
Tests for frontend-proxy TLS/mTLS functionality as per issue #2075 spec
All tests assume no implementation exists currently, so they should fail by default
"""
import os
import pytest
import requests
import subprocess
import time
from pathlib import Path

TEST_CERT_DIR = Path(__file__).parent / "test_certs"
TEST_SERVER_CERT = TEST_CERT_DIR / "server.crt"
TEST_SERVER_KEY = TEST_CERT_DIR / "server.key"
TEST_CA_CERT = TEST_CERT_DIR / "ca.crt"
TEST_CLIENT_CERT = TEST_CERT_DIR / "client.crt"
TEST_CLIENT_KEY = TEST_CERT_DIR / "client.key"


def setup_module():
    """Create test certificates for testing - self signed for test purposes"""
    TEST_CERT_DIR.mkdir(exist_ok=True)
    # Generate test CA, server, client certs - run openssl commands
    subprocess.run([
        "openssl", "req", "-x509", "-sha256", "-newkey", "rsa:4096",
        "-days", "1", "-nodes", "-keyout", str(TEST_CA_CERT.with_suffix(".key")),
        "-out", str(TEST_CA_CERT), "-subj", "/CN=test-ca.local"
    ], check=True, capture_output=True)
    # Generate server cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-nodes", "-keyout", str(TEST_SERVER_KEY),
        "-out", str(TEST_SERVER_CERT.with_suffix(".csr")), "-subj", "/CN=frontend-proxy.local"
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "x509", "-req", "-in", str(TEST_SERVER_CERT.with_suffix(".csr")),
        "-CA", str(TEST_CA_CERT), "-CAkey", str(TEST_CA_CERT.with_suffix(".key")),
        "-CAcreateserial", "-out", str(TEST_SERVER_CERT), "-days", "1", "-sha256"
    ], check=True, capture_output=True)
    # Generate client cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-nodes", "-keyout", str(TEST_CLIENT_KEY),
        "-out", str(TEST_CLIENT_CERT.with_suffix(".csr")), "-subj", "/CN=test-client.local"
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "x509", "-req", "-in", str(TEST_CLIENT_CERT.with_suffix(".csr")),
        "-CA", str(TEST_CA_CERT), "-CAkey", str(TEST_CA_CERT.with_suffix(".key")),
        "-CAcreateserial", "-out", str(TEST_CLIENT_CERT), "-days", "1", "-sha256"
    ], check=True, capture_output=True)


def teardown_module():
    """Clean up test certs"""
    for f in TEST_CERT_DIR.glob("*"):
        f.unlink()
    TEST_CERT_DIR.rmdir()


def start_frontend_proxy(env_vars):
    """Helper to start frontend proxy with given env vars, return process"""
    env = os.environ.copy()
    env.update(env_vars)
    proc = subprocess.Popen(
        ["envoy", "-c", "/etc/envoy/envoy.yaml", "--service-node", "frontend-proxy", "--service-cluster", "frontend-proxy"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    # Wait for proxy to start
    time.sleep(5)
    return proc


def stop_frontend_proxy(proc):
    """Helper to stop proxy process"""
    proc.terminate()
    proc.wait(timeout=10)


def test_ac1_default_plaintext_behavior():
    """AC-1: No TLS env vars set → plaintext on 8080, upstream plaintext, no 8443 listener"""
    proc = start_frontend_proxy({})
    try:
        # Test plaintext port 8080 works
        resp = requests.get("http://localhost:8080/health", timeout=2)
        assert resp.status_code == 200, "Plaintext health check failed"
        
        # Test port 8443 is not open (should throw connection error)
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("https://localhost:8443/health", verify=str(TEST_CA_CERT), timeout=2)
            
        # Verify upstream requests are plaintext (check access logs or backend metrics if available, simple check here)
        # For test purposes, confirm no TLS handshake errors to upstreams that expect plaintext
    finally:
        stop_frontend_proxy(proc)


def test_ac2a_https_listener_enabled_when_cert_key_set():
    """AC-2a: TLS cert/key set → listener on 8443 enabled"""
    env = {
        "FRONTEND_PROXY_TLS_CERT_PATH": str(TEST_SERVER_CERT),
        "FRONTEND_PROXY_TLS_KEY_PATH": str(TEST_SERVER_KEY)
    }
    proc = start_frontend_proxy(env)
    try:
        # Test HTTPS connection works
        resp = requests.get("https://localhost:8443/health", verify=str(TEST_CA_CERT), timeout=2)
        assert resp.status_code == 200, "HTTPS health check failed"
    finally:
        stop_frontend_proxy(proc)


def test_ac2b_tls_handshake_presents_correct_cert():
    """AC-2b: TLS handshake returns correct configured server certificate"""
    env = {
        "FRONTEND_PROXY_TLS_CERT_PATH": str(TEST_SERVER_CERT),
        "FRONTEND_PROXY_TLS_KEY_PATH": str(TEST_SERVER_KEY)
    }
    proc = start_frontend_proxy(env)
    try:
        import ssl
        import socket
        context = ssl.create_default_context(cafile=str(TEST_CA_CERT))
        with socket.create_connection(("localhost", 8443)) as sock:
            with context.wrap_socket(sock, server_hostname="frontend-proxy.local") as ssock:
                cert = ssock.getpeercert()
                assert cert["subject"][-1][0][1] == "frontend-proxy.local", "Incorrect server certificate presented"
    finally:
        stop_frontend_proxy(proc)


def test_ac2c_plaintext_8080_remains_enabled():
    """AC-2c: When TLS is enabled, port 8080 plaintext still works"""
    env = {
        "FRONTEND_PROXY_TLS_CERT_PATH": str(TEST_SERVER_CERT),
        "FRONTEND_PROXY_TLS_KEY_PATH": str(TEST_SERVER_KEY)
    }
    proc = start_frontend_proxy(env)
    try:
        resp = requests.get("http://localhost:8080/health", timeout=2)
        assert resp.status_code == 200, "Plaintext port 8080 stopped working after TLS enabled"
    finally:
        stop_frontend_proxy(proc)


def test_ac3a_mtls_enforces_client_cert():
    """AC-3a: mTLS enabled → require valid client cert for 8443 connections"""
    env = {
        "FRONTEND_PROXY_TLS_CERT_PATH": str(TEST_SERVER_CERT),
        "FRONTEND_PROXY_TLS_KEY_PATH": str(TEST_SERVER_KEY),
        "FRONTEND_PROXY_MTLS_CA_CERT_PATH": str(TEST_CA_CERT),
        "FRONTEND_PROXY_MTLS_ENABLE": "true"
    }
    proc = start_frontend_proxy(env)
    try:
        # Request without client cert should fail handshake
        with pytest.raises(requests.exceptions.SSLError):
            requests.get("https://localhost:8443/health", verify=str(TEST_CA_CERT), timeout=2)
        
        # Request with valid client cert should succeed
        resp = requests.get(
            "https://localhost:8443/health",
            verify=str(TEST_CA_CERT),
            cert=(str(TEST_CLIENT_CERT), str(TEST_CLIENT_KEY)),
            timeout=2
        )
        assert resp.status_code == 200, "Valid client cert rejected"
    finally:
        stop_frontend_proxy(proc)


def test_ac3b_invalid_client_cert_rejected():
    """AC-3b: Invalid client cert → TLS handshake error"""
    # Create invalid client cert (signed by different CA)
    invalid_ca = TEST_CERT_DIR / "invalid_ca.crt"
    invalid_client_cert = TEST_CERT_DIR / "invalid_client.crt"
    invalid_client_key = TEST_CERT_DIR / "invalid_client.key"
    subprocess.run([
        "openssl", "req", "-x509", "-sha256", "-newkey", "rsa:4096",
        "-days", "1", "-nodes", "-keyout", str(invalid_ca.with_suffix(".key")),
        "-out", str(invalid_ca), "-subj", "/CN=invalid-ca.local"
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-nodes", "-keyout", str(invalid_client_key),
        "-out", str(invalid_client_cert.with_suffix(".csr")), "-subj", "/CN=malicious-client.local"
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "x509", "-req", "-in", str(invalid_client_cert.with_suffix(".csr")),
        "-CA", str(invalid_ca), "-CAkey", str(invalid_ca.with_suffix(".key")),
        "-CAcreateserial", "-out", str(invalid_client_cert), "-days", "1", "-sha256"
    ], check=True, capture_output=True)

    env = {
        "FRONTEND_PROXY_TLS_CERT_PATH": str(TEST_SERVER_CERT),
        "FRONTEND_PROXY_TLS_KEY_PATH": str(TEST_SERVER_KEY),
        "FRONTEND_PROXY_MTLS_CA_CERT_PATH": str(TEST_CA_CERT),
        "FRONTEND_PROXY_MTLS_ENABLE": "true"
    }
    proc = start_frontend_proxy(env)
    try:
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(
                "https://localhost:8443/health",
                verify=str(TEST_CA_CERT),
                cert=(str(invalid_client_cert), str(invalid_client_key)),
                timeout=2
            )
    finally:
        stop_frontend_proxy(proc)
        for f in [invalid_ca, invalid_ca.with_suffix(".key"), invalid_client_cert, invalid_client_cert.with_suffix(".csr"), invalid_client_key]:
            f.unlink(missing_ok=True)


def test_ac4a_upstream_tls_enabled_encrypts_traffic():
    """AC-4a: Upstream TLS enabled → all upstream requests use TLS"""
    # We assume a test upstream backend is running with TLS enabled for this test
    env = {
        "FRONTEND_PROXY_UPSTREAM_TLS_ENABLE": "true"
    }
    proc = start_frontend_proxy(env)
    try:
        # Send request to proxy, verify backend receives TLS traffic
        # For test purposes, check that request to upstream uses TLS port
        resp = requests.get("http://localhost:8080/test-endpoint", timeout=2)
        # If backend returns proof of TLS connection, validate that
        assert resp.headers.get("X-Connection-Protocol") == "TLS", "Upstream connection not using TLS"
    finally:
        stop_frontend_proxy(proc)


def test_ac4b_upstream_cert_validated_against_system_ca():
    """AC-4b: Upstream TLS enabled → upstream server cert validated against system CA store"""
    env = {
        "FRONTEND_PROXY_UPSTREAM_TLS_ENABLE": "true"
    }
    proc = start_frontend_proxy(env)
    try:
        # Request to upstream with self-signed cert not in system CA should fail
        with pytest.raises(requests.exceptions.HTTPError) as exc_info:
            requests.get("http://localhost:8080/self-signed-endpoint", timeout=2)
        assert exc_info.value.response.status_code in (502, 503), "Proxy accepted invalid upstream certificate"
    finally:
        stop_frontend_proxy(proc)


def test_ac5a_upstream_mtls_presents_client_cert():
    """AC-5a: Upstream TLS + client cert/key set → proxy presents client cert to upstreams"""
    env = {
        "FRONTEND_PROXY_UPSTREAM_TLS_ENABLE": "true",
        "FRONTEND_PROXY_UPSTREAM_TLS_CERT_PATH": str(TEST_CLIENT_CERT),
        "FRONTEND_PROXY_UPSTREAM_TLS_KEY_PATH": str(TEST_CLIENT_KEY)
    }
    proc = start_frontend_proxy(env)
    try:
        # Request to endpoint that requires client cert should succeed
        resp = requests.get("http://localhost:8080/mtls-protected-endpoint", timeout=2)
        assert resp.status_code == 200, "Upstream rejected client certificate from proxy"
    finally:
        stop_frontend_proxy(proc)


def test_ac5b_upstream_client_cert_validated():
    """AC-5b: Upstream requiring client cert validates proxy's presented cert"""
    env = {
        "FRONTEND_PROXY_UPSTREAM_TLS_ENABLE": "true"
        # No client cert/key provided
    }
    proc = start_frontend_proxy(env)
    try:
        # Request to endpoint requiring client cert should fail
        with pytest.raises(requests.exceptions.HTTPError) as exc_info:
            requests.get("http://localhost:8080/mtls-protected-endpoint", timeout=2)
        assert exc_info.value.response.status_code in (502, 503), "Proxy accessed mTLS upstream without client cert"
    finally:
        stop_frontend_proxy(proc)


def test_ac6_partial_configs_fallback_to_plaintext():
    """AC-6: Partial/invalid TLS configs → feature disabled, fallback to plaintext, no crash"""
    # Test case 1: Only cert path provided, no key
    env = {"FRONTEND_PROXY_TLS_CERT_PATH": str(TEST_SERVER_CERT)}
    proc = start_frontend_proxy(env)
    try:
        # 8443 should not be open
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("https://localhost:8443/health", verify=str(TEST_CA_CERT), timeout=2)
        # Plaintext should still work
        resp = requests.get("http://localhost:8080/health", timeout=2)
        assert resp.status_code == 200, "Proxy crashed with partial TLS config"
    finally:
        stop_frontend_proxy(proc)
    
    # Test case 2: mTLS enabled but no CA cert path
    env = {
        "FRONTEND_PROXY_TLS_CERT_PATH": str(TEST_SERVER_CERT),
        "FRONTEND_PROXY_TLS_KEY_PATH": str(TEST_SERVER_KEY),
        "FRONTEND_PROXY_MTLS_ENABLE": "true"
    }
    proc = start_frontend_proxy(env)
    try:
        # HTTPS should work without mTLS enforced
        resp = requests.get("https://localhost:8443/health", verify=str(TEST_CA_CERT), timeout=2)
        assert resp.status_code == 200, "mTLS partial config broke HTTPS listener"
    finally:
        stop_frontend_proxy(proc)
    
    # Test case 3: Upstream TLS enabled but only cert path provided, no key
    env = {
        "FRONTEND_PROXY_UPSTREAM_TLS_ENABLE": "true",
        "FRONTEND_PROXY_UPSTREAM_TLS_CERT_PATH": str(TEST_CLIENT_CERT)
    }
    proc = start_frontend_proxy(env)
    try:
        # Should fallback to no upstream mTLS, plaintext if possible?
        # Or upstream TLS still works without client cert
        resp = requests.get("http://localhost:8080/health", timeout=2)
        assert resp.status_code == 200, "Partial upstream TLS config crashed proxy"
    finally:
        stop_frontend_proxy(proc)
