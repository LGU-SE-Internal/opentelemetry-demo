import os
import pytest
import requests
from kubernetes import client, config

# Test AC-1: TLS enabled works, HTTPS on 443, HTTP on 3000 rejected
def test_ac1_tls_enabled_https_only():
    # Env setup for AC1
    os.environ["GRAFANA_TLS_ENABLED"] = "true"
    os.environ["GRAFANA_TLS_CERT_PATH"] = "/tmp/test.crt"
    os.environ["GRAFANA_TLS_KEY_PATH"] = "/tmp/test.key"
    
    # Check config generated correctly
    from src.grafana.config import generate_grafana_ini
    ini_content = generate_grafana_ini()
    
    assert "[server]" in ini_content
    assert "protocol = https" in ini_content
    assert f"cert_file = {os.environ['GRAFANA_TLS_CERT_PATH']}" in ini_content
    assert f"cert_key = {os.environ['GRAFANA_TLS_KEY_PATH']}" in ini_content
    
    # Check service port is 443
    from src.grafana.k8s import generate_grafana_service
    service_spec = generate_grafana_service()
    assert any(port.port == 443 for port in service_spec.spec.ports)
    assert not any(port.port == 3000 for port in service_spec.spec.ports)
    
    # Invariant: HTTP connection to 3000 is rejected, HTTPS to 443 works
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("http://localhost:3000", timeout=2)
    # Note: This will fail until implementation exists
    response = requests.get("https://localhost:443", verify=False, timeout=2)
    assert response.status_code in [200, 302, 401]


# Test AC-2: TLS disabled (default) uses HTTP on 3000, no HTTPS port
def test_ac2_tls_disabled_http_only():
    # Clear any TLS env vars, use defaults
    if "GRAFANA_TLS_ENABLED" in os.environ:
        del os.environ["GRAFANA_TLS_ENABLED"]
    if "GRAFANA_MTLS_ENABLED" in os.environ:
        del os.environ["GRAFANA_MTLS_ENABLED"]
    
    from src.grafana.config import generate_grafana_ini
    ini_content = generate_grafana_ini()
    
    assert "[server]" in ini_content
    assert "protocol = http" in ini_content
    assert "cert_file" not in ini_content
    assert "cert_key" not in ini_content
    
    from src.grafana.k8s import generate_grafana_service
    service_spec = generate_grafana_service()
    assert any(port.port == 3000 for port in service_spec.spec.ports)
    assert not any(port.port == 443 for port in service_spec.spec.ports)
    
    # Invariant: HTTPS to 443 rejected, HTTP to 3000 works
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("https://localhost:443", verify=False, timeout=2)
    # Note: This will fail until implementation exists
    response = requests.get("http://localhost:3000", timeout=2)
    assert response.status_code in [200, 302, 401]


# Test AC-3: mTLS enabled requires valid client cert
def test_ac3_mtls_enabled_enforces_client_cert():
    os.environ["GRAFANA_TLS_ENABLED"] = "true"
    os.environ["GRAFANA_MTLS_ENABLED"] = "true"
    os.environ["GRAFANA_TLS_CERT_PATH"] = "/tmp/test.crt"
    os.environ["GRAFANA_TLS_KEY_PATH"] = "/tmp/test.key"
    os.environ["GRAFANA_MTLS_CA_CERT_PATH"] = "/tmp/ca.crt"
    
    from src.grafana.config import generate_grafana_ini
    ini_content = generate_grafana_ini()
    
    assert "client_ca_file = /tmp/ca.crt" in ini_content
    assert "client_auth_type = RequireAndVerifyClientCert" in ini_content
    
    # Invariant: Connection without client cert returns 401
    # Note: This will fail until implementation exists
    response = requests.get("https://localhost:443", verify=False, timeout=2)
    assert response.status_code == 401
    
    # Connection with valid client cert works
    # Note: This will fail until implementation exists
    valid_cert = ("/tmp/client.crt", "/tmp/client.key")
    response = requests.get("https://localhost:443", cert=valid_cert, verify=False, timeout=2)
    assert response.status_code in [200, 302]


# Test AC-4: mTLS disabled accepts all HTTPS clients
def test_ac4_mtls_disabled_no_client_cert_required():
    os.environ["GRAFANA_TLS_ENABLED"] = "true"
    os.environ["GRAFANA_MTLS_ENABLED"] = "false"
    os.environ["GRAFANA_TLS_CERT_PATH"] = "/tmp/test.crt"
    os.environ["GRAFANA_TLS_KEY_PATH"] = "/tmp/test.key"
    
    from src.grafana.config import generate_grafana_ini
    ini_content = generate_grafana_ini()
    
    assert "client_ca_file" not in ini_content
    assert "client_auth_type" not in ini_content
    
    # Invariant: No client cert needed for HTTPS connection
    # Note: This will fail until implementation exists
    response = requests.get("https://localhost:443", verify=False, timeout=2)
    assert response.status_code in [200, 302, 401]


# Test AC-5: TLS enabled mounts secrets correctly
def test_ac5_tls_secret_mounts():
    os.environ["GRAFANA_TLS_ENABLED"] = "true"
    os.environ["GRAFANA_TLS_SECRET_NAME"] = "test-grafana-tls"
    os.environ["GRAFANA_TLS_CERT_PATH"] = "/etc/grafana/tls/tls.crt"
    os.environ["GRAFANA_TLS_KEY_PATH"] = "/etc/grafana/tls/tls.key"
    
    from src.grafana.k8s import generate_grafana_deployment
    deployment = generate_grafana_deployment()
    
    # Check volumes exist
    volumes = {v.name: v for v in deployment.spec.template.spec.volumes}
    assert "grafana-tls-cert" in volumes
    assert volumes["grafana-tls-cert"].secret.secret_name == "test-grafana-tls"
    assert "grafana-tls-key" in volumes
    assert volumes["grafana-tls-key"].secret.secret_name == "test-grafana-tls"
    
    # Check volume mounts exist
    container = deployment.spec.template.spec.containers[0]
    mounts = {m.mount_path: m for m in container.volume_mounts}
    assert os.environ["GRAFANA_TLS_CERT_PATH"] in mounts
    assert os.environ["GRAFANA_TLS_KEY_PATH"] in mounts


# Test AC-6: mTLS enabled mounts CA secret correctly
def test_ac6_mtls_ca_secret_mounts():
    os.environ["GRAFANA_TLS_ENABLED"] = "true"
    os.environ["GRAFANA_MTLS_ENABLED"] = "true"
    os.environ["GRAFANA_MTLS_CA_SECRET_NAME"] = "test-grafana-mtls-ca"
    os.environ["GRAFANA_MTLS_CA_CERT_PATH"] = "/etc/grafana/mtls/ca.crt"
    
    from src.grafana.k8s import generate_grafana_deployment
    deployment = generate_grafana_deployment()
    
    volumes = {v.name: v for v in deployment.spec.template.spec.volumes}
    assert "grafana-mtls-ca" in volumes
    assert volumes["grafana-mtls-ca"].secret.secret_name == "test-grafana-mtls-ca"
    
    container = deployment.spec.template.spec.containers[0]
    mounts = {m.mount_path: m for m in container.volume_mounts}
    assert os.environ["GRAFANA_MTLS_CA_CERT_PATH"] in mounts


# Test AC-7: mTLS enabled with TLS disabled fails startup
def test_ac7_mtls_without_tls_fails():
    os.environ["GRAFANA_TLS_ENABLED"] = "false"
    os.environ["GRAFANA_MTLS_ENABLED"] = "true"
    
    from src.grafana.config import validate_config
    # Invariant: Config validation fails, error logged
    with pytest.raises(ValueError, match="mTLS cannot be enabled without TLS being enabled first"):
        validate_config()


# Test AC-8: TLS enabled without cert/key paths fails
def test_ac8_tls_enabled_missing_cert_key_fails():
    os.environ["GRAFANA_TLS_ENABLED"] = "true"
    if "GRAFANA_TLS_CERT_PATH" in os.environ:
        del os.environ["GRAFANA_TLS_CERT_PATH"]
    if "GRAFANA_TLS_KEY_PATH" in os.environ:
        del os.environ["GRAFANA_TLS_KEY_PATH"]
    
    from src.grafana.config import validate_config
    # Check missing cert path
    with pytest.raises(ValueError, match="GRAFANA_TLS_CERT_PATH is required when TLS is enabled"):
        validate_config()
    
    os.environ["GRAFANA_TLS_CERT_PATH"] = "/tmp/test.crt"
    # Check missing key path
    with pytest.raises(ValueError, match="GRAFANA_TLS_KEY_PATH is required when TLS is enabled"):
        validate_config()


# Test AC-9: mTLS enabled without CA path fails
def test_ac9_mtls_enabled_missing_ca_fails():
    os.environ["GRAFANA_TLS_ENABLED"] = "true"
    os.environ["GRAFANA_MTLS_ENABLED"] = "true"
    if "GRAFANA_MTLS_CA_CERT_PATH" in os.environ:
        del os.environ["GRAFANA_MTLS_CA_CERT_PATH"]
    
    from src.grafana.config import validate_config
    with pytest.raises(ValueError, match="GRAFANA_MTLS_CA_CERT_PATH is required when mTLS is enabled"):
        validate_config()
