#!/usr/bin/env python3
import subprocess
import time
import pytest
import requests
import os
import tempfile
from pathlib import Path

IMAGE_PROVIDER_SERVICE_NAME = "imageprovider"
HTTP_PORT = 8080
HTTPS_PORT = 8443
BASE_HTTP_URL = f"http://localhost:{HTTP_PORT}"
BASE_HTTPS_URL = f"https://localhost:{HTTPS_PORT}"
DOCKER_COMPOSE_CMD = ["docker", "compose"]
TEST_CERTS_DIR = Path(tempfile.gettempdir()) / "image_provider_test_certs"


def run_cmd(cmd, shell=False, check=True):
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=check)


def generate_test_certs():
    """Generate self-signed test certs, server key/cert, CA cert, client key/cert"""
    TEST_CERTS_DIR.mkdir(exist_ok=True, mode=0o755)
    
    # Generate CA cert
    if not (TEST_CERTS_DIR / "ca.key").exists():
        run_cmd([
            "openssl", "req", "-x509", "-sha256", "-newkey", "rsa:4096", "-nodes",
            "-keyout", str(TEST_CERTS_DIR / "ca.key"),
            "-out", str(TEST_CERTS_DIR / "ca.crt"),
            "-days", "365",
            "-subj", "/CN=test-ca"
        ])
    
    # Generate server cert
    if not (TEST_CERTS_DIR / "server.key").exists():
        run_cmd([
            "openssl", "req", "-newkey", "rsa:4096", "-nodes",
            "-keyout", str(TEST_CERTS_DIR / "server.key"),
            "-out", str(TEST_CERTS_DIR / "server.csr"),
            "-subj", "/CN=localhost"
        ])
        run_cmd([
            "openssl", "x509", "-req", "-in", str(TEST_CERTS_DIR / "server.csr"),
            "-CA", str(TEST_CERTS_DIR / "ca.crt"),
            "-CAkey", str(TEST_CERTS_DIR / "ca.key"),
            "-CAcreateserial",
            "-out", str(TEST_CERTS_DIR / "server.crt"),
            "-days", "365",
            "-sha256"
        ])
    
    # Generate valid client cert (signed by CA)
    if not (TEST_CERTS_DIR / "client.valid.key").exists():
        run_cmd([
            "openssl", "req", "-newkey", "rsa:4096", "-nodes",
            "-keyout", str(TEST_CERTS_DIR / "client.valid.key"),
            "-out", str(TEST_CERTS_DIR / "client.valid.csr"),
            "-subj", "/CN=valid-client"
        ])
        run_cmd([
            "openssl", "x509", "-req", "-in", str(TEST_CERTS_DIR / "client.valid.csr"),
            "-CA", str(TEST_CERTS_DIR / "ca.crt"),
            "-CAkey", str(TEST_CERTS_DIR / "ca.key"),
            "-CAcreateserial",
            "-out", str(TEST_CERTS_DIR / "client.valid.crt"),
            "-days", "365",
            "-sha256"
        ])
    
    # Generate invalid client cert (self-signed, not signed by CA)
    if not (TEST_CERTS_DIR / "client.invalid.key").exists():
        run_cmd([
            "openssl", "req", "-x509", "-sha256", "-newkey", "rsa:4096", "-nodes",
            "-keyout", str(TEST_CERTS_DIR / "client.invalid.key"),
            "-out", str(TEST_CERTS_DIR / "client.invalid.crt"),
            "-days", "365",
            "-subj", "/CN=invalid-client"
        ])


def wait_for_service_start(timeout=10, check_https=False):
    """Wait for image provider service to be reachable"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            requests.get(f"{BASE_HTTP_URL}/status", timeout=1, verify=False)
            if check_https:
                requests.get(f"{BASE_HTTPS_URL}/status", timeout=1, verify=False)
            return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            time.sleep(0.5)
    return False


def get_service_exit_code():
    """Get exit code of the image provider service container"""
    result = run_cmd([
        "docker", "inspect", "-f", "{{.State.ExitCode}}",
        IMAGE_PROVIDER_SERVICE_NAME
    ], check=False)
    if result.returncode != 0:
        return None
    return int(result.stdout.strip())


@pytest.fixture(scope="session", autouse=True)
def setup_test_certs():
    generate_test_certs()
    yield
    # Cleanup certs
    for f in TEST_CERTS_DIR.glob("*"):
        f.unlink(missing_ok=True)
    TEST_CERTS_DIR.rmdir()


@pytest.fixture(scope="function")
def image_provider_base():
    """Base fixture to clean up existing instances before each test"""
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", IMAGE_PROVIDER_SERVICE_NAME], check=False)
    # Unset all TLS env vars by default
    for env_var in ["IMAGE_PROVIDER_TLS_CERT_PATH", "IMAGE_PROVIDER_TLS_KEY_PATH", "IMAGE_PROVIDER_TLS_CA_CERT_PATH"]:
        if env_var in os.environ:
            del os.environ[env_var]
    yield
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", IMAGE_PROVIDER_SERVICE_NAME], check=False)


@pytest.mark.ac1
def test_ac1_http_works_when_no_tls_env_vars_set(image_provider_base):
    """AC-1: When no TLS-related environment variables are set, the image-provider service
    successfully starts and responds to HTTP requests on port 8080, returning 200 OK for valid static asset paths"""
    # Start service without any TLS env vars
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", IMAGE_PROVIDER_SERVICE_NAME])
    
    # Verify service starts successfully
    assert wait_for_service_start(timeout=15), "Image provider service failed to start"
    
    # Verify HTTP works on port 8080
    response = requests.get(f"{BASE_HTTP_URL}/health", timeout=2)
    assert response.status_code == 200
    
    # Verify HTTPS is not listening on port 8443
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"{BASE_HTTPS_URL}/health", timeout=2, verify=False)


@pytest.mark.ac2
def test_ac2_https_works_when_tls_cert_and_key_set(image_provider_base):
    """AC-2: When IMAGE_PROVIDER_TLS_CERT_PATH and IMAGE_PROVIDER_TLS_KEY_PATH are set to valid PEM files:
    a. Service successfully starts
    b. Service responds to HTTPS requests on port 8443 with valid TLS certificate matching the provided cert
    c. Service continues to respond to HTTP requests on port 8080"""
    # Set TLS env vars, mount certs directory into container
    os.environ["IMAGE_PROVIDER_TLS_CERT_PATH"] = "/certs/server.crt"
    os.environ["IMAGE_PROVIDER_TLS_KEY_PATH"] = "/certs/server.key"
    
    # Add volume mount for certs
    run_cmd(DOCKER_COMPOSE_CMD + [
        "run", "-d", "--name", IMAGE_PROVIDER_SERVICE_NAME,
        "-p", f"{HTTP_PORT}:{HTTP_PORT}",
        "-p", f"{HTTPS_PORT}:{HTTPS_PORT}",
        "-v", f"{TEST_CERTS_DIR}:/certs:ro",
        "-e", "IMAGE_PROVIDER_TLS_CERT_PATH=/certs/server.crt",
        "-e", "IMAGE_PROVIDER_TLS_KEY_PATH=/certs/server.key",
        IMAGE_PROVIDER_SERVICE_NAME
    ])
    
    # Verify service starts successfully with HTTPS
    assert wait_for_service_start(timeout=15, check_https=True), "Image provider service failed to start with TLS"
    
    # Verify HTTP still works on port 8080
    response = requests.get(f"{BASE_HTTP_URL}/health", timeout=2)
    assert response.status_code == 200
    
    # Verify HTTPS works on port 8443
    response = requests.get(f"{BASE_HTTPS_URL}/health", timeout=2, verify=str(TEST_CERTS_DIR / "ca.crt"))
    assert response.status_code == 200
    assert response.text.strip() == "OK"


@pytest.mark.ac3
def test_ac3_service_fails_when_cert_set_without_key(image_provider_base):
    """AC-3: When IMAGE_PROVIDER_TLS_CERT_PATH is set without IMAGE_PROVIDER_TLS_KEY_PATH,
    the Nginx service fails to start with a configuration error referencing missing TLS private key"""
    # Set only cert path, no key path
    run_cmd(DOCKER_COMPOSE_CMD + [
        "run", "-d", "--name", IMAGE_PROVIDER_SERVICE_NAME,
        "-p", f"{HTTP_PORT}:{HTTP_PORT}",
        "-v", f"{TEST_CERTS_DIR}:/certs:ro",
        "-e", "IMAGE_PROVIDER_TLS_CERT_PATH=/certs/server.crt",
        IMAGE_PROVIDER_SERVICE_NAME
    ])
    
    # Wait for service to fail
    time.sleep(5)
    
    # Verify service exited with non-zero code
    exit_code = get_service_exit_code()
    assert exit_code is not None and exit_code != 0, "Service should have failed to start"
    
    # Check logs for missing key error
    logs = run_cmd(["docker", "logs", IMAGE_PROVIDER_SERVICE_NAME], check=False).stderr
    assert "ssl_certificate_key" in logs or "private key" in logs or "TLS key" in logs, "Error message should reference missing TLS private key"


@pytest.mark.ac4
def test_ac4_mtls_enforced_when_ca_cert_set(image_provider_base):
    """AC-4: When IMAGE_PROVIDER_TLS_CA_CERT_PATH is set alongside valid TLS cert/key:
    a. Requests to HTTPS port 8443 with a valid client certificate signed by the provided CA return 200 OK for valid assets
    b. Requests to HTTPS port 8443 without a client certificate return 400 Bad Request
    c. Requests to HTTPS port 8443 with a client certificate not signed by the provided CA return 400 Bad Request"""
    # Set all TLS env vars including CA cert
    run_cmd(DOCKER_COMPOSE_CMD + [
        "run", "-d", "--name", IMAGE_PROVIDER_SERVICE_NAME,
        "-p", f"{HTTP_PORT}:{HTTP_PORT}",
        "-p", f"{HTTPS_PORT}:{HTTPS_PORT}",
        "-v", f"{TEST_CERTS_DIR}:/certs:ro",
        "-e", "IMAGE_PROVIDER_TLS_CERT_PATH=/certs/server.crt",
        "-e", "IMAGE_PROVIDER_TLS_KEY_PATH=/certs/server.key",
        "-e", "IMAGE_PROVIDER_TLS_CA_CERT_PATH=/certs/ca.crt",
        IMAGE_PROVIDER_SERVICE_NAME
    ])
    
    # Verify service starts
    assert wait_for_service_start(timeout=15, check_https=True), "Image provider service failed to start with mTLS"
    
    # Case a: Valid client cert should return 200
    response = requests.get(
        f"{BASE_HTTPS_URL}/health",
        cert=(str(TEST_CERTS_DIR / "client.valid.crt"), str(TEST_CERTS_DIR / "client.valid.key")),
        verify=str(TEST_CERTS_DIR / "ca.crt"),
        timeout=2
    )
    assert response.status_code == 200
    
    # Case b: No client cert should return 400
    response = requests.get(
        f"{BASE_HTTPS_URL}/health",
        verify=str(TEST_CERTS_DIR / "ca.crt"),
        timeout=2
    )
    assert response.status_code == 400
    
    # Case c: Invalid client cert should return 400
    response = requests.get(
        f"{BASE_HTTPS_URL}/health",
        cert=(str(TEST_CERTS_DIR / "client.invalid.crt"), str(TEST_CERTS_DIR / "client.invalid.key")),
        verify=str(TEST_CERTS_DIR / "ca.crt"),
        timeout=2
    )
    assert response.status_code == 400


@pytest.mark.ac5
def test_ac5_tls_env_vars_documented_in_readme():
    """AC-5: All three TLS environment variables are documented in the image-provider service's README/configuration documentation
    with descriptions, default values, and example usage"""
    readme_path = Path("./src/image-provider/README.md")
    assert readme_path.exists(), "image-provider README.md not found"
    
    readme_content = readme_path.read_text()
    
    # Check all three env vars are present
    assert "IMAGE_PROVIDER_TLS_CERT_PATH" in readme_content, "IMAGE_PROVIDER_TLS_CERT_PATH not documented"
    assert "IMAGE_PROVIDER_TLS_KEY_PATH" in readme_content, "IMAGE_PROVIDER_TLS_KEY_PATH not documented"
    assert "IMAGE_PROVIDER_TLS_CA_CERT_PATH" in readme_content, "IMAGE_PROVIDER_TLS_CA_CERT_PATH not documented"
