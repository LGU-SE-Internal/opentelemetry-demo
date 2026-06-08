#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
import pytest
import os
import tempfile
import ssl
import subprocess
import requests
from requests.exceptions import SSLError

@pytest.fixture
def temp_cert_files():
    """Fixture to create temporary test certificate/key files (invalid dummy content for failure tests)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create dummy invalid files for path existence tests
        cert_path = os.path.join(tmpdir, "cert.pem")
        key_path = os.path.join(tmpdir, "key.pem")
        ca_path = os.path.join(tmpdir, "ca.pem")
        
        with open(cert_path, "w") as f:
            f.write("invalid cert content")
        with open(key_path, "w") as f:
            f.write("invalid key content")
        with open(ca_path, "w") as f:
            f.write("invalid ca content")
            
        yield {
            "cert": cert_path,
            "key": key_path,
            "ca": ca_path,
            "dir": tmpdir
        }

def run_llm_service(env_vars, timeout=5):
    """Helper to run the llm service with given env vars and capture output/exit code"""
    env = os.environ.copy()
    env.update(env_vars)
    proc = subprocess.Popen(
        ["python", "app.py"],
        cwd="./src/llm",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return -1, "", "Service timed out (likely running successfully if no error)"

def test_ac1_no_tls_vars_starts_http_mode():
    """AC-1: When no TLS-related environment variables are set, service starts normally, accepts unencrypted HTTP requests on its configured port, and all existing endpoints return expected responses."""
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac2_valid_tls_cert_key_starts_https_mode():
    """AC-2: When valid, readable PEM paths are provided for both LLM_SERVICE_TLS_CERT_PATH and LLM_SERVICE_TLS_KEY_PATH:
    - Service starts successfully in HTTPS mode
    - Service accepts valid HTTPS requests on its configured port using the provided certificate
    - All existing endpoints return identical responses as over HTTP
    - Unencrypted HTTP requests to the port are rejected
    """
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac3_only_cert_provided_fails_start():
    """AC-3: When only LLM_SERVICE_TLS_CERT_PATH is set (key path missing), service fails to start with the required error message indicating both cert and key are needed."""
    env = {
        "LLM_SERVICE_TLS_CERT_PATH": "/tmp/fake-cert.pem"
    }
    returncode, stdout, stderr = run_llm_service(env)
    assert returncode != 0, "Service should fail to start when only cert path is provided"
    assert "Both TLS certificate and key paths must be provided to enable HTTPS mode" in stderr + stdout, f"Missing expected error message, output: {stderr + stdout}"
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac4_only_key_provided_fails_start():
    """AC-4: When only LLM_SERVICE_TLS_KEY_PATH is set (cert path missing), service fails to start with the required error message indicating both cert and key are needed."""
    env = {
        "LLM_SERVICE_TLS_KEY_PATH": "/tmp/fake-key.pem"
    }
    returncode, stdout, stderr = run_llm_service(env)
    assert returncode != 0, "Service should fail to start when only key path is provided"
    assert "Both TLS certificate and key paths must be provided to enable HTTPS mode" in stderr + stdout, f"Missing expected error message, output: {stderr + stdout}"
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac5_cert_path_nonexistent_fails_start(temp_cert_files):
    """AC-5: When LLM_SERVICE_TLS_CERT_PATH points to a non-existent or unreadable file, service fails to start with file access error."""
    env = {
        "LLM_SERVICE_TLS_CERT_PATH": "/tmp/non-existent-cert-1234.pem",
        "LLM_SERVICE_TLS_KEY_PATH": temp_cert_files["key"]
    }
    returncode, stdout, stderr = run_llm_service(env)
    assert returncode != 0, "Service should fail to start when cert path does not exist"
    assert "TLS file not found or unreadable:" in stderr + stdout, f"Missing expected file access error, output: {stderr + stdout}"
    assert "/tmp/non-existent-cert-1234.pem" in stderr + stdout, f"Missing path in error message, output: {stderr + stdout}"
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac6_key_path_nonexistent_fails_start(temp_cert_files):
    """AC-6: When LLM_SERVICE_TLS_KEY_PATH points to a non-existent or unreadable file, service fails to start with file access error."""
    env = {
        "LLM_SERVICE_TLS_CERT_PATH": temp_cert_files["cert"],
        "LLM_SERVICE_TLS_KEY_PATH": "/tmp/non-existent-key-1234.pem"
    }
    returncode, stdout, stderr = run_llm_service(env)
    assert returncode != 0, "Service should fail to start when key path does not exist"
    assert "TLS file not found or unreadable:" in stderr + stdout, f"Missing expected file access error, output: {stderr + stdout}"
    assert "/tmp/non-existent-key-1234.pem" in stderr + stdout, f"Missing path in error message, output: {stderr + stdout}"
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac7_invalid_pem_format_fails_start(temp_cert_files):
    """AC-7: When LLM_SERVICE_TLS_CERT_PATH/LLM_SERVICE_TLS_KEY_PATH point to files that are not valid PEM format certificate/key pairs, service fails to start with invalid format error."""
    env = {
        "LLM_SERVICE_TLS_CERT_PATH": temp_cert_files["cert"],
        "LLM_SERVICE_TLS_KEY_PATH": temp_cert_files["key"]
    }
    returncode, stdout, stderr = run_llm_service(env)
    assert returncode != 0, "Service should fail to start when cert/key are invalid PEM"
    assert "Invalid TLS" in stderr + stdout, f"Missing expected invalid format error, output: {stderr + stdout}"
    assert ("certificate" in stderr + stdout or "key" in stderr + stdout), f"Missing cert/key reference in error, output: {stderr + stdout}"
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac8_all_three_tls_vars_enables_mtls():
    """AC-8: When valid paths are provided for all three TLS variables (cert, key, CA cert):
    - Service starts successfully in HTTPS + mTLS mode
    - Requests with a valid client certificate signed by the provided CA are accepted and return expected responses
    - Requests without a client certificate are rejected with 401 Unauthorized
    - Requests with an invalid/expired client certificate, or one not signed by the provided CA, are rejected with 401 Unauthorized
    """
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac9_ca_provided_without_cert_key_fails_start(temp_cert_files):
    """AC-9: When LLM_SERVICE_TLS_CA_CERT_PATH is provided without both TLS cert and key paths, service fails to start with error indicating mTLS requires HTTPS enabled."""
    env = {
        "LLM_SERVICE_TLS_CA_CERT_PATH": temp_cert_files["ca"]
    }
    returncode, stdout, stderr = run_llm_service(env)
    assert returncode != 0, "Service should fail to start when CA provided without cert/key"
    assert "mTLS requires HTTPS mode: TLS certificate and key paths must also be provided" in stderr + stdout, f"Missing expected mTLS error, output: {stderr + stdout}"
    pytest.fail("Not implemented - TLS support not yet added")

def test_ac10_invalid_ca_cert_fails_start(temp_cert_files):
    """AC-10: When LLM_SERVICE_TLS_CA_CERT_PATH points to a non-existent, unreadable, or invalid PEM file, service fails to start with invalid CA error."""
    env = {
        "LLM_SERVICE_TLS_CERT_PATH": temp_cert_files["cert"],
        "LLM_SERVICE_TLS_KEY_PATH": temp_cert_files["key"],
        "LLM_SERVICE_TLS_CA_CERT_PATH": "/tmp/non-existent-ca-1234.pem"
    }
    returncode, stdout, stderr = run_llm_service(env)
    assert returncode != 0, "Service should fail to start when CA path is invalid"
    assert "Invalid mTLS CA certificate:" in stderr + stdout, f"Missing expected invalid CA error, output: {stderr + stdout}"
    pytest.fail("Not implemented - TLS support not yet added")
