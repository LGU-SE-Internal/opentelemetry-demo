#!/usr/bin/env python3
import os
import sys
import time
import tempfile
import subprocess
import grpc
import ssl
from typing import Optional, Tuple

# Import protobuf definitions for currency service
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'pb'))
import demo_pb2
import demo_pb2_grpc

DEFAULT_CURRENCY_SERVICE_ADDR = "localhost:7000"
SERVICE_BINARY = os.path.join(os.path.dirname(__file__), '..', 'src', 'currency', 'server')

# Generate test TLS certificates for use in tests
def generate_test_certs() -> Tuple[str, str, str, str, str]:
    """Generate self-signed test CA, server cert/key, client cert/key in temp directory"""
    tmpdir = tempfile.mkdtemp()
    
    # Generate CA key and cert
    subprocess.run([
        "openssl", "req", "-x509", "-sha256", "-nodes", "-newkey", "rsa:2048", "-days", "1",
        "-keyout", f"{tmpdir}/ca.key", "-out", f"{tmpdir}/ca.crt",
        "-subj", "/CN=test-ca",
    ], check=True, capture_output=True)
    
    # Generate server key and CSR
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{tmpdir}/server.key",
        "-out", f"{tmpdir}/server.csr", "-subj", "/CN=localhost",
    ], check=True, capture_output=True)
    
    # Sign server cert with CA
    subprocess.run([
        "openssl", "x509", "-req", "-in", f"{tmpdir}/server.csr", "-CA", f"{tmpdir}/ca.crt",
        "-CAkey", f"{tmpdir}/ca.key", "-CAcreateserial", "-out", f"{tmpdir}/server.crt",
        "-days", "1", "-sha256",
    ], check=True, capture_output=True)
    
    # Generate client key and CSR
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{tmpdir}/client.key",
        "-out", f"{tmpdir}/client.csr", "-subj", "/CN=test-client",
    ], check=True, capture_output=True)
    
    # Sign client cert with CA
    subprocess.run([
        "openssl", "x509", "-req", "-in", f"{tmpdir}/client.csr", "-CA", f"{tmpdir}/ca.crt",
        "-CAkey", f"{tmpdir}/ca.key", "-CAcreateserial", "-out", f"{tmpdir}/client.crt",
        "-days", "1", "-sha256",
    ], check=True, capture_output=True)
    
    return (
        f"{tmpdir}/ca.crt",
        f"{tmpdir}/server.crt",
        f"{tmpdir}/server.key",
        f"{tmpdir}/client.crt",
        f"{tmpdir}/client.key",
    )

def start_currency_service(env_overrides: dict = None, wait_time: int = 3) -> Tuple[subprocess.Popen, str, str]:
    """Start currency service with optional env vars, return process and stdout/stderr after wait"""
    env = os.environ.copy()
    env["PORT"] = DEFAULT_CURRENCY_SERVICE_ADDR.split(":")[1]
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.Popen(
        [SERVICE_BINARY],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(wait_time)
    return_code = proc.poll()
    stdout, stderr = proc.communicate(timeout=2) if return_code is not None else ("", "")
    return proc, stdout, stderr

def test_ac1_tls_enabled_successful_start():
    """AC-1: When CURRENCY_SERVICE_TLS_CERT_PATH and CURRENCY_SERVICE_TLS_KEY_PATH are set to valid readable PEM files,
    the currency service starts successfully using gRPC TLS server credentials and accepts TLS 1.2+ connections from clients."""
    ca_crt, server_crt, server_key, _, _ = generate_test_certs()
    env = {
        "CURRENCY_SERVICE_TLS_CERT_PATH": server_crt,
        "CURRENCY_SERVICE_TLS_KEY_PATH": server_key
    }
    
    proc, _, stderr = start_currency_service(env)
    try:
        assert proc.poll() is None, f"Service failed to start with valid TLS config: {stderr}"
        
        # Test TLS connection works
        with open(ca_crt, 'rb') as f:
            ca_data = f.read()
        credentials = grpc.ssl_channel_credentials(root_certificates=ca_data)
        with grpc.secure_channel(DEFAULT_CURRENCY_SERVICE_ADDR, credentials) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            response = stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD", to_currency="EUR", units=100, nanos=0
            ))
            assert response is not None and response.units > 0, "TLS gRPC connection failed"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

def test_ac2_single_tls_config_param_fails():
    """AC-2: When only one of CURRENCY_SERVICE_TLS_CERT_PATH or CURRENCY_SERVICE_TLS_KEY_PATH is set,
    the service fails to start with the required pair error message."""
    _, server_crt, server_key, _, _ = generate_test_certs()
    
    # Test only cert path provided
    env1 = {"CURRENCY_SERVICE_TLS_CERT_PATH": server_crt}
    proc1, _, stderr1 = start_currency_service(env1)
    try:
        assert proc1.poll() is not None, "Service should exit when only cert path is provided"
        assert "Both TLS certificate path and private key path must be provided to enable TLS" in stderr1, \
            f"Wrong error message for single cert path: {stderr1}"
    finally:
        if proc1.poll() is None:
            proc1.terminate()
            proc1.wait(timeout=5)
    
    # Test only key path provided
    env2 = {"CURRENCY_SERVICE_TLS_KEY_PATH": server_key}
    proc2, _, stderr2 = start_currency_service(env2)
    try:
        assert proc2.poll() is not None, "Service should exit when only key path is provided"
        assert "Both TLS certificate path and private key path must be provided to enable TLS" in stderr2, \
            f"Wrong error message for single key path: {stderr2}"
    finally:
        if proc2.poll() is None:
            proc2.terminate()
            proc2.wait(timeout=5)

def test_ac3_missing_tls_file_fails():
    """AC-3: When either TLS cert or key path points to a non-existent file,
    the service fails to start with the missing file error message."""
    _, server_crt, _, _, _ = generate_test_certs()
    missing_path = "/tmp/this_file_does_not_exist.pem"
    
    # Test missing cert path
    env1 = {
        "CURRENCY_SERVICE_TLS_CERT_PATH": missing_path,
        "CURRENCY_SERVICE_TLS_KEY_PATH": server_crt  # Use existing file for key just to test cert missing
    }
    proc1, _, stderr1 = start_currency_service(env1)
    try:
        assert proc1.poll() is not None, "Service should exit when cert path is missing"
        assert f"TLS file {missing_path} is missing or unreadable" in stderr1, \
            f"Wrong error message for missing cert path: {stderr1}"
    finally:
        if proc1.poll() is None:
            proc1.terminate()
            proc1.wait(timeout=5)
    
    # Test missing key path
    env2 = {
        "CURRENCY_SERVICE_TLS_CERT_PATH": server_crt,
        "CURRENCY_SERVICE_TLS_KEY_PATH": missing_path
    }
    proc2, _, stderr2 = start_currency_service(env2)
    try:
        assert proc2.poll() is not None, "Service should exit when key path is missing"
        assert f"TLS file {missing_path} is missing or unreadable" in stderr2, \
            f"Wrong error message for missing key path: {stderr2}"
    finally:
        if proc2.poll() is None:
            proc2.terminate()
            proc2.wait(timeout=5)

def test_ac4_mtls_enabled_rejects_untrusted_clients():
    """AC-4: When CURRENCY_SERVICE_TLS_CA_CERT_PATH is provided alongside valid cert/key paths,
    the service enables mTLS and rejects connections from clients presenting certificates not signed by the configured CA bundle."""
    ca_crt, server_crt, server_key, client_crt, client_key = generate_test_certs()
    # Generate another client cert signed by a different CA
    tmpdir2 = tempfile.mkdtemp()
    subprocess.run([
        "openssl", "req", "-x509", "-sha256", "-nodes", "-newkey", "rsa:2048", "-days", "1",
        "-keyout", f"{tmpdir2}/bad-ca.key", "-out", f"{tmpdir2}/bad-ca.crt", "-subj", "/CN=bad-ca",
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{tmpdir2}/bad-client.key",
        "-out", f"{tmpdir2}/bad-client.csr", "-subj", "/CN=bad-client",
    ], check=True, capture_output=True)
    subprocess.run([
        "openssl", "x509", "-req", "-in", f"{tmpdir2}/bad-client.csr", "-CA", f"{tmpdir2}/bad-ca.crt",
        "-CAkey", f"{tmpdir2}/bad-ca.key", "-CAcreateserial", "-out", f"{tmpdir2}/bad-client.crt",
        "-days", "1", "-sha256",
    ], check=True, capture_output=True)
    bad_client_crt = f"{tmpdir2}/bad-client.crt"
    bad_client_key = f"{tmpdir2}/bad-client.key"
    
    env = {
        "CURRENCY_SERVICE_TLS_CERT_PATH": server_crt,
        "CURRENCY_SERVICE_TLS_KEY_PATH": server_key,
        "CURRENCY_SERVICE_TLS_CA_CERT_PATH": ca_crt
    }
    
    proc, _, stderr = start_currency_service(env)
    try:
        assert proc.poll() is None, f"Service failed to start with mTLS config: {stderr}"
        
        # Test valid client works
        with open(ca_crt, 'rb') as f:
            ca_data = f.read()
        with open(client_crt, 'rb') as f:
            client_cert_data = f.read()
        with open(client_key, 'rb') as f:
            client_key_data = f.read()
        valid_creds = grpc.ssl_channel_credentials(
            root_certificates=ca_data,
            private_key=client_key_data,
            certificate_chain=client_cert_data
        )
        with grpc.secure_channel(DEFAULT_CURRENCY_SERVICE_ADDR, valid_creds) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            response = stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD", to_currency="EUR", units=100, nanos=0
            ))
            assert response is not None, "Valid mTLS client connection failed"
        
        # Test invalid client is rejected
        invalid_creds = grpc.ssl_channel_credentials(
            root_certificates=ca_data,
            private_key=open(bad_client_key, 'rb').read(),
            certificate_chain=open(bad_client_crt, 'rb').read()
        )
        with grpc.secure_channel(DEFAULT_CURRENCY_SERVICE_ADDR, invalid_creds) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            try:
                stub.Convert(demo_pb2.CurrencyConversionRequest(
                    from_currency="USD", to_currency="EUR", units=100, nanos=0
                ), timeout=2)
                assert False, "Bad mTLS client should be rejected"
            except grpc.RpcError as e:
                assert e.code() in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.UNAVAILABLE), \
                    f"Wrong error code for bad mTLS client: {e.code()}"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

def test_ac5_tls_no_mtls_accepts_any_trusting_client():
    """AC-5: When only cert/key paths are provided (no CA path), the service uses standard TLS
    (no client certificate verification) and accepts connections from any client that trusts the server certificate."""
    ca_crt, server_crt, server_key, _, _ = generate_test_certs()
    env = {
        "CURRENCY_SERVICE_TLS_CERT_PATH": server_crt,
        "CURRENCY_SERVICE_TLS_KEY_PATH": server_key
    }
    
    proc, _, stderr = start_currency_service(env)
    try:
        assert proc.poll() is None, f"Service failed to start with TLS no mTLS config: {stderr}"
        
        # Test client without cert works (as long as it trusts server CA)
        with open(ca_crt, 'rb') as f:
            ca_data = f.read()
        credentials = grpc.ssl_channel_credentials(root_certificates=ca_data)
        with grpc.secure_channel(DEFAULT_CURRENCY_SERVICE_ADDR, credentials) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            response = stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD", to_currency="EUR", units=100, nanos=0
            ))
            assert response is not None, "TLS connection without client cert failed"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

def test_ac6_no_tls_config_falls_back_to_insecure():
    """AC-6: When none of the TLS environment variables are set, the service falls back to
    insecure plaintext gRPC credentials (maintains backward compatibility)."""
    proc, _, stderr = start_currency_service({})
    try:
        assert proc.poll() is None, f"Service failed to start with no TLS config: {stderr}"
        
        # Test insecure connection works
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            response = stub.Convert(demo_pb2.CurrencyConversionRequest(
                from_currency="USD", to_currency="EUR", units=100, nanos=0
            ))
            assert response is not None, "Insecure gRPC connection failed when TLS not configured"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

def test_ac7_mtls_config_without_cert_key_fails():
    """AC-7: When CURRENCY_SERVICE_TLS_CA_CERT_PATH is set without cert/key paths,
    the service fails to start with the mTLS requires server TLS error message."""
    ca_crt, _, _, _, _ = generate_test_certs()
    env = {"CURRENCY_SERVICE_TLS_CA_CERT_PATH": ca_crt}
    
    proc, _, stderr = start_currency_service(env)
    try:
        assert proc.poll() is not None, "Service should exit when CA path provided without cert/key"
        assert "mTLS configuration requires server TLS certificate and key to be provided" in stderr, \
            f"Wrong error message for mTLS without server cert/key: {stderr}"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
