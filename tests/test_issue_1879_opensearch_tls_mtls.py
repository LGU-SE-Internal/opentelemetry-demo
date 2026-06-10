#!/usr/bin/env python3
import pytest
import subprocess
import time
import os
import requests
from pathlib import Path

OPENSEARCH_IMAGE_NAME = "otel/opensearch:dev"
TEST_CERT_DIR = os.path.join(os.path.dirname(__file__), "test_certs_opensearch")
# Dummy test cert/key/ca values (invalid for actual use, valid for file existence checks)
DUMMY_CERT = "-----BEGIN CERTIFICATE-----\nMIIC5zCCAc+gAwIBAgIUZ7X7m5z3x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END CERTIFICATE-----"
DUMMY_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDl1M0m8z7x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END PRIVATE KEY-----"
DUMMY_CA = "-----BEGIN CERTIFICATE-----\nMIIC4DCCAcigAwIBAgIUa6s5d4f3e2d1c0b9a8z7y6x5w4v3u2t1s0r9q8p7o6n5m4l3k2j1i0h9g8f7e6\n-----END CERTIFICATE-----"
INVALID_CERT = "-----BEGIN CERTIFICATE-----\nINVALIDINVALIDINVALID\n-----END CERTIFICATE-----"

def create_test_certs():
    """Create temporary test certificate files"""
    Path(TEST_CERT_DIR).mkdir(exist_ok=True, mode=0o755)
    for fname, content in [
        ("tls.crt", DUMMY_CERT),
        ("tls.key", DUMMY_KEY),
        ("ca.crt", DUMMY_CA),
        ("invalid.crt", INVALID_CERT),
        ("invalid.key", INVALID_CERT)
    ]:
        with open(os.path.join(TEST_CERT_DIR, fname), "w") as f:
            f.write(content)
    os.chmod(os.path.join(TEST_CERT_DIR, "tls.key"), 0o600)

def run_opensearch_container(env_vars, volumes=None, wait_for_exit=True, timeout=60):
    """Helper to run opensearch container with given env vars and volumes"""
    cmd = ["docker", "run", "--rm", "-p", "9200:9200", "-p", "9300:9300"]
    if volumes:
        for vol in volumes:
            cmd.extend(["-v", vol])
    for k, v in env_vars.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.extend(["-e", "discovery.type=single-node"])
    cmd.append(OPENSEARCH_IMAGE_NAME)
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not wait_for_exit:
        time.sleep(20)  # Give opensearch time to start
        return proc, None, None
    
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise Exception("Container timed out")

@pytest.fixture(scope="module", autouse=True)
def setup_test_certs():
    create_test_certs()
    yield
    import shutil
    shutil.rmtree(TEST_CERT_DIR, ignore_errors=True)

@pytest.mark.ac1
def test_ac1_http_tls_enabled_only_accepts_https():
    """AC-1: When OPENSEARCH_HTTP_TLS_ENABLED=true, HTTP endpoint only accepts HTTPS connections, HTTP is refused"""
    volumes = [
        f"{TEST_CERT_DIR}:/usr/share/opensearch/config/certs:ro"
    ]
    env = {
        "OPENSEARCH_HTTP_TLS_ENABLED": "true",
        "OPENSEARCH_TLS_CERT_PATH": "/usr/share/opensearch/config/certs/tls.crt",
        "OPENSEARCH_TLS_KEY_PATH": "/usr/share/opensearch/config/certs/tls.key",
        "OPENSEARCH_TLS_CA_PATH": "/usr/share/opensearch/config/certs/ca.crt"
    }
    proc, _, _ = run_opensearch_container(env, volumes=volumes, wait_for_exit=False)
    try:
        # Test unencrypted HTTP connection fails
        with pytest.raises((requests.exceptions.ConnectionError, requests.exceptions.SSLError)):
            requests.get("http://localhost:9200", timeout=5)
        
        # Test HTTPS connection works (ignore cert error for dummy cert)
        response = requests.get("https://localhost:9200", verify=False, timeout=5)
        assert response.status_code in [200, 401]  # 401 if security is enabled, 200 otherwise
    finally:
        proc.terminate()
        proc.wait()

@pytest.mark.ac2
def test_ac2_http_mtls_enabled_rejects_requests_without_client_cert():
    """AC-2: When HTTP TLS and mTLS are enabled, requests without valid client cert return 401"""
    volumes = [
        f"{TEST_CERT_DIR}:/usr/share/opensearch/config/certs:ro"
    ]
    env = {
        "OPENSEARCH_HTTP_TLS_ENABLED": "true",
        "OPENSEARCH_HTTP_MTLS_ENABLED": "true",
        "OPENSEARCH_TLS_CERT_PATH": "/usr/share/opensearch/config/certs/tls.crt",
        "OPENSEARCH_TLS_KEY_PATH": "/usr/share/opensearch/config/certs/tls.key",
        "OPENSEARCH_TLS_CA_PATH": "/usr/share/opensearch/config/certs/ca.crt"
    }
    proc, _, _ = run_opensearch_container(env, volumes=volumes, wait_for_exit=False)
    try:
        # Request without client cert should return 401
        response = requests.get("https://localhost:9200", verify=False, timeout=5)
        assert response.status_code == 401
    finally:
        proc.terminate()
        proc.wait()

@pytest.mark.ac3
def test_ac3_http_mtls_enabled_accepts_requests_with_valid_client_cert():
    """AC-3: When HTTP TLS and mTLS are enabled, requests with valid client cert return 200"""
    volumes = [
        f"{TEST_CERT_DIR}:/usr/share/opensearch/config/certs:ro"
    ]
    env = {
        "OPENSEARCH_HTTP_TLS_ENABLED": "true",
        "OPENSEARCH_HTTP_MTLS_ENABLED": "true",
        "OPENSEARCH_TLS_CERT_PATH": "/usr/share/opensearch/config/certs/tls.crt",
        "OPENSEARCH_TLS_KEY_PATH": "/usr/share/opensearch/config/certs/tls.key",
        "OPENSEARCH_TLS_CA_PATH": "/usr/share/opensearch/config/certs/ca.crt"
    }
    proc, _, _ = run_opensearch_container(env, volumes=volumes, wait_for_exit=False)
    try:
        # Request with valid client cert should return 200
        response = requests.get(
            "https://localhost:9200", 
            verify=False,
            cert=(os.path.join(TEST_CERT_DIR, "tls.crt"), os.path.join(TEST_CERT_DIR, "tls.key")),
            timeout=5
        )
        assert response.status_code == 200
    finally:
        proc.terminate()
        proc.wait()

@pytest.mark.ac4
def test_ac4_transport_tls_enabled_only_accepts_encrypted_connections():
    """AC-4: When transport TLS enabled, inter-node communication on port 9300 uses TLS only"""
    volumes = [
        f"{TEST_CERT_DIR}:/usr/share/opensearch/config/certs:ro"
    ]
    env = {
        "OPENSEARCH_TRANSPORT_TLS_ENABLED": "true",
        "OPENSEARCH_TRANSPORT_MTLS_ENABLED": "true",
        "OPENSEARCH_TLS_CERT_PATH": "/usr/share/opensearch/config/certs/tls.crt",
        "OPENSEARCH_TLS_KEY_PATH": "/usr/share/opensearch/config/certs/tls.key",
        "OPENSEARCH_TLS_CA_PATH": "/usr/share/opensearch/config/certs/ca.crt"
    }
    proc, _, _ = run_opensearch_container(env, volumes=volumes, wait_for_exit=False)
    try:
        # Attempt to establish plain TCP connection (non-TLS) to transport port should fail
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        # If TLS is enforced, plain connection will be reset or timeout
        with pytest.raises((ConnectionResetError, socket.timeout)):
            s.connect(("localhost", 9300))
            s.send(b"GET / HTTP/1.1\r\n\r\n")
            s.recv(1024)
        s.close()
    finally:
        proc.terminate()
        proc.wait()

@pytest.mark.ac5
def test_ac5_liveness_readiness_probes_work_with_tls_enabled():
    """AC-5: Liveness and readiness probes return success when HTTP TLS is enabled"""
    # First check that K8s manifest has correct probe configuration
    proc = subprocess.run(
        ["grep", "-A", "10", "livenessProbe", "kubernetes/opensearch/opensearch-statefulset.yaml"],
        capture_output=True, text=True
    )
    assert "HTTPS" in proc.stdout, "Liveness probe should use HTTPS scheme when TLS enabled"
    assert "port: 9200" in proc.stdout, "Liveness probe should use port 9200"
    
    proc = subprocess.run(
        ["grep", "-A", "10", "readinessProbe", "kubernetes/opensearch/opensearch-statefulset.yaml"],
        capture_output=True, text=True
    )
    assert "HTTPS" in proc.stdout, "Readiness probe should use HTTPS scheme when TLS enabled"
    assert "port: 9200" in proc.stdout, "Readiness probe should use port 9200"

@pytest.mark.ac6
def test_ac6_tls_secret_mounted_to_correct_path():
    """AC-6: Kubernetes secret opensearch-tls is mounted to /usr/share/opensearch/config/certs when TLS enabled"""
    # Check K8s manifest for volume mount
    proc = subprocess.run(
        ["grep", "-A", "5", "mountPath: /usr/share/opensearch/config/certs", "kubernetes/opensearch/opensearch-statefulset.yaml"],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, "Missing volume mount for TLS certificates"
    assert "opensearch-tls" in proc.stdout, "Volume should reference opensearch-tls secret"
    
    # Check volume definition
    proc = subprocess.run(
        ["grep", "-B", "5", "secretName: opensearch-tls", "kubernetes/opensearch/opensearch-statefulset.yaml"],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, "Missing opensearch-tls secret volume definition"

@pytest.mark.ac7
def test_ac7_documentation_exists_for_tls_configuration():
    """AC-7: Documentation exists for TLS/mTLS configuration in Docker and Kubernetes"""
    # Check docs for TLS configuration section
    proc = subprocess.run(
        ["grep", "-i", "opensearch.*tls", "docs/services/opensearch.md"],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, "Missing OpenSearch TLS documentation"
    assert "PEM" in proc.stdout, "Documentation should mention PEM certificate format"
    assert "secret" in proc.stdout, "Documentation should mention Kubernetes secret structure"
    assert all(var in proc.stdout for var in [
        "OPENSEARCH_HTTP_TLS_ENABLED",
        "OPENSEARCH_TRANSPORT_TLS_ENABLED",
        "OPENSEARCH_HTTP_MTLS_ENABLED",
        "OPENSEARCH_TRANSPORT_MTLS_ENABLED"
    ]), "Documentation should list all TLS environment variables"

@pytest.mark.ac8
def test_ac8_tls_disabled_backwards_compatible():
    """AC-8: When OPENSEARCH_HTTP_TLS_ENABLED=false (default), existing HTTP functionality works unchanged"""
    env = {
        "OPENSEARCH_HTTP_TLS_ENABLED": "false",
        "OPENSEARCH_TRANSPORT_TLS_ENABLED": "false"
    }
    proc, _, _ = run_opensearch_container(env, wait_for_exit=False)
    try:
        # Unencrypted HTTP connection should work normally
        response = requests.get("http://localhost:9200", timeout=5)
        assert response.status_code in [200, 401]  # 401 if security enabled, 200 otherwise
    finally:
        proc.terminate()
        proc.wait()
