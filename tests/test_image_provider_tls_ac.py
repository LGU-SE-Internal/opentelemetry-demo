import os
import pytest
import requests
import subprocess
import time
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

IMAGE_NAME = "otel-demo-image-provider:latest"
CERT_DIR = os.path.join(os.path.dirname(__file__), "test_certs")

def test_ac1_tls_disabled_only_listens_port_80():
    # AC-1: TLS disabled, only port 80 open, no 443 listener
    with DockerContainer(IMAGE_NAME) as container:
        container.with_env("IMAGE_PROVIDER_TLS_ENABLED", "false")
        container.with_exposed_ports(80, 443)
        
        # Wait for container to start
        time.sleep(3)
        
        # Check port 80 is reachable
        port_80 = container.get_exposed_port(80)
        response = requests.get(f"http://localhost:{port_80}/health", timeout=5)
        assert response.status_code == 200
        
        # Check port 443 is not open
        port_443 = container.get_exposed_port(443)
        with pytest.raises((requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            requests.get(f"https://localhost:{port_443}/health", verify=False, timeout=3)

def test_ac2_tls_enabled_valid_certs_listens_443():
    # AC-2: TLS enabled with valid certs, 443 works
    os.makedirs(CERT_DIR, exist_ok=True)
    # Generate test certs first (will fail if not implemented yet)
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", f"{CERT_DIR}/key.pem",
        "-out", f"{CERT_DIR}/cert.pem", "-days", "365", "-nodes", "-subj", "/CN=image-provider.test"
    ], check=True, capture_output=True)
    
    with DockerContainer(IMAGE_NAME) as container:
        container.with_env("IMAGE_PROVIDER_TLS_ENABLED", "true")
        container.with_volume_mapping(CERT_DIR, "/etc/nginx/ssl", "ro")
        container.with_exposed_ports(80, 443)
        
        wait_for_logs(container, "start worker process", timeout=10)
        
        # Check 443 works with TLS
        port_443 = container.get_exposed_port(443)
        response = requests.get(f"https://localhost:{port_443}/health", verify=f"{CERT_DIR}/cert.pem", timeout=5)
        assert response.status_code == 200

def test_ac3_tls_enabled_missing_certs_fails_start():
    # AC-3: TLS enabled but no cert/key, fails to start
    with DockerContainer(IMAGE_NAME) as container:
        container.with_env("IMAGE_PROVIDER_TLS_ENABLED", "true")
        container.with_exposed_ports(443)
        
        # Container should exit quickly
        time.sleep(5)
        exit_code = container.get_wrapped_container().wait()["StatusCode"]
        assert exit_code != 0
        
        # Check logs have error about missing cert/key
        logs = container.get_logs()[0].decode() + container.get_logs()[1].decode()
        assert "cert.pem" in logs or "key.pem" in logs or "certificate" in logs

def test_ac4_mtls_enabled_validates_client_certs():
    # AC-4: mTLS enabled, invalid client cert gets 403, valid gets 200
    os.makedirs(CERT_DIR, exist_ok=True)
    # Generate CA, server cert, client certs
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", f"{CERT_DIR}/ca.key",
        "-out", f"{CERT_DIR}/ca-bundle.pem", "-days", "365", "-nodes", "-subj", "/CN=test-ca.test"
    ], check=True, capture_output=True)
    # Server cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-keyout", f"{CERT_DIR}/key.pem",
        "-out", f"{CERT_DIR}/server.csr", "-nodes", "-subj", "/CN=image-provider.test"
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "x509", "-req", "-in", f"{CERT_DIR}/server.csr", "-CA", f"{CERT_DIR}/ca-bundle.pem",
        "-CAkey", f"{CERT_DIR}/ca.key", "-CAcreateserial", "-out", f"{CERT_DIR}/cert.pem", "-days", "365"
    ], check=True, capture_output=True)
    # Valid client cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-keyout", f"{CERT_DIR}/client.key",
        "-out", f"{CERT_DIR}/client.csr", "-nodes", "-subj", "/CN=valid-client.test"
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "x509", "-req", "-in", f"{CERT_DIR}/client.csr", "-CA", f"{CERT_DIR}/ca-bundle.pem",
        "-CAkey", f"{CERT_DIR}/ca.key", "-CAcreateserial", "-out", f"{CERT_DIR}/client.crt", "-days", "365"
    ], check=True, capture_output=True)
    # Invalid client cert (self-signed)
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", f"{CERT_DIR}/invalid-client.key",
        "-out", f"{CERT_DIR}/invalid-client.crt", "-days", "365", "-nodes", "-subj", "/CN=invalid-client.test"
    ], check=True, capture_output=True)
    
    with DockerContainer(IMAGE_NAME) as container:
        container.with_env("IMAGE_PROVIDER_TLS_ENABLED", "true")
        container.with_env("IMAGE_PROVIDER_MTLS_ENABLED", "true")
        container.with_volume_mapping(CERT_DIR, "/etc/nginx/ssl", "ro")
        container.with_exposed_ports(443)
        
        wait_for_logs(container, "start worker process", timeout=10)
        port_443 = container.get_exposed_port(443)
        
        # No client cert: should get 403
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(f"https://localhost:{port_443}/health", verify=f"{CERT_DIR}/ca-bundle.pem", timeout=5)
        
        # Invalid client cert: should get 403/SSL error
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(f"https://localhost:{port_443}/health", verify=f"{CERT_DIR}/ca-bundle.pem",
                        cert=(f"{CERT_DIR}/invalid-client.crt", f"{CERT_DIR}/invalid-client.key"), timeout=5)
        
        # Valid client cert: should work
        response = requests.get(f"https://localhost:{port_443}/health", verify=f"{CERT_DIR}/ca-bundle.pem",
                               cert=(f"{CERT_DIR}/client.crt", f"{CERT_DIR}/client.key"), timeout=5)
        assert response.status_code == 200

def test_ac5_mtls_enabled_missing_ca_bundle_fails_start():
    # AC-5: mTLS enabled but no CA bundle, fails to start
    os.makedirs(CERT_DIR, exist_ok=True)
    # Generate only server certs, no CA bundle
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", f"{CERT_DIR}/key.pem",
        "-out", f"{CERT_DIR}/cert.pem", "-days", "365", "-nodes", "-subj", "/CN=image-provider.test"
    ], check=True, capture_output=True)
    # Remove CA bundle if exists
    if os.path.exists(f"{CERT_DIR}/ca-bundle.pem"):
        os.unlink(f"{CERT_DIR}/ca-bundle.pem")
    
    with DockerContainer(IMAGE_NAME) as container:
        container.with_env("IMAGE_PROVIDER_TLS_ENABLED", "true")
        container.with_env("IMAGE_PROVIDER_MTLS_ENABLED", "true")
        container.with_volume_mapping(CERT_DIR, "/etc/nginx/ssl", "ro")
        
        time.sleep(5)
        exit_code = container.get_wrapped_container().wait()["StatusCode"]
        assert exit_code != 0
        
        logs = container.get_logs()[0].decode() + container.get_logs()[1].decode()
        assert "ca-bundle.pem" in logs or "CA" in logs or "client certificate" in logs

def test_ac6_dockerfile_creates_ssl_dir_with_correct_permissions():
    # AC-6: Dockerfile creates /etc/nginx/ssl with nginx user read permissions
    result = subprocess.run([
        "docker", "run", "--rm", "--entrypoint", "stat", IMAGE_NAME, "-c", "%a %U %G", "/etc/nginx/ssl"
    ], capture_output=True, text=True)
    assert result.returncode == 0
    
    perms, user, group = result.stdout.strip().split()
    # Check directory has read permissions for nginx user/group
    assert int(perms) >= 500  # Read + execute for owner at minimum
    assert user == "nginx" or group == "nginx"

def test_ac7_tls_disabled_existing_http_works():
    # AC-7: TLS disabled, all existing HTTP functionality works as before
    with DockerContainer(IMAGE_NAME) as container:
        container.with_env("IMAGE_PROVIDER_TLS_ENABLED", "false")
        container.with_exposed_ports(80)
        
        wait_for_logs(container, "start worker process", timeout=10)
        port_80 = container.get_exposed_port(80)
        
        # Test health endpoint
        response = requests.get(f"http://localhost:{port_80}/health", timeout=5)
        assert response.status_code == 200
        
        # Test static asset retrieval
        response = requests.get(f"http://localhost:{port_80}/static/products/1.jpg", timeout=5)
        assert response.status_code == 200
        assert len(response.content) > 0
