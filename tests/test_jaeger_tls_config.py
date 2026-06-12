import yaml
import subprocess
import os
import pytest
import requests
import grpc
from grpc import ssl_channel_credentials

JAEGER_DEPLOYMENT_PATH = "k8s/jaeger-deployment.yaml"
JAEGER_VALUES_PATH = "k8s/values.yaml"
TEST_CERT_PATH = "/tmp/test-tls.crt"
TEST_KEY_PATH = "/tmp/test-tls.key"
TEST_CA_PATH = "/tmp/test-ca.crt"

@pytest.fixture
def values_manifest():
    with open(JAEGER_VALUES_PATH, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture
def deployment_manifest():
    with open(JAEGER_DEPLOYMENT_PATH, "r") as f:
        return list(yaml.safe_load_all(f))[0]

@pytest.fixture
def jaeger_container(deployment_manifest):
    containers = deployment_manifest["spec"]["template"]["spec"]["containers"]
    for c in containers:
        if c["name"] == "jaeger":
            return c
    pytest.fail("Jaeger container not found in deployment manifest")

def test_ac1_tls_disabled_accepts_unencrypted():
    # AC-1: JAEGER_TLS_ENABLED=false/unset, all endpoints accept unencrypted connections, service starts
    env = os.environ.copy()
    env.pop("JAEGER_TLS_ENABLED", None)
    env.pop("JAEGER_TLS_CERT_PATH", None)
    env.pop("JAEGER_TLS_KEY_PATH", None)
    env.pop("JAEGER_TLS_CA_CERT_PATH", None)
    
    # Test service starts successfully with no TLS config
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", "JAEGER_TLS_ENABLED=false", "jaegertracing/all-in-one:latest", "version"],
        capture_output=True,
        text=True,
        env=env
    )
    assert result.returncode == 0, "Service failed to start with TLS disabled"
    
    # Test unencrypted connections work to all endpoints
    endpoints = [
        ("http", "localhost", 4318, "/health"),
        ("http", "localhost", 16686, "/"),
    ]
    for scheme, host, port, path in endpoints:
        try:
            resp = requests.get(f"{scheme}://{host}:{port}{path}", timeout=2)
            assert resp.status_code in [200, 404], f"Unencrypted connection to {port} failed"
        except requests.exceptions.ConnectionError:
            pytest.fail(f"Unencrypted connection to port {port} rejected when TLS should be disabled")

def test_ac2_tls_enabled_rejects_unencrypted():
    # AC-2: JAEGER_TLS_ENABLED=true with valid certs, endpoints only accept TLS 1.2+ connections
    env = os.environ.copy()
    env["JAEGER_TLS_ENABLED"] = "true"
    env["JAEGER_TLS_CERT_PATH"] = TEST_CERT_PATH
    env["JAEGER_TLS_KEY_PATH"] = TEST_KEY_PATH
    
    # Test unencrypted connections are rejected
    endpoints = [
        ("http", "localhost", 4318, "/health"),
        ("http", "localhost", 16686, "/"),
    ]
    for scheme, host, port, path in endpoints:
        try:
            requests.get(f"{scheme}://{host}:{port}{path}", timeout=2, verify=False)
            pytest.fail(f"Unencrypted connection to port {port} succeeded when TLS should be enabled")
        except requests.exceptions.ConnectionError:
            pass  # Expected
    
    # Test TLS 1.2+ connections work
    for scheme, host, port, path in endpoints:
        try:
            resp = requests.get(f"https://{host}:{port}{path}", timeout=2, verify=TEST_CA_PATH, tls_version=requests.adapters.DEFAULT_TLS_VERSION)
            assert resp.status_code in [200, 404], f"TLS connection to {port} failed"
        except requests.exceptions.ConnectionError:
            pytest.fail(f"TLS connection to port {port} rejected when TLS should be enabled")

def test_ac3_tls_enabled_missing_cert_path():
    # AC-3: JAEGER_TLS_ENABLED=true without JAEGER_TLS_CERT_PATH, exit non-zero with correct error
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", "JAEGER_TLS_ENABLED=true", "-e", "JAEGER_TLS_KEY_PATH=/tmp/key.pem", "jaegertracing/all-in-one:latest"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "Service started successfully with missing TLS cert path"
    assert "TLS enabled but required configuration missing: JAEGER_TLS_CERT_PATH and JAEGER_TLS_KEY_PATH must be provided" in result.stderr, "Incorrect error message for missing cert path"

def test_ac4_tls_enabled_missing_key_path():
    # AC-4: JAEGER_TLS_ENABLED=true without JAEGER_TLS_KEY_PATH, exit non-zero with correct error
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", "JAEGER_TLS_ENABLED=true", "-e", "JAEGER_TLS_CERT_PATH=/tmp/cert.pem", "jaegertracing/all-in-one:latest"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "Service started successfully with missing TLS key path"
    assert "TLS enabled but required configuration missing: JAEGER_TLS_CERT_PATH and JAEGER_TLS_KEY_PATH must be provided" in result.stderr, "Incorrect error message for missing key path"

def test_ac5_tls_enabled_invalid_cert_path():
    # AC-5: JAEGER_TLS_ENABLED=true with non-existent cert path, exit non-zero with correct error
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", "JAEGER_TLS_ENABLED=true", "-e", "JAEGER_TLS_CERT_PATH=/nonexistent/cert.pem", "-e", "JAEGER_TLS_KEY_PATH=/tmp/key.pem", "jaegertracing/all-in-one:latest"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "Service started successfully with invalid cert path"
    assert "Failed to read TLS certificate/key file: /nonexistent/cert.pem" in result.stderr, "Incorrect error message for invalid cert path"

def test_ac6_tls_enabled_invalid_key_path():
    # AC-6: JAEGER_TLS_ENABLED=true with non-existent key path, exit non-zero with correct error
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", "JAEGER_TLS_ENABLED=true", "-e", "JAEGER_TLS_CERT_PATH=/tmp/cert.pem", "-e", "JAEGER_TLS_KEY_PATH=/nonexistent/key.pem", "jaegertracing/all-in-one:latest"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "Service started successfully with invalid key path"
    assert "Failed to read TLS certificate/key file: /nonexistent/key.pem" in result.stderr, "Incorrect error message for invalid key path"

def test_ac7_mtls_enabled_requires_client_cert():
    # AC-7: JAEGER_TLS_CA_CERT_PATH provided, endpoints require valid client certificate
    env = os.environ.copy()
    env["JAEGER_TLS_ENABLED"] = "true"
    env["JAEGER_TLS_CERT_PATH"] = TEST_CERT_PATH
    env["JAEGER_TLS_KEY_PATH"] = TEST_KEY_PATH
    env["JAEGER_TLS_CA_CERT_PATH"] = TEST_CA_PATH
    
    # Test connection without client cert is rejected
    try:
        requests.get("https://localhost:16686/", timeout=2, verify=TEST_CA_PATH)
        pytest.fail("Connection succeeded without client cert when mTLS should be enabled")
    except requests.exceptions.SSLError:
        pass  # Expected
    
    # Test connection with valid client cert works
    try:
        resp = requests.get("https://localhost:16686/", timeout=2, verify=TEST_CA_PATH, cert=(TEST_CERT_PATH, TEST_KEY_PATH))
        assert resp.status_code in [200, 404], "Connection with valid client cert failed"
    except requests.exceptions.SSLError:
        pytest.fail("Connection with valid client cert rejected when mTLS should be enabled")

def test_ac8_k8s_tls_enabled_mounts_secret_and_env(jaeger_container, deployment_manifest, values_manifest):
    # AC-8: jaeger.tls.enabled=true in values, secret mounted, env vars set, service starts
    assert "jaeger" in values_manifest, "jaeger section missing from values.yaml"
    assert "tls" in values_manifest["jaeger"], "jaeger.tls section missing from values.yaml"
    assert "enabled" in values_manifest["jaeger"]["tls"], "jaeger.tls.enabled missing from values.yaml"
    assert "certSecretName" in values_manifest["jaeger"]["tls"], "jaeger.tls.certSecretName missing from values.yaml"
    assert "enableClientAuth" in values_manifest["jaeger"]["tls"], "jaeger.tls.enableClientAuth missing from values.yaml"
    
    # Check TLS volume exists when enabled
    volumes = deployment_manifest["spec"]["template"]["spec"]["volumes"]
    tls_volume = None
    for vol in volumes:
        if vol.get("name") == "jaeger-tls":
            tls_volume = vol
            break
    assert tls_volume is not None, "TLS volume not found in deployment when TLS is enabled"
    assert tls_volume["secret"]["secretName"] == "{{ .Values.jaeger.tls.certSecretName }}", "TLS volume secret name not templated correctly"
    
    # Check volume mount exists
    assert "volumeMounts" in jaeger_container, "volumeMounts missing from Jaeger container"
    tls_mount = None
    for mount in jaeger_container["volumeMounts"]:
        if mount.get("mountPath") == "/etc/jaeger/tls/":
            tls_mount = mount
            break
    assert tls_mount is not None, "TLS volume not mounted to /etc/jaeger/tls/"
    assert tls_mount["name"] == "jaeger-tls", "TLS mount name does not match volume name"
    
    # Check environment variables are set
    env_vars = {e["name"]: e["value"] for e in jaeger_container.get("env", [])}
    assert "JAEGER_TLS_ENABLED" in env_vars, "JAEGER_TLS_ENABLED env var missing"
    assert env_vars["JAEGER_TLS_ENABLED"] == "{{ .Values.jaeger.tls.enabled | quote }}", "JAEGER_TLS_ENABLED not templated correctly"
    assert "JAEGER_TLS_CERT_PATH" in env_vars, "JAEGER_TLS_CERT_PATH env var missing"
    assert env_vars["JAEGER_TLS_CERT_PATH"] == "/etc/jaeger/tls/tls.crt", "JAEGER_TLS_CERT_PATH not set correctly"
    assert "JAEGER_TLS_KEY_PATH" in env_vars, "JAEGER_TLS_KEY_PATH env var missing"
    assert env_vars["JAEGER_TLS_KEY_PATH"] == "/etc/jaeger/tls/tls.key", "JAEGER_TLS_KEY_PATH not set correctly"

def test_ac9_k8s_tls_disabled_no_mounts_or_env(jaeger_container, deployment_manifest):
    # AC-9: jaeger.tls.enabled=false, no TLS volumes/mounts/env vars, backward compatible
    # Check no TLS volume when disabled
    volumes = deployment_manifest["spec"]["template"]["spec"]["volumes"]
    tls_volume = None
    for vol in volumes:
        if vol.get("name") == "jaeger-tls":
            tls_volume = vol
            break
    assert tls_volume is None, "TLS volume present in deployment when TLS is disabled"
    
    # Check no TLS volume mount
    if "volumeMounts" in jaeger_container:
        for mount in jaeger_container["volumeMounts"]:
            assert mount.get("mountPath") != "/etc/jaeger/tls/", "TLS volume mount present when TLS is disabled"
    
    # Check no TLS environment variables
    env_vars = [e["name"] for e in jaeger_container.get("env", [])]
    assert "JAEGER_TLS_ENABLED" not in env_vars, "JAEGER_TLS_ENABLED env var present when TLS is disabled"
    assert "JAEGER_TLS_CERT_PATH" not in env_vars, "JAEGER_TLS_CERT_PATH env var present when TLS is disabled"
    assert "JAEGER_TLS_KEY_PATH" not in env_vars, "JAEGER_TLS_KEY_PATH env var present when TLS is disabled"
    assert "JAEGER_TLS_CA_CERT_PATH" not in env_vars, "JAEGER_TLS_CA_CERT_PATH env var present when TLS is disabled"
