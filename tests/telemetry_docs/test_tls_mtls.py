#!/usr/bin/env python3
"""Integration tests for telemetry-docs TLS/mTLS functionality"""
import os
import pytest
import subprocess
import requests
import ssl
import socket
from tempfile import TemporaryDirectory

# Helper to run Nginx container with given env vars
def run_nginx_with_env(env_vars, expect_fail=False):
    cmd = ["docker", "run", "--rm", "-p", "8080:80", "-p", "8443:443"]
    for k, v in env_vars.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.append("telemetry-docs:test")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if expect_fail:
        assert result.returncode != 0
        return result.stderr
    else:
        assert result.returncode == 0
        return result

# Test AC1: TLS disabled, only port 80 open, plain HTTP
def test_ac1_tls_disabled_plaintext_only():
    # No TLS env vars set
    env = {}
    run_nginx_with_env(env)
    # Check port 80 works plain HTTP
    resp = requests.get("http://localhost:8080/index.html", timeout=5)
    assert resp.status_code == 200
    # Check port 443 is closed/not listening
    with pytest.raises((ConnectionRefusedError, socket.timeout)):
        ssl.wrap_socket(socket.create_connection(("localhost", 8443), timeout=5))
    # Check no TLS config in rendered nginx.conf
    conf = subprocess.run(["docker", "exec", "telemetry-docs-test", "cat", "/etc/nginx/nginx.conf"], capture_output=True, text=True).stdout
    assert "ssl_certificate" not in conf
    assert "listen 443" not in conf

# Test AC2: TLS enabled, valid certs, redirect HTTP to HTTPS
def test_ac2_tls_enabled_valid_certs_redirect_and_https():
    with TemporaryDirectory() as tmpdir:
        # Create dummy cert and key for test
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", f"{tmpdir}/server.key",
            "-out", f"{tmpdir}/server.crt", "-sha256", "-days", "1", "-nodes",
            "-subj", "/CN=telemetry-docs.test"
        ], check=True)
        env = {
            "TELEMETRY_DOCS_TLS_ENABLED": "true",
            "TELEMETRY_DOCS_TLS_CERT_PATH": "/certs/server.crt",
            "TELEMETRY_DOCS_TLS_KEY_PATH": "/certs/server.key"
        }
        # Mount certs into container
        run_nginx_with_env({**env, "TELEMETRY_DOCS_MTLS_ENABLED": "false"})
        # Check port 80 redirects to HTTPS
        resp = requests.get("http://localhost:8080/index.html", allow_redirects=False, timeout=5)
        assert resp.status_code == 301
        assert resp.headers["Location"].startswith("https://")
        # Check port 443 serves valid TLS
        resp = requests.get("https://localhost:8443/index.html", verify=f"{tmpdir}/server.crt", timeout=5)
        assert resp.status_code == 200
        # Check cert matches configured one
        conn = ssl.create_connection(("localhost", 8443))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(f"{tmpdir}/server.crt")
        with context.wrap_socket(conn, server_hostname="telemetry-docs.test") as ssock:
            cert = ssock.getpeercert()
            assert cert["subjectAltName"][0][1] == "telemetry-docs.test" or cert["subject"][-1][0][1] == "telemetry-docs.test"

# Test AC3: TLS enabled but missing cert path
def test_ac3_tls_enabled_missing_cert_fails_start():
    env = {
        "TELEMETRY_DOCS_TLS_ENABLED": "true",
        "TELEMETRY_DOCS_TLS_KEY_PATH": "/nonexistent/key.pem"
    }
    stderr = run_nginx_with_env(env, expect_fail=True)
    assert "missing TLS certificate" in stderr.lower() or "ssl_certificate" in stderr.lower()

# Test AC4: mTLS enabled valid CA, client cert validation
def test_ac4_mtls_enabled_client_cert_validation():
    with TemporaryDirectory() as tmpdir:
        # Generate CA, server cert, valid client cert, invalid client cert
        # CA
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", f"{tmpdir}/ca.key",
            "-out", f"{tmpdir}/ca.crt", "-sha256", "-days", "1", "-nodes", "-subj", "/CN=test-ca"
        ], check=True)
        # Server cert
        subprocess.run([
            "openssl", "req", "-newkey", "rsa:4096", "-keyout", f"{tmpdir}/server.key",
            "-out", f"{tmpdir}/server.csr", "-nodes", "-subj", "/CN=telemetry-docs.test"
        ], check=True)
        subprocess.run([
            "openssl", "x509", "-req", "-in", f"{tmpdir}/server.csr", "-CA", f"{tmpdir}/ca.crt",
            "-CAkey", f"{tmpdir}/ca.key", "-CAcreateserial", "-out", f"{tmpdir}/server.crt",
            "-days", "1", "-sha256"
        ], check=True)
        # Valid client cert
        subprocess.run([
            "openssl", "req", "-newkey", "rsa:4096", "-keyout", f"{tmpdir}/valid-client.key",
            "-out", f"{tmpdir}/valid-client.csr", "-nodes", "-subj", "/CN=valid-client"
        ], check=True)
        subprocess.run([
            "openssl", "x509", "-req", "-in", f"{tmpdir}/valid-client.csr", "-CA", f"{tmpdir}/ca.crt",
            "-CAkey", f"{tmpdir}/ca.key", "-CAcreateserial", "-out", f"{tmpdir}/valid-client.crt",
            "-days", "1", "-sha256"
        ], check=True)
        # Invalid client cert (signed by other CA)
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", f"{tmpdir}/other-ca.key",
            "-out", f"{tmpdir}/other-ca.crt", "-sha256", "-days", "1", "-nodes", "-subj", "/CN=other-ca"
        ], check=True)
        subprocess.run([
            "openssl", "req", "-newkey", "rsa:4096", "-keyout", f"{tmpdir}/invalid-client.key",
            "-out", f"{tmpdir}/invalid-client.csr", "-nodes", "-subj", "/CN=invalid-client"
        ], check=True)
        subprocess.run([
            "openssl", "x509", "-req", "-in", f"{tmpdir}/invalid-client.csr", "-CA", f"{tmpdir}/other-ca.crt",
            "-CAkey", f"{tmpdir}/other-ca.key", "-CAcreateserial", "-out", f"{tmpdir}/invalid-client.crt",
            "-days", "1", "-sha256"
        ], check=True)

        env = {
            "TELEMETRY_DOCS_TLS_ENABLED": "true",
            "TELEMETRY_DOCS_TLS_CERT_PATH": "/certs/server.crt",
            "TELEMETRY_DOCS_TLS_KEY_PATH": "/certs/server.key",
            "TELEMETRY_DOCS_MTLS_ENABLED": "true",
            "TELEMETRY_DOCS_MTLS_CA_CERT_PATH": "/certs/ca.crt"
        }
        run_nginx_with_env(env)

        # Test no client cert: 400 error
        resp = requests.get("https://localhost:8443/index.html", verify=f"{tmpdir}/ca.crt", allow_redirects=False, timeout=5)
        assert resp.status_code == 400

        # Test invalid client cert: 403 error
        resp = requests.get(
            "https://localhost:8443/index.html",
            verify=f"{tmpdir}/ca.crt",
            cert=(f"{tmpdir}/invalid-client.crt", f"{tmpdir}/invalid-client.key"),
            allow_redirects=False,
            timeout=5
        )
        assert resp.status_code == 403

        # Test valid client cert: 200 OK
        resp = requests.get(
            "https://localhost:8443/index.html",
            verify=f"{tmpdir}/ca.crt",
            cert=(f"{tmpdir}/valid-client.crt", f"{tmpdir}/valid-client.key"),
            timeout=5
        )
        assert resp.status_code == 200

# Test AC5: mTLS enabled but TLS disabled fails start
def test_ac5_mtls_enabled_tls_disabled_fails_start():
    env = {
        "TELEMETRY_DOCS_TLS_ENABLED": "false",
        "TELEMETRY_DOCS_MTLS_ENABLED": "true",
        "TELEMETRY_DOCS_MTLS_CA_CERT_PATH": "/some/path/ca.crt"
    }
    stderr = run_nginx_with_env(env, expect_fail=True)
    assert "mtls requires tls to be enabled" in stderr.lower()

# Test AC6: mTLS enabled but missing CA cert path fails start
def test_ac6_mtls_enabled_missing_ca_cert_fails_start():
    env = {
        "TELEMETRY_DOCS_TLS_ENABLED": "true",
        "TELEMETRY_DOCS_TLS_CERT_PATH": "/certs/server.crt",
        "TELEMETRY_DOCS_TLS_KEY_PATH": "/certs/server.key",
        "TELEMETRY_DOCS_MTLS_ENABLED": "true"
    }
    stderr = run_nginx_with_env(env, expect_fail=True)
    assert "missing client ca certificate" in stderr.lower()

# Test AC7: Existing plaintext deployments work without changes
def test_ac7_backward_compatibility_no_env_changes():
    # No env vars set at all, same as existing deployments
    run_nginx_with_env({})
    resp = requests.get("http://localhost:8080/index.html", timeout=5)
    assert resp.status_code == 200
    # No TLS listener active
    with pytest.raises((ConnectionRefusedError, socket.timeout)):
        ssl.wrap_socket(socket.create_connection(("localhost", 8443), timeout=5))
