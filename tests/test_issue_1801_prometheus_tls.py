#!/usr/bin/env python3
import pytest
import subprocess
import time
import os
import tempfile
import requests
from pathlib import Path

PROMETHEUS_IMAGE_NAME = "otel/prometheus:dev"
TEST_CERT_DIR = os.path.join(os.path.dirname(__file__), "test_certs_prometheus")
# Dummy test cert/key/ca values (invalid for actual use, valid for file existence checks)
DUMMY_CERT = "-----BEGIN CERTIFICATE-----\nMIIC5zCCAc+gAwIBAgIUZ7X7m5z3x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END CERTIFICATE-----"
DUMMY_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDl1M0m8z7x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END PRIVATE KEY-----"
DUMMY_CA = "-----BEGIN CERTIFICATE-----\nMIIC4DCCAcigAwIBAgIUa6s5d4f3e2d1c0b9a8z7y6x5w4v3u2t1s0r9q8p7o6n5m4l3k2j1i0h9g8f7e6\n-----END CERTIFICATE-----"

def create_test_certs():
    """Create temporary test certificate files"""
    Path(TEST_CERT_DIR).mkdir(exist_ok=True, mode=0o755)
    for fname, content in [
        ("server.crt", DUMMY_CERT),
        ("server.key", DUMMY_KEY),
        ("ca.crt", DUMMY_CA),
        ("client.crt", DUMMY_CERT),
        ("client.key", DUMMY_KEY),
        ("alertmanager_ca.crt", DUMMY_CA),
        ("alertmanager_client.crt", DUMMY_CERT),
        ("alertmanager_client.key", DUMMY_KEY),
        ("remote_write_ca.crt", DUMMY_CA),
        ("remote_write_client.crt", DUMMY_CERT),
        ("remote_write_client.key", DUMMY_KEY),
    ]:
        with open(os.path.join(TEST_CERT_DIR, fname), "w") as f:
            f.write(content)
    for keyfile in ["server.key", "client.key", "alertmanager_client.key", "remote_write_client.key"]:
        os.chmod(os.path.join(TEST_CERT_DIR, keyfile), 0o600)

def run_prometheus_container(env_vars, volumes=None, wait_for_exit=True, timeout=30):
    """Helper to run prometheus container with given env vars and volumes"""
    cmd = ["docker", "run", "--rm"]
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

def test_ac1_prometheus_https_endpoint_with_cert_key():
    """AC-1: When PROMETHEUS_TLS_CERT_PATH and PROMETHEUS_TLS_KEY_PATH are set to valid readable certificate/key files, Prometheus HTTP API endpoint listens on HTTPS instead of HTTP and presents the configured certificate to clients."""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/server.key"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Test that HTTPS works (not HTTP)
        resp = requests.get("https://localhost:9090/-/healthy", verify=False, timeout=5)
        assert resp.status_code == 200
        # Test HTTP fails
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("http://localhost:9090/-/healthy", timeout=5)
    finally:
        proc.kill()
        proc.communicate()

def test_ac2_mtls_client_validation_with_ca():
    """AC-2: When PROMETHEUS_TLS_CLIENT_CA_PATH is set to a valid CA certificate file, only clients presenting a valid certificate signed by this CA can access the Prometheus HTTP API endpoint; requests without valid client certificates are rejected with 401 Unauthorized."""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/server.key",
        "PROMETHEUS_TLS_CLIENT_CA_PATH": "/certs/ca.crt"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Request without client cert should fail
        with pytest.raises(requests.exceptions.SSLError):
            requests.get("https://localhost:9090/-/healthy", verify=False, timeout=5)
        # Request with valid client cert should work
        resp = requests.get(
            "https://localhost:9090/-/healthy",
            verify=False,
            cert=(os.path.join(TEST_CERT_DIR, "client.crt"), os.path.join(TEST_CERT_DIR, "client.key")),
            timeout=5
        )
        assert resp.status_code == 200
    finally:
        proc.kill()
        proc.communicate()

def test_ac3_cert_without_key_fails_startup():
    """AC-3: When PROMETHEUS_TLS_CERT_PATH is set but PROMETHEUS_TLS_KEY_PATH is not set, the entrypoint script exits with code 1 and logs an appropriate error message."""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/server.crt"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=True)
    assert proc.returncode == 1
    assert "PROMETHEUS_TLS_KEY_PATH is required when PROMETHEUS_TLS_CERT_PATH is set" in stderr or "PROMETHEUS_TLS_KEY_PATH is required when PROMETHEUS_TLS_CERT_PATH is set" in stdout

def test_ac4_alertmanager_tls_ca_validation():
    """AC-4: When ALERTMANAGER_TLS_CA_PATH is set to a valid CA certificate file, Prometheus connects to Alertmanager over HTTPS and validates the Alertmanager server certificate against the provided CA."""
    env = {
        "ALERTMANAGER_TLS_CA_PATH": "/certs/alertmanager_ca.crt"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Check that generated prometheus.yml has tls_config for alertmanager with ca_file
        exec_cmd = ["docker", "exec", proc.pid, "cat", "/etc/prometheus/prometheus.yml"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=10)
        assert "alertmanager_config:" in res.stdout
        assert "tls_config:" in res.stdout
        assert "ca_file: /certs/alertmanager_ca.crt" in res.stdout
    finally:
        proc.kill()
        proc.communicate()

def test_ac5_alertmanager_mtls_client_certs():
    """AC-5: When ALERTMANAGER_TLS_CERT_PATH and ALERTMANAGER_TLS_KEY_PATH are set to valid client certificate/key files, Prometheus presents these client certificates when connecting to Alertmanager for mTLS authentication."""
    env = {
        "ALERTMANAGER_TLS_CERT_PATH": "/certs/alertmanager_client.crt",
        "ALERTMANAGER_TLS_KEY_PATH": "/certs/alertmanager_client.key"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Check that generated prometheus.yml has tls_config for alertmanager with cert and key files
        exec_cmd = ["docker", "exec", proc.pid, "cat", "/etc/prometheus/prometheus.yml"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=10)
        assert "alertmanager_config:" in res.stdout
        assert "tls_config:" in res.stdout
        assert "cert_file: /certs/alertmanager_client.crt" in res.stdout
        assert "key_file: /certs/alertmanager_client.key" in res.stdout
    finally:
        proc.kill()
        proc.communicate()

def test_ac6_remote_write_tls_ca_validation():
    """AC-6: When REMOTE_WRITE_TLS_CA_PATH is set to a valid CA certificate file, Prometheus connects to remote write endpoints over HTTPS and validates the remote write server certificate against the provided CA."""
    env = {
        "REMOTE_WRITE_TLS_CA_PATH": "/certs/remote_write_ca.crt"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Check that generated prometheus.yml has tls_config for remote_write with ca_file
        exec_cmd = ["docker", "exec", proc.pid, "cat", "/etc/prometheus/prometheus.yml"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=10)
        assert "remote_write:" in res.stdout
        assert "tls_config:" in res.stdout
        assert "ca_file: /certs/remote_write_ca.crt" in res.stdout
    finally:
        proc.kill()
        proc.communicate()

def test_ac7_remote_write_mtls_client_certs():
    """AC-7: When REMOTE_WRITE_TLS_CERT_PATH and REMOTE_WRITE_TLS_KEY_PATH are set to valid client certificate/key files, Prometheus presents these client certificates when connecting to remote write endpoints for mTLS authentication."""
    env = {
        "REMOTE_WRITE_TLS_CERT_PATH": "/certs/remote_write_client.crt",
        "REMOTE_WRITE_TLS_KEY_PATH": "/certs/remote_write_client.key"
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Check that generated prometheus.yml has tls_config for remote_write with cert and key files
        exec_cmd = ["docker", "exec", proc.pid, "cat", "/etc/prometheus/prometheus.yml"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=10)
        assert "remote_write:" in res.stdout
        assert "tls_config:" in res.stdout
        assert "cert_file: /certs/remote_write_client.crt" in res.stdout
        assert "key_file: /certs/remote_write_client.key" in res.stdout
    finally:
        proc.kill()
        proc.communicate()

def test_ac8_tls_settings_templated_correctly():
    """AC-8: All configured TLS settings are correctly templated into the generated prometheus.yml config file at container startup, following Prometheus official TLS configuration syntax."""
    env = {
        "PROMETHEUS_TLS_CERT_PATH": "/certs/server.crt",
        "PROMETHEUS_TLS_KEY_PATH": "/certs/server.key",
        "PROMETHEUS_TLS_CLIENT_CA_PATH": "/certs/ca.crt",
        "ALERTMANAGER_TLS_CA_PATH": "/certs/alertmanager_ca.crt",
        "ALERTMANAGER_TLS_CERT_PATH": "/certs/alertmanager_client.crt",
        "ALERTMANAGER_TLS_KEY_PATH": "/certs/alertmanager_client.key",
        "REMOTE_WRITE_TLS_CA_PATH": "/certs/remote_write_ca.crt",
        "REMOTE_WRITE_TLS_CERT_PATH": "/certs/remote_write_client.crt",
        "REMOTE_WRITE_TLS_KEY_PATH": "/certs/remote_write_client.key",
    }
    volumes = [f"{TEST_CERT_DIR}:/certs:ro"]
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        exec_cmd = ["docker", "exec", proc.pid, "cat", "/etc/prometheus/prometheus.yml"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=10)
        # Check server TLS config for web endpoint
        assert "tls_server_config:" in res.stdout
        assert "cert_file: /certs/server.crt" in res.stdout
        assert "key_file: /certs/server.key" in res.stdout
        assert "client_ca_file: /certs/ca.crt" in res.stdout
        assert "client_auth_type: RequireAndVerifyClientCert" in res.stdout
        # Check Alertmanager TLS config
        assert "alertmanager_config:" in res.stdout
        assert "tls_config:" in res.stdout
        assert "ca_file: /certs/alertmanager_ca.crt" in res.stdout
        assert "cert_file: /certs/alertmanager_client.crt" in res.stdout
        assert "key_file: /certs/alertmanager_client.key" in res.stdout
        # Check remote write TLS config
        assert "remote_write:" in res.stdout
        assert "tls_config:" in res.stdout
        assert "ca_file: /certs/remote_write_ca.crt" in res.stdout
        assert "cert_file: /certs/remote_write_client.crt" in res.stdout
        assert "key_file: /certs/remote_write_client.key" in res.stdout
    finally:
        proc.kill()
        proc.communicate()

def test_ac9_no_tls_vars_uses_http_backwards_compatibility():
    """AC-9: When no TLS environment variables are set, Prometheus continues to operate over unencrypted HTTP with no authentication, maintaining backwards compatibility with existing deployments."""
    env = {}
    volumes = []
    proc, stdout, stderr = run_prometheus_container(env, volumes, wait_for_exit=False)
    try:
        # Test HTTP works
        resp = requests.get("http://localhost:9090/-/healthy", timeout=5)
        assert resp.status_code == 200
        # Test HTTPS fails
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get("https://localhost:9090/-/healthy", verify=False, timeout=5)
        # Check config has no TLS settings
        exec_cmd = ["docker", "exec", proc.pid, "cat", "/etc/prometheus/prometheus.yml"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=10)
        assert "tls_server_config:" not in res.stdout
        assert "tls_config:" not in res.stdout
    finally:
        proc.kill()
        proc.communicate()
