import os
import ssl
import tempfile
import pytest
import subprocess
import requests
from time import sleep
from pathlib import Path

# Environment variable names from spec
TLS_CERT_PATH_VAR = "IMAGE_PROVIDER_TLS_CERT_PATH"
TLS_KEY_PATH_VAR = "IMAGE_PROVIDER_TLS_KEY_PATH"
TLS_CA_CERT_PATH_VAR = "IMAGE_PROVIDER_TLS_CA_CERT_PATH"
LISTEN_PORT = 8080
SERVICE_ENTRYPOINT = "python main.py"  # Assuming standard entrypoint

def generate_test_certs(cert_path, key_path, ca_cert_path=None, is_ca=False):
    """Helper to generate self-signed test certificates using openssl"""
    # Generate private key
    subprocess.run(
        ["openssl", "genrsa", "-out", str(key_path), "2048"],
        check=True, capture_output=True
    )
    # Generate CSR
    subprocess.run(
        ["openssl", "req", "-new", "-key", str(key_path), "-out", "/tmp/test.csr",
         "-subj", "/CN=test-image-provider.local"],
        check=True, capture_output=True
    )
    # Generate certificate
    extensions = ["-extensions", "v3_ca"] if is_ca else []
    subprocess.run(
        ["openssl", "x509", "-req", "-days", "365", "-in", "/tmp/test.csr",
         "-signkey", str(key_path), "-out", str(cert_path), *extensions],
        check=True, capture_output=True
    )
    if ca_cert_path:
        # Generate client cert signed by CA
        client_key = "/tmp/client.key"
        client_csr = "/tmp/client.csr"
        subprocess.run(["openssl", "genrsa", "-out", client_key, "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "req", "-new", "-key", client_key, "-out", client_csr,
                        "-subj", "/CN=test-client.local"], check=True, capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-days", "365", "-in", client_csr,
                        "-CA", str(ca_cert_path), "-CAkey", str(key_path),
                        "-CAcreateserial", "-out", "/tmp/client.crt"],
                       check=True, capture_output=True)
        return Path(client_key), Path("/tmp/client.crt")
    return None

@pytest.fixture
def temp_certs():
    """Fixture that provides valid matching cert and key files"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = Path(tmpdir) / "server.crt"
        key_path = Path(tmpdir) / "server.key"
        generate_test_certs(cert_path, key_path)
        yield str(cert_path), str(key_path)

@pytest.fixture
def temp_ca_certs(temp_certs):
    """Fixture that provides valid CA cert for mTLS"""
    cert_path, key_path = temp_certs
    with tempfile.TemporaryDirectory() as tmpdir:
        ca_cert_path = Path(tmpdir) / "ca.crt"
        ca_key_path = Path(tmpdir) / "ca.key"
        client_key, client_cert = generate_test_certs(ca_cert_path, ca_key_path, is_ca=True, ca_cert_path=ca_cert_path)
        yield str(ca_cert_path), str(client_key), str(client_cert)

def test_ac1_plaintext_operation_when_no_tls_vars_set():
    # AC-1: No TLS vars set -> service starts, accepts plaintext connections
    env = os.environ.copy()
    env.pop(TLS_CERT_PATH_VAR, None)
    env.pop(TLS_KEY_PATH_VAR, None)
    env.pop(TLS_CA_CERT_PATH_VAR, None)
    
    # Start service
    proc = subprocess.Popen(
        SERVICE_ENTRYPOINT.split(),
        env=env, cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    
    try:
        sleep(2)
        # Check service is running
        assert proc.poll() is None, "Service exited unexpectedly when no TLS config provided"
        
        # Test plaintext connection succeeds
        resp = requests.get(f"http://localhost:{LISTEN_PORT}/health", timeout=2)
        assert resp.status_code == 200, "Plaintext connection failed when no TLS config provided"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac2_tls_connections_accepted_and_plaintext_rejected_with_valid_certs(temp_certs):
    cert_path, key_path = temp_certs
    env = os.environ.copy()
    env[TLS_CERT_PATH_VAR] = cert_path
    env[TLS_KEY_PATH_VAR] = key_path
    env.pop(TLS_CA_CERT_PATH_VAR, None)
    
    proc = subprocess.Popen(
        SERVICE_ENTRYPOINT.split(),
        env=env, cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    
    try:
        sleep(2)
        assert proc.poll() is None, "Service exited unexpectedly with valid TLS config"
        
        # Test plaintext connection is rejected
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get(f"http://localhost:{LISTEN_PORT}/health", timeout=2)
        
        # Test TLS 1.2+ connection succeeds
        resp = requests.get(f"https://localhost:{LISTEN_PORT}/health", verify=False, timeout=2)
        assert resp.status_code == 200, "TLS connection failed with valid TLS config"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac3_error_when_only_one_tls_param_provided(temp_certs):
    cert_path, _ = temp_certs
    env = os.environ.copy()
    env[TLS_CERT_PATH_VAR] = cert_path
    env.pop(TLS_KEY_PATH_VAR, None)
    env.pop(TLS_CA_CERT_PATH_VAR, None)
    
    proc = subprocess.Popen(
        SERVICE_ENTRYPOINT.split(),
        env=env, cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stdout, stderr = proc.communicate(timeout=5)
    
    assert proc.returncode != 0, "Service started successfully with incomplete TLS config"
    assert "TLSConfigurationError" in stderr + stdout, "Missing TLSConfigurationError for incomplete config"
    assert "missing" in (stderr + stdout).lower(), "Error message does not mention missing parameter"
    
    # Check no sockets are open
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://localhost:{LISTEN_PORT}/health", timeout=1)
        requests.get(f"https://localhost:{LISTEN_PORT}/health", timeout=1, verify=False)

def test_ac4_error_when_cert_file_missing(temp_certs):
    _, key_path = temp_certs
    env = os.environ.copy()
    env[TLS_CERT_PATH_VAR] = "/non/existent/path/cert.crt"
    env[TLS_KEY_PATH_VAR] = key_path
    env.pop(TLS_CA_CERT_PATH_VAR, None)
    
    proc = subprocess.Popen(
        SERVICE_ENTRYPOINT.split(),
        env=env, cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stdout, stderr = proc.communicate(timeout=5)
    
    assert proc.returncode != 0
    assert "TLSConfigurationError" in stderr + stdout
    assert "certificate" in (stderr + stdout).lower()
    assert "missing" in (stderr + stdout).lower() or "not found" in (stderr + stdout).lower()

def test_ac5_error_when_key_file_missing(temp_certs):
    cert_path, _ = temp_certs
    env = os.environ.copy()
    env[TLS_CERT_PATH_VAR] = cert_path
    env[TLS_KEY_PATH_VAR] = "/non/existent/path/key.key"
    env.pop(TLS_CA_CERT_PATH_VAR, None)
    
    proc = subprocess.Popen(
        SERVICE_ENTRYPOINT.split(),
        env=env, cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stdout, stderr = proc.communicate(timeout=5)
    
    assert proc.returncode != 0
    assert "TLSConfigurationError" in stderr + stdout
    assert "key" in (stderr + stdout).lower()
    assert "missing" in (stderr + stdout).lower() or "not found" in (stderr + stdout).lower()

def test_ac6_error_when_cert_and_key_mismatched(temp_certs):
    cert_path, _ = temp_certs
    # Generate a separate unrelated key
    with tempfile.TemporaryDirectory() as tmpdir:
        wrong_key_path = Path(tmpdir) / "wrong.key"
        subprocess.run(["openssl", "genrsa", "-out", str(wrong_key_path), "2048"], check=True, capture_output=True)
        
        env = os.environ.copy()
        env[TLS_CERT_PATH_VAR] = cert_path
        env[TLS_KEY_PATH_VAR] = str(wrong_key_path)
        env.pop(TLS_CA_CERT_PATH_VAR, None)
        
        proc = subprocess.Popen(
            SERVICE_ENTRYPOINT.split(),
            env=env, cwd=Path(__file__).parent.parent,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        stdout, stderr = proc.communicate(timeout=5)
        
        assert proc.returncode != 0
        assert "TLSConfigurationError" in stderr + stdout
        assert "invalid" in (stderr + stdout).lower() or "mismatch" in (stderr + stdout).lower()

def test_ac7_mtls_enforced_when_ca_provided(temp_certs, temp_ca_certs):
    cert_path, key_path = temp_certs
    ca_cert_path, client_key, client_cert = temp_ca_certs
    env = os.environ.copy()
    env[TLS_CERT_PATH_VAR] = cert_path
    env[TLS_KEY_PATH_VAR] = key_path
    env[TLS_CA_CERT_PATH_VAR] = ca_cert_path
    
    proc = subprocess.Popen(
        SERVICE_ENTRYPOINT.split(),
        env=env, cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    
    try:
        sleep(2)
        assert proc.poll() is None, "Service exited unexpectedly with valid mTLS config"
        
        # Test connection without client cert is rejected
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(f"https://localhost:{LISTEN_PORT}/health", verify=False, timeout=2)
        
        # Test connection with valid client cert succeeds
        resp = requests.get(f"https://localhost:{LISTEN_PORT}/health",
                            cert=(str(client_cert), str(client_key)),
                            verify=False, timeout=2)
        assert resp.status_code == 200, "mTLS connection failed with valid client cert"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac8_error_when_ca_cert_invalid(temp_certs):
    cert_path, key_path = temp_certs
    # Create invalid CA file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.crt') as f:
        f.write("this is not a valid PEM file")
        f.flush()
        
        env = os.environ.copy()
        env[TLS_CERT_PATH_VAR] = cert_path
        env[TLS_KEY_PATH_VAR] = key_path
        env[TLS_CA_CERT_PATH_VAR] = f.name
        
        proc = subprocess.Popen(
            SERVICE_ENTRYPOINT.split(),
            env=env, cwd=Path(__file__).parent.parent,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        stdout, stderr = proc.communicate(timeout=5)
        
        assert proc.returncode != 0
        assert "TLSConfigurationError" in stderr + stdout
        assert "CA" in (stderr + stdout) or "certificate authority" in (stderr + stdout).lower()
        assert "invalid" in (stderr + stdout).lower() or "corrupted" in (stderr + stdout).lower()
