#!/usr/bin/env python3
import pytest
import subprocess
import time
import os
import requests
from pathlib import Path

PROMETHEUS_IMAGE_NAME = "otel/prometheus:dev"
TEST_CERT_DIR = os.path.join(os.path.dirname(__file__), "test_certs_1979")
# Dummy test cert/key/ca values (invalid for actual crypto, valid for file checks)
DUMMY_VALID_CERT = "-----BEGIN CERTIFICATE-----\nMIIC5zCCAc+gAwIBAgIUZ7X7m5z3x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END CERTIFICATE-----"
DUMMY_VALID_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDl1M0m8z7x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END PRIVATE KEY-----"
DUMMY_UNMATCHED_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDF2N1f5n2y9z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END PRIVATE KEY-----"
DUMMY_CA = "-----BEGIN CERTIFICATE-----\nMIIC4DCCAcigAwIBAgIUa6s5d4f3e2d1c0b9a8z7y6x5w4v3u2t1s0r9q8p7o6n5m4l3k2j1i0h9g8f7e6\n-----END CERTIFICATE-----"
DUMMY_CLIENT_CERT = "-----BEGIN CERTIFICATE-----\nMIIC3zCCAcigAwIBAgIUVb3s2d1f7e5r4q3p2o1n0m9l8k7j6i5h4g3f2e1d0c9b8a7z6y5x4w3v2u1t0\n-----END CERTIFICATE-----"
DUMMY_CLIENT_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCg7H2a9k4b3z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END PRIVATE KEY-----"

def create_test_certs():
    """Create temporary test certificate files"""
    Path(TEST_CERT_DIR).mkdir(exist_ok=True, mode=0o755)
    for fname, content in [
        ("valid_server.crt", DUMMY_VALID_CERT),
        ("valid_server.key", DUMMY_VALID_KEY),
        ("unmatched_server.key", DUMMY_UNMATCHED_KEY),
        ("ca.crt", DUMMY_CA),
        ("client.crt", DUMMY_CLIENT_CERT),
        ("client.key", DUMMY_CLIENT_KEY),
    ]:
        with open(os.path.join(TEST_CERT_DIR, fname), "w") as f:
            f.write(content)
    for keyfile in ["valid_server.key", "unmatched_server.key", "client.key"]:
        os.chmod(os.path.join(TEST_CERT_DIR, keyfile), 0o600)

def run_prometheus_container(env_vars, volumes=None, wait_for_exit=True, timeout=20, network_host=True):
    """Helper to run prometheus container with given env vars and volumes"""
    cmd = ["docker", "run", "--rm"]
    if network_host:
        cmd.extend(["--net=host"])
    if volumes:
        for vol in volumes:
            cmd.extend(["-v", vol])
    for k, v in env_vars.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.append(PROMETHEUS_IMAGE_NAME)
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not wait_for_exit:
        time.sleep(10)
        return proc, None, None
    
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return proc, stdout, stderr

@pytest.fixture(scope="module", autouse=True)
def setup_test_certs():
    create_test_certs()
    yield
    import shutil
    shutil.rmtree(TEST_CERT_DIR)

def test_ac1_no_tls_vars_serves_unencrypted_http():
    """AC-1: When no TLS environment variables are set, Prometheus starts and serves unencrypted HTTP on port 9090"""
    env = {}
    volumes = []
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # HTTP should work
        resp = requests.get("http://localhost:9090/-/healthy", timeout=5)
        assert resp.status_code == 200
        # HTTPS should fail
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("https://localhost:9090/-/healthy", verify=False, timeout=5)
    finally:
        proc.kill()
        proc.communicate()

def test_ac2_only_cert_provided_exits_code_1():
    """AC-2: When only PROMETHEUS_TLS_CERT_PATH is set without key, entrypoint exits with code 1"""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/valid_server.crt"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=True)
    assert proc.returncode == 1
    assert "both certificate and key must be provided" in stderr.lower() or "both certificate and key must be provided" in stdout.lower()

def test_ac3_only_key_provided_exits_code_1():
    """AC-3: When only PROMETHEUS_TLS_KEY_PATH is set without cert, entrypoint exits with code 1"""
    env = {
        "PROMETHEUS_TLS_KEY_PATH": "/certs/valid_server.key"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=True)
    assert proc.returncode == 1
    assert "both certificate and key must be provided" in stderr.lower() or "both certificate and key must be provided" in stdout.lower()

def test_ac4_valid_cert_key_serves_https():
    """AC-4: When valid TLS cert/key paths are provided, Prometheus serves encrypted HTTPS on port 9090"""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/valid_server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/valid_server.key"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # HTTPS should work
        resp = requests.get("https://localhost:9090/-/healthy", verify=os.path.join(TEST_CERT_DIR, "valid_server.crt"), timeout=5)
        assert resp.status_code == 200
        # HTTP should fail
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("http://localhost:9090/-/healthy", timeout=5)
    finally:
        proc.kill()
        proc.communicate()

def test_ac5_cert_file_not_found_exits_code_2():
    """AC-5: When provided TLS cert file does not exist, entrypoint exits with code 2"""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/nonexistent/cert.pem",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/valid_server.key"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=True)
    assert proc.returncode == 2
    assert "certificate file not found" in stderr.lower() or "certificate file not found" in stdout.lower()

def test_ac6_key_file_not_found_exits_code_3():
    """AC-6: When provided TLS key file does not exist, entrypoint exits with code 3"""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/valid_server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/nonexistent/key.pem"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=True)
    assert proc.returncode == 3
    assert "key file not found" in stderr.lower() or "key file not found" in stdout.lower()

def test_ac7_mismatched_cert_key_exits_code_5():
    """AC-7: When provided TLS cert and key are mismatched, entrypoint exits with code 5"""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/valid_server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/unmatched_server.key"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=True)
    assert proc.returncode == 5
    assert "invalid certificate/key pair" in stderr.lower() or "invalid certificate/key pair" in stdout.lower()

def test_ac8_mtls_requires_client_cert():
    """AC-8: When valid mTLS CA path is provided, Prometheus requires valid client certificate"""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/valid_server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/valid_server.key",
        "PROMETHEUS_TLS_CLIENT_CA_PATH": "/certs/ca.crt"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Request without client cert should fail (401 or SSL error)
        with pytest.raises((requests.exceptions.SSLError, requests.exceptions.HTTPError)):
            resp = requests.get("https://localhost:9090/-/healthy", verify=os.path.join(TEST_CERT_DIR, "valid_server.crt"), timeout=5)
            resp.raise_for_status()
        # Request with valid client cert should work
        resp = requests.get(
            "https://localhost:9090/-/healthy",
            verify=os.path.join(TEST_CERT_DIR, "valid_server.crt"),
            cert=(os.path.join(TEST_CERT_DIR, "client.crt"), os.path.join(TEST_CERT_DIR, "client.key")),
            timeout=5
        )
        assert resp.status_code == 200
    finally:
        proc.kill()
        proc.communicate()

def test_ac9_ca_file_not_found_exits_code_4():
    """AC-9: When provided mTLS CA file does not exist, entrypoint exits with code 4"""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/valid_server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/valid_server.key",
        "PROMETHEUS_TLS_CLIENT_CA_PATH": "/nonexistent/ca.pem"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=True)
    assert proc.returncode == 4
    assert "ca file not found" in stderr.lower() or "ca file not found" in stdout.lower()
