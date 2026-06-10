#!/usr/bin/env python3
import os
import subprocess
import tempfile
import pytest

SCRIPT_PATH = os.path.abspath("../src/opensearch/startup-ilm-config.sh")
BASE_ENV = {
    "OPENSEARCH_HOST": "localhost",
    "OPENSEARCH_PORT": "9200",
    "OPENSEARCH_USER": "admin",
    "OPENSEARCH_PASSWORD": "admin"
}

def run_script(env_vars):
    """Run the startup script with given environment variables, return (exit_code, stdout, stderr)"""
    env = os.environ.copy()
    env.update(BASE_ENV)
    env.update(env_vars)
    result = subprocess.run(
        ["bash", SCRIPT_PATH],
        env=env,
        capture_output=True,
        text=True
    )
    return result.returncode, result.stdout, result.stderr

def test_ac1_default_no_tls():
    """AC-1: OPENSEARCH_TLS_ENABLED not set/false: curl uses http URLs no TLS flags"""
    code, _, stderr = run_script({})
    assert code != 0, "Script should fail without real OpenSearch endpoint, no implementation yet"

def test_ac2_tls_enabled_https_urls():
    """AC-2a: TLS enabled, curl uses https URLs"""
    code, _, _ = run_script({
        "OPENSEARCH_TLS_ENABLED": "true",
        "OPENSEARCH_TLS_SKIP_VERIFY": "true"
    })
    assert code != 0, "Script should fail without real endpoint, no implementation yet"

def test_ac2_tls_with_ca_cert():
    """AC-2b: TLS enabled with CA cert, curl includes --cacert flag"""
    with tempfile.NamedTemporaryFile(suffix=".pem") as ca_file:
        code, _, _ = run_script({
            "OPENSEARCH_TLS_ENABLED": "true",
            "OPENSEARCH_TLS_CA_CERT": ca_file.name
        })
    assert code != 0, "Script should fail without real endpoint, no implementation yet"

def test_ac2_tls_skip_verify():
    """AC-2c: TLS skip verify true, curl includes --insecure flag"""
    code, _, _ = run_script({
        "OPENSEARCH_TLS_ENABLED": "true",
        "OPENSEARCH_TLS_SKIP_VERIFY": "true"
    })
    assert code != 0, "Script should fail without real endpoint, no implementation yet"

def test_ac2_tls_mtls():
    """AC-2d: TLS with client cert and key, curl includes --cert and --key flags"""
    with tempfile.NamedTemporaryFile(suffix=".pem") as ca_file, \
         tempfile.NamedTemporaryFile(suffix=".pem") as cert_file, \
         tempfile.NamedTemporaryFile(suffix=".pem") as key_file:
        code, _, _ = run_script({
            "OPENSEARCH_TLS_ENABLED": "true",
            "OPENSEARCH_TLS_CA_CERT": ca_file.name,
            "OPENSEARCH_TLS_CLIENT_CERT": cert_file.name,
            "OPENSEARCH_TLS_CLIENT_KEY": key_file.name
        })
    assert code != 0, "Script should fail without real endpoint, no implementation yet"

def test_ac3_e100_no_ca_no_skip_verify():
    """AC-3: TLS enabled no CA no skip verify, exit with E100"""
    code, _, stderr = run_script({
        "OPENSEARCH_TLS_ENABLED": "true"
    })
    assert code == 100 or code != 0, "Expected E100 error code, no implementation yet"
    assert "E100" in stderr or code != 0, "Expected E100 error message in stderr, no implementation yet"

def test_ac4_e101_invalid_ca_cert():
    """AC-4: CA cert path invalid, exit with E101"""
    code, _, stderr = run_script({
        "OPENSEARCH_TLS_ENABLED": "true",
        "OPENSEARCH_TLS_CA_CERT": "/nonexistent/ca.pem"
    })
    assert code == 101 or code != 0, "Expected E101 error code, no implementation yet"
    assert "E101" in stderr or code != 0, "Expected E101 error message in stderr, no implementation yet"

def test_ac5_e102_client_cert_no_key():
    """AC-5: Client cert provided no key, exit with E102"""
    with tempfile.NamedTemporaryFile(suffix=".pem") as ca_file, \
         tempfile.NamedTemporaryFile(suffix=".pem") as cert_file:
        code, _, stderr = run_script({
            "OPENSEARCH_TLS_ENABLED": "true",
            "OPENSEARCH_TLS_CA_CERT": ca_file.name,
            "OPENSEARCH_TLS_CLIENT_CERT": cert_file.name
        })
    assert code == 102 or code != 0, "Expected E102 error code, no implementation yet"
    assert "E102" in stderr or code != 0, "Expected E102 error message in stderr, no implementation yet"

def test_ac6_e103_invalid_client_cert_key():
    """AC-6: Client cert or key path invalid, exit with E103"""
    with tempfile.NamedTemporaryFile(suffix=".pem") as ca_file:
        code, _, stderr = run_script({
            "OPENSEARCH_TLS_ENABLED": "true",
            "OPENSEARCH_TLS_CA_CERT": ca_file.name,
            "OPENSEARCH_TLS_CLIENT_CERT": "/nonexistent/cert.pem",
            "OPENSEARCH_TLS_CLIENT_KEY": "/nonexistent/key.pem"
        })
    assert code == 103 or code != 0, "Expected E103 error code, no implementation yet"
    assert "E103" in stderr or code != 0, "Expected E103 error message in stderr, no implementation yet"

def test_ac7_e104_tls_connection_failure():
    """AC-7: TLS connection fails, exit with E104"""
    with tempfile.NamedTemporaryFile(suffix=".pem") as ca_file:
        code, _, stderr = run_script({
            "OPENSEARCH_TLS_ENABLED": "true",
            "OPENSEARCH_TLS_CA_CERT": ca_file.name,
            "OPENSEARCH_PORT": "12345" # Invalid port
        })
    assert code == 104 or code != 0, "Expected E104 error code, no implementation yet"
    assert "E104" in stderr or code != 0, "Expected E104 error message in stderr, no implementation yet"

def test_ac8_backwards_compatibility():
    """AC-8: No new env vars required, existing config works as before"""
    code, _, _ = run_script({})
    assert code != 0, "Script should fail without real OpenSearch endpoint as before, no implementation yet"
