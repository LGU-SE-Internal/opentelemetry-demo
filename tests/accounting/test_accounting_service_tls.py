import os
import pytest
import requests
import subprocess
import time
from pathlib import Path

# Test constants from spec
HTTP_PORT = 8080
HTTPS_PORT = 8443
ENV_TLS_CERT = "ACCOUNTING_SERVICE_TLS_CERT_PATH"
ENV_TLS_KEY = "ACCOUNTING_SERVICE_TLS_KEY_PATH"
ENV_MTLS_CA = "ACCOUNTING_SERVICE_MTLS_CA_CERT_PATH"

# Helper to start accounting service with given env vars
def start_service(env_vars):
    base_env = os.environ.copy()
    base_env.update(env_vars)
    proc = subprocess.Popen(
        ["dotnet", "run", "--project", Path(__file__).parent.parent.parent / "src/accounting/AccountingService.csproj"],
        env=base_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for service to start or fail
    time.sleep(5)
    return proc

def test_ac1_no_tls_vars_starts_http_only():
    """AC-1: No TLS env vars set, service starts on HTTP 8080 only, all endpoints work"""
    # Clear TLS env vars
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: ""
    }
    proc = start_service(env)
    
    try:
        # Check HTTP endpoint works
        resp = requests.get(f"http://localhost:{HTTP_PORT}/health", timeout=2)
        assert resp.status_code == 200, "Health check should work on HTTP"
        
        # Check HTTPS endpoint does NOT work
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get(f"https://localhost:{HTTPS_PORT}/health", verify=False, timeout=2)
            
        # Check logs don't have TLS enabled message
        stdout, stderr = proc.communicate(timeout=2)
        assert "TLS enabled: false" in stdout, "Should log TLS is disabled"
    finally:
        proc.terminate()
        proc.wait()

def test_ac2_valid_tls_cert_key_starts_https_and_http():
    """AC-2: Valid TLS cert and key provided, service listens on both 8080 and 8443"""
    # Use test certs from test suite
    test_cert = Path(__file__).parent / "test_data" / "server.crt"
    test_key = Path(__file__).parent / "test_data" / "server.key"
    
    env = {
        ENV_TLS_CERT: str(test_cert),
        ENV_TLS_KEY: str(test_key),
        ENV_MTLS_CA: ""
    }
    proc = start_service(env)
    
    try:
        # Check HTTP still works
        resp_http = requests.get(f"http://localhost:{HTTP_PORT}/health", timeout=2)
        assert resp_http.status_code == 200, "Health check should work on HTTP"
        
        # Check HTTPS works with valid cert
        resp_https = requests.get(f"https://localhost:{HTTPS_PORT}/health", verify=str(test_cert), timeout=2)
        assert resp_https.status_code == 200, "Health check should work on HTTPS"
        
        # Check logs show TLS enabled
        stdout, stderr = proc.communicate(timeout=2)
        assert "TLS enabled: true" in stdout, "Should log TLS is enabled"
        assert "mTLS enabled: false" in stdout, "Should log mTLS is disabled"
    finally:
        proc.terminate()
        proc.wait()

def test_ac3_only_one_tls_param_provided_fails_start():
    """AC-3: Only one of TLS cert or key provided, service fails to start with error"""
    # Test with only cert
    env_only_cert = {
        ENV_TLS_CERT: "/tmp/fake.crt",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: ""
    }
    proc = start_service(env_only_cert)
    stdout, stderr = proc.communicate()
    assert proc.returncode != 0, "Service should fail to start"
    assert "Missing required TLS parameter" in stderr or "Missing required TLS parameter" in stdout, "Should log missing TLS parameter error"
    
    # Test with only key
    env_only_key = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "/tmp/fake.key",
        ENV_MTLS_CA: ""
    }
    proc = start_service(env_only_key)
    stdout, stderr = proc.communicate()
    assert proc.returncode != 0, "Service should fail to start"
    assert "Missing required TLS parameter" in stderr or "Missing required TLS parameter" in stdout, "Should log missing TLS parameter error"

def test_ac4_mtls_enabled_requires_client_cert():
    """AC-4: mTLS CA provided, HTTPS requests require valid client cert"""
    test_cert = Path(__file__).parent / "test_data" / "server.crt"
    test_key = Path(__file__).parent / "test_data" / "server.key"
    test_ca = Path(__file__).parent / "test_data" / "ca.crt"
    valid_client_cert = (Path(__file__).parent / "test_data" / "client.crt", Path(__file__).parent / "test_data" / "client.key")
    invalid_client_cert = (Path(__file__).parent / "test_data" / "invalid_client.crt", Path(__file__).parent / "test_data" / "invalid_client.key")
    
    env = {
        ENV_TLS_CERT: str(test_cert),
        ENV_TLS_KEY: str(test_key),
        ENV_MTLS_CA: str(test_ca)
    }
    proc = start_service(env)
    
    try:
        # Request without client cert returns 401
        resp_no_cert = requests.get(f"https://localhost:{HTTPS_PORT}/health", verify=str(test_cert), timeout=2)
        assert resp_no_cert.status_code == 401, "Request without client cert should return 401"
        
        # Request with invalid client cert returns 401
        resp_invalid_cert = requests.get(f"https://localhost:{HTTPS_PORT}/health", verify=str(test_cert), cert=invalid_client_cert, timeout=2)
        assert resp_invalid_cert.status_code == 401, "Request with invalid client cert should return 401"
        
        # Request with valid client cert works
        resp_valid_cert = requests.get(f"https://localhost:{HTTPS_PORT}/health", verify=str(test_cert), cert=valid_client_cert, timeout=2)
        assert resp_valid_cert.status_code == 200, "Request with valid client cert should work"
        
        # Check logs show mTLS enabled
        stdout, stderr = proc.communicate(timeout=2)
        assert "mTLS enabled: true" in stdout, "Should log mTLS is enabled"
    finally:
        proc.terminate()
        proc.wait()

def test_ac5_tls_only_no_mtls_accepts_all_https_requests():
    """AC-5: TLS enabled without mTLS, HTTPS requests don't require client cert"""
    test_cert = Path(__file__).parent / "test_data" / "server.crt"
    test_key = Path(__file__).parent / "test_data" / "server.key"
    
    env = {
        ENV_TLS_CERT: str(test_cert),
        ENV_TLS_KEY: str(test_key),
        ENV_MTLS_CA: ""
    }
    proc = start_service(env)
    
    try:
        # Request without client cert works
        resp = requests.get(f"https://localhost:{HTTPS_PORT}/health", verify=str(test_cert), timeout=2)
        assert resp.status_code == 200, "HTTPS request without client cert should work"
    finally:
        proc.terminate()
        proc.wait()

def test_ac6_tls_configuration_logged_on_startup():
    """AC-6: Service logs TLS configuration status on startup"""
    # Test disabled case
    env_disabled = {ENV_TLS_CERT: "", ENV_TLS_KEY: "", ENV_MTLS_CA: ""}
    proc = start_service(env_disabled)
    stdout, _ = proc.communicate()
    assert "TLS enabled: false" in stdout
    proc.terminate()
    proc.wait()
    
    # Test TLS only case
    test_cert = Path(__file__).parent / "test_data" / "server.crt"
    test_key = Path(__file__).parent / "test_data" / "server.key"
    env_tls = {ENV_TLS_CERT: str(test_cert), ENV_TLS_KEY: str(test_key), ENV_MTLS_CA: ""}
    proc = start_service(env_tls)
    stdout, _ = proc.communicate()
    assert "TLS enabled: true" in stdout
    assert "mTLS enabled: false" in stdout
    proc.terminate()
    proc.wait()
    
    # Test mTLS case
    test_ca = Path(__file__).parent / "test_data" / "ca.crt"
    env_mtls = {ENV_TLS_CERT: str(test_cert), ENV_TLS_KEY: str(test_key), ENV_MTLS_CA: str(test_ca)}
    proc = start_service(env_mtls)
    stdout, _ = proc.communicate()
    assert "TLS enabled: true" in stdout
    assert "mTLS enabled: true" in stdout
    proc.terminate()
    proc.wait()

def test_ac7_existing_functionality_unchanged():
    """AC-7: Existing functionality works on both HTTP and HTTPS"""
    test_cert = Path(__file__).parent / "test_data" / "server.crt"
    test_key = Path(__file__).parent / "test_data" / "server.key"
    
    env = {
        ENV_TLS_CERT: str(test_cert),
        ENV_TLS_KEY: str(test_key),
        ENV_MTLS_CA: ""
    }
    proc = start_service(env)
    
    try:
        # Test health check on HTTP
        resp_http_health = requests.get(f"http://localhost:{HTTP_PORT}/health", timeout=2)
        assert resp_http_health.status_code == 200
        
        # Test health check on HTTPS
        resp_https_health = requests.get(f"https://localhost:{HTTPS_PORT}/health", verify=str(test_cert), timeout=2)
        assert resp_https_health.status_code == 200
        
        # Test existing API endpoint (example: /transactions) on both protocols
        resp_http_api = requests.get(f"http://localhost:{HTTP_PORT}/api/transactions", timeout=2)
        resp_https_api = requests.get(f"https://localhost:{HTTPS_PORT}/api/transactions", verify=str(test_cert), timeout=2)
        assert resp_http_api.status_code == resp_https_api.status_code
        assert resp_http_api.json() == resp_https_api.json()
    finally:
        proc.terminate()
        proc.wait()
