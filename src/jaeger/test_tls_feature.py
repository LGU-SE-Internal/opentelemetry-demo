#!/usr/bin/env python3
import os
import subprocess
import tempfile
import pytest
import requests
from pathlib import Path

# Test constants
JAEGER_QUERY_PORT = 16686
BASE_URL = f"http://localhost:{JAEGER_QUERY_PORT}"
BASE_URL_HTTPS = f"https://localhost:{JAEGER_QUERY_PORT}"
ENTRYPOINT_SCRIPT = Path(__file__).parent / "entrypoint.sh"

# Helper to generate dummy cert/key files for testing
def generate_dummy_cert_files(tmp_path):
    # Generate self-signed cert for server
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:4096",
            "-keyout", str(tmp_path / "server.key"),
            "-out", str(tmp_path / "server.crt"),
            "-days", "365", "-nodes",
            "-subj", "/CN=localhost"
        ],
        check=True,
        capture_output=True
    )
    # Generate client cert signed by same CA for mTLS tests
    subprocess.run(
        [
            "openssl", "req", "-newkey", "rsa:4096",
            "-keyout", str(tmp_path / "client.key"),
            "-out", str(tmp_path / "client.csr"),
            "-nodes", "-subj", "/CN=test-client"
        ],
        check=True,
        capture_output=True
    )
    subprocess.run(
        [
            "openssl", "x509", "-req", "-in", str(tmp_path / "client.csr"),
            "-CA", str(tmp_path / "server.crt"),
            "-CAkey", str(tmp_path / "server.key"),
            "-CAcreateserial",
            "-out", str(tmp_path / "client.crt"),
            "-days", "365"
        ],
        check=True,
        capture_output=True
    )
    # Generate invalid/expired client cert
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:4096",
            "-keyout", str(tmp_path / "invalid_client.key"),
            "-out", str(tmp_path / "invalid_client.crt"),
            "-days", "-1", "-nodes",
            "-subj", "/CN=invalid-client"
        ],
        check=True,
        capture_output=True
    )
    return {
        "server_cert": str(tmp_path / "server.crt"),
        "server_key": str(tmp_path / "server.key"),
        "client_cert": str(tmp_path / "client.crt"),
        "client_key": str(tmp_path / "client.key"),
        "invalid_client_cert": str(tmp_path / "invalid_client.crt"),
        "invalid_client_key": str(tmp_path / "invalid_client.key"),
    }

@pytest.fixture
def temp_certs(tmp_path):
    return generate_dummy_cert_files(tmp_path)

def test_ac1_no_tls_vars_starts_unencrypted():
    """AC-1: No TLS env vars, starts unencrypted HTTP on 16686"""
    env = os.environ.copy()
    # Remove any existing TLS vars
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    
    # Start service
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        # Wait for service to come up
        import time
        time.sleep(5)
        # Test unencrypted connection works
        resp = requests.get(BASE_URL + "/health", timeout=5)
        assert resp.status_code == 200
        # Test HTTPS connection fails
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(BASE_URL_HTTPS + "/health", verify=False, timeout=5)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

def test_ac2_tls_no_mtls_starts_https_no_client_cert(temp_certs):
    """AC-2: TLS cert/key set, no mTLS, HTTPS works no client cert required"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    env["JAEGER_QUERY_TLS_CERT_PATH"] = temp_certs["server_cert"]
    env["JAEGER_QUERY_TLS_KEY_PATH"] = temp_certs["server_key"]
    env["JAEGER_QUERY_TLS_CLIENT_AUTH"] = "none"
    
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        import time
        time.sleep(5)
        # Test HTTPS works without client cert
        resp = requests.get(BASE_URL_HTTPS + "/health", verify=temp_certs["server_cert"], timeout=5)
        assert resp.status_code == 200
        # Test HTTP connection fails
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get(BASE_URL + "/health", timeout=5)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

def test_ac3_mtls_enabled_requires_valid_client_cert(temp_certs):
    """AC-3: mTLS enabled, only accepts valid client certs signed by CA"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    env["JAEGER_QUERY_TLS_CERT_PATH"] = temp_certs["server_cert"]
    env["JAEGER_QUERY_TLS_KEY_PATH"] = temp_certs["server_key"]
    env["JAEGER_QUERY_TLS_CA_PATH"] = temp_certs["server_cert"]
    env["JAEGER_QUERY_TLS_CLIENT_AUTH"] = "required"
    
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        import time
        time.sleep(5)
        # Test connection without client cert fails
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(BASE_URL_HTTPS + "/health", verify=temp_certs["server_cert"], timeout=5)
        # Test connection with valid client cert works
        resp = requests.get(
            BASE_URL_HTTPS + "/health",
            verify=temp_certs["server_cert"],
            cert=(temp_certs["client_cert"], temp_certs["client_key"]),
            timeout=5
        )
        assert resp.status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)

def test_ac4_cert_set_key_missing_fails_start():
    """AC-4: Cert path set but key path missing, fails start with correct error"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    env["JAEGER_QUERY_TLS_CERT_PATH"] = "/tmp/non/existent/cert.crt"
    
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode != 0
    assert "TLS certificate path provided but private key path missing" in stderr

def test_ac5_key_set_cert_missing_fails_start():
    """AC-5: Key path set but cert path missing, fails start with correct error"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    env["JAEGER_QUERY_TLS_KEY_PATH"] = "/tmp/non/existent/key.key"
    
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode != 0
    assert "TLS private key path provided but certificate path missing" in stderr

def test_ac6_mtls_enabled_ca_missing_fails_start():
    """AC-6: mTLS required but CA path missing, fails start with correct error"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    env["JAEGER_QUERY_TLS_CERT_PATH"] = "/tmp/cert.crt"
    env["JAEGER_QUERY_TLS_KEY_PATH"] = "/tmp/key.key"
    env["JAEGER_QUERY_TLS_CLIENT_AUTH"] = "required"
    
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode != 0
    assert "mTLS client auth enabled but CA certificate path missing" in stderr

def test_ac7_invalid_cert_path_fails_start(temp_certs):
    """AC-7: Non-existent cert path provided, fails start with file read error"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    env["JAEGER_QUERY_TLS_CERT_PATH"] = "/tmp/this/file/does/not/exist.crt"
    env["JAEGER_QUERY_TLS_KEY_PATH"] = temp_certs["server_key"]
    
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode != 0
    assert "Failed to read" in stderr
    assert "certificate" in stderr
    assert "/tmp/this/file/does/not/exist.crt" in stderr

def test_ac8_invalid_client_cert_rejected(temp_certs):
    """AC-8: Invalid/expired client cert rejected with TLS handshake failure"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("JAEGER_QUERY_TLS_"):
            del env[k]
    env["JAEGER_QUERY_TLS_CERT_PATH"] = temp_certs["server_cert"]
    env["JAEGER_QUERY_TLS_KEY_PATH"] = temp_certs["server_key"]
    env["JAEGER_QUERY_TLS_CA_PATH"] = temp_certs["server_cert"]
    env["JAEGER_QUERY_TLS_CLIENT_AUTH"] = "required"
    
    proc = subprocess.Popen(
        [str(ENTRYPOINT_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        import time
        time.sleep(5)
        # Test connection with invalid client cert fails
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(
                BASE_URL_HTTPS + "/health",
                verify=temp_certs["server_cert"],
                cert=(temp_certs["invalid_client_cert"], temp_certs["invalid_client_key"]),
                timeout=5
            )
    finally:
        proc.terminate()
        proc.wait(timeout=10)
