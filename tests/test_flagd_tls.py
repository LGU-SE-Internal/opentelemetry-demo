import os
import pytest
import subprocess
import tempfile
from pathlib import Path
import requests
import grpc
from flagd.proto import service_pb2_grpc, service_pb2

# Test constants
FLAGD_DEFAULT_HTTP_PORT = 8013
FLAGD_DEFAULT_GRPC_PORT = 8014
FLAGD_BINARY = "flagd"  # Adjust if needed for test environment

@pytest.fixture
def temp_certs():
    """Fixture to create temporary self-signed certificates for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Generate dummy test certs (these are just placeholder files for existence checks)
        server_cert = tmpdir / "server.crt"
        server_key = tmpdir / "server.key"
        ca_cert = tmpdir / "ca.crt"
        client_cert = tmpdir / "client.crt"
        client_key = tmpdir / "client.key"
        
        # Write dummy PEM content (valid enough for file existence checks)
        for f in [server_cert, server_key, ca_cert, client_cert, client_key]:
            f.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n")
        
        yield {
            "server_cert": str(server_cert),
            "server_key": str(server_key),
            "ca_cert": str(ca_cert),
            "client_cert": str(client_cert),
            "client_key": str(client_key),
        }

def test_ac1_plaintext_mode_no_tls_vars():
    """AC-1: No TLS env vars set, flagd starts in plaintext mode, accepts unencrypted connections"""
    env = os.environ.copy()
    # Remove any existing TLS env vars
    for var in ["FLAGD_TLS_SERVER_CERT_PATH", "FLAGD_TLS_SERVER_KEY_PATH", "FLAGD_TLS_CA_CERT_PATH", "FLAGD_TLS_CLIENT_AUTH_REQUIRED"]:
        if var in env:
            del env[var]
    
    # Start flagd process
    proc = subprocess.Popen(
        [FLAGD_BINARY, "start", "--config", "src/flagd/demo.flagd.json"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Wait for startup and check process is running
    try:
        proc.wait(timeout=5)
        assert proc.returncode == 0, f"Flagd failed to start: {proc.stderr.read()}"
        
        # Verify plaintext HTTP connection works
        resp = requests.get(f"http://localhost:{FLAGD_DEFAULT_HTTP_PORT}/healthz")
        assert resp.status_code == 200, "Plaintext HTTP connection failed"
        
        # Verify plaintext gRPC connection works
        with grpc.insecure_channel(f"localhost:{FLAGD_DEFAULT_GRPC_PORT}") as channel:
            stub = service_pb2_grpc.ServiceStub(channel)
            resp = stub.Health(service_pb2.HealthRequest())
            assert resp.status == service_pb2.HealthResponse.SERVING, "Plaintext gRPC connection failed"
    finally:
        proc.terminate()
        proc.wait()

def test_ac2_server_tls_mode_valid_certs(temp_certs):
    """AC-2: Server cert and key set, flagd starts in server TLS mode, rejects unencrypted connections"""
    env = os.environ.copy()
    env["FLAGD_TLS_SERVER_CERT_PATH"] = temp_certs["server_cert"]
    env["FLAGD_TLS_SERVER_KEY_PATH"] = temp_certs["server_key"]
    # Remove other TLS vars
    for var in ["FLAGD_TLS_CA_CERT_PATH", "FLAGD_TLS_CLIENT_AUTH_REQUIRED"]:
        if var in env:
            del env[var]
    
    proc = subprocess.Popen(
        [FLAGD_BINARY, "start", "--config", "src/flagd/demo.flagd.json"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        proc.wait(timeout=5)
        assert proc.returncode == 0, f"Flagd failed to start with server TLS: {proc.stderr.read()}"
        
        # Verify unencrypted HTTP connection is rejected
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get(f"http://localhost:{FLAGD_DEFAULT_HTTP_PORT}/healthz", timeout=2)
        
        # Verify unencrypted gRPC connection is rejected
        with grpc.insecure_channel(f"localhost:{FLAGD_DEFAULT_GRPC_PORT}") as channel:
            stub = service_pb2_grpc.ServiceStub(channel)
            with pytest.raises(grpc.RpcError):
                stub.Health(service_pb2.HealthRequest(), timeout=2)
        
        # Verify TLS encrypted connection works (skip actual cert validation for test)
        resp = requests.get(f"https://localhost:{FLAGD_DEFAULT_HTTP_PORT}/healthz", verify=False, timeout=2)
        assert resp.status_code == 200, "TLS HTTP connection failed"
    finally:
        proc.terminate()
        proc.wait()

def test_ac3_mtls_mode_valid_config(temp_certs):
    """AC-3: All TLS vars set with client auth required, flagd starts in mTLS mode, rejects invalid client certs"""
    env = os.environ.copy()
    env["FLAGD_TLS_SERVER_CERT_PATH"] = temp_certs["server_cert"]
    env["FLAGD_TLS_SERVER_KEY_PATH"] = temp_certs["server_key"]
    env["FLAGD_TLS_CA_CERT_PATH"] = temp_certs["ca_cert"]
    env["FLAGD_TLS_CLIENT_AUTH_REQUIRED"] = "true"
    
    proc = subprocess.Popen(
        [FLAGD_BINARY, "start", "--config", "src/flagd/demo.flagd.json"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        proc.wait(timeout=5)
        assert proc.returncode == 0, f"Flagd failed to start with mTLS: {proc.stderr.read()}"
        
        # Verify connection without client cert is rejected
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(f"https://localhost:{FLAGD_DEFAULT_HTTP_PORT}/healthz", verify=False, timeout=2)
        
        # Verify connection with invalid client cert is rejected
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(
                f"https://localhost:{FLAGD_DEFAULT_HTTP_PORT}/healthz",
                cert=("/invalid/path/cert.crt", "/invalid/path/key.key"),
                verify=False,
                timeout=2
            )
        
        # Verify connection with valid client cert works
        resp = requests.get(
            f"https://localhost:{FLAGD_DEFAULT_HTTP_PORT}/healthz",
            cert=(temp_certs["client_cert"], temp_certs["client_key"]),
            verify=False,
            timeout=2
        )
        assert resp.status_code == 200, "mTLS connection with valid client cert failed"
    finally:
        proc.terminate()
        proc.wait()

def test_ac4_missing_server_key_fails_start(temp_certs):
    """AC-4: Server cert set but key missing, flagd fails to start with missing private key error"""
    env = os.environ.copy()
    env["FLAGD_TLS_SERVER_CERT_PATH"] = temp_certs["server_cert"]
    # Don't set FLAGD_TLS_SERVER_KEY_PATH
    for var in ["FLAGD_TLS_SERVER_KEY_PATH", "FLAGD_TLS_CA_CERT_PATH", "FLAGD_TLS_CLIENT_AUTH_REQUIRED"]:
        if var in env:
            del env[var]
    
    proc = subprocess.Popen(
        [FLAGD_BINARY, "start", "--config", "src/flagd/demo.flagd.json"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    proc.wait(timeout=10)
    assert proc.returncode != 0, "Flagd started successfully with missing TLS private key"
    assert "missing TLS private key" in proc.stderr.read().lower(), "Expected error message about missing private key not found"

def test_ac5_mtls_missing_ca_fails_start(temp_certs):
    """AC-5: Client auth required but CA cert missing, flagd fails to start with missing CA bundle error"""
    env = os.environ.copy()
    env["FLAGD_TLS_SERVER_CERT_PATH"] = temp_certs["server_cert"]
    env["FLAGD_TLS_SERVER_KEY_PATH"] = temp_certs["server_key"]
    env["FLAGD_TLS_CLIENT_AUTH_REQUIRED"] = "true"
    # Don't set FLAGD_TLS_CA_CERT_PATH
    if "FLAGD_TLS_CA_CERT_PATH" in env:
        del env["FLAGD_TLS_CA_CERT_PATH"]
    
    proc = subprocess.Popen(
        [FLAGD_BINARY, "start", "--config", "src/flagd/demo.flagd.json"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    proc.wait(timeout=10)
    assert proc.returncode != 0, "Flagd started successfully with mTLS enabled but missing CA cert"
    assert "missing ca certificate" in proc.stderr.read().lower(), "Expected error message about missing CA bundle not found"

def test_ac7_backward_compatibility_no_tls_vars():
    """AC-7: Existing deployments without TLS vars work exactly as before"""
    env = os.environ.copy()
    # Remove all TLS env vars
    for var in ["FLAGD_TLS_SERVER_CERT_PATH", "FLAGD_TLS_SERVER_KEY_PATH", "FLAGD_TLS_CA_CERT_PATH", "FLAGD_TLS_CLIENT_AUTH_REQUIRED"]:
        if var in env:
            del env[var]
    
    # Capture startup command arguments to ensure no TLS flags are added
    proc = subprocess.Popen(
        ["bash", "-c", f"echo $@; {FLAGD_BINARY} start --config src/flagd/demo.flagd.json --help 2>&1 | grep -E '(--cert|--key|--client-ca)' || echo 'no_tls_flags'"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    stdout, _ = proc.communicate(timeout=10)
    # Verify no TLS flags are present in startup args by default
    assert "no_tls_flags" in stdout, "TLS flags are being added by default when no TLS env vars are set"
