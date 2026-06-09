#!/usr/bin/env python3
"""
Integration tests for Product Reviews Service TLS/mTLS implementation.
All tests correspond to Acceptance Criteria from the spec.
"""
import os
import time
import grpc
import subprocess
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "product-reviews"))
import demo_pb2
import demo_pb2_grpc

PRODUCT_REVIEWS_PORT = 35500
TEST_CERTS_DIR = Path(__file__).parent / "test_certs_product_reviews"
TEST_CERTS_DIR.mkdir(exist_ok=True)

# Test cert paths
SERVER_CERT = TEST_CERTS_DIR / "server.crt"
SERVER_KEY = TEST_CERTS_DIR / "server.key"
CA_CERT = TEST_CERTS_DIR / "ca.crt"
CLIENT_CERT = TEST_CERTS_DIR / "client.crt"
CLIENT_KEY = TEST_CERTS_DIR / "client.key"
INVALID_CERT = TEST_CERTS_DIR / "invalid.crt"

# Environment variable names from spec
GRPC_TLS_CERT_ENV = "PRODUCT_REVIEWS_GRPC_TLS_CERT_PATH"
GRPC_TLS_KEY_ENV = "PRODUCT_REVIEWS_GRPC_TLS_KEY_PATH"
GRPC_MTLS_CA_ENV = "PRODUCT_REVIEWS_GRPC_MTLS_CA_CERT_PATH"
DB_TLS_MODE_ENV = "PRODUCT_REVIEWS_DB_TLS_MODE"
DB_TLS_CA_ENV = "PRODUCT_REVIEWS_DB_TLS_CA_CERT_PATH"
DB_TLS_CLIENT_CERT_ENV = "PRODUCT_REVIEWS_DB_TLS_CLIENT_CERT_PATH"
DB_TLS_CLIENT_KEY_ENV = "PRODUCT_REVIEWS_DB_TLS_CLIENT_KEY_PATH"

def generate_test_certs():
    """Generate self-signed test certificates for TLS/mTLS tests"""
    for f in TEST_CERTS_DIR.glob("*"):
        f.unlink()

    # Generate CA cert
    subprocess.run([
        "openssl", "req", "-x509", "-sha256", "-newkey", "rsa:4096",
        "-days", "1", "-nodes", "-keyout", str(CA_CERT),
        "-out", str(CA_CERT), "-subj", "/CN=Test CA",
        "-addext", "keyUsage = critical, keyCertSign, cRLSign",
        "-addext", "basicConstraints = critical, CA:TRUE"
    ], check=True, capture_output=True)

    # Generate server cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-nodes",
        "-keyout", str(SERVER_KEY), "-out", str(TEST_CERTS_DIR / "server.csr"),
        "-subj", "/CN=localhost"
    ], check=True, capture_output=True)

    subprocess.run([
        "openssl", "x509", "-req", "-in", str(TEST_CERTS_DIR / "server.csr"),
        "-CA", str(CA_CERT), "-CAkey", str(CA_CERT), "-CAcreateserial",
        "-out", str(SERVER_CERT), "-days", "1", "-sha256",
        "-extfile", "/dev/stdin"
    ], input="subjectAltName = DNS:localhost, IP:127.0.0.1", check=True, capture_output=True, text=True)

    # Generate client cert
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:4096", "-nodes",
        "-keyout", str(CLIENT_KEY), "-out", str(TEST_CERTS_DIR / "client.csr"),
        "-subj", "/CN=test-client"
    ], check=True, capture_output=True)

    subprocess.run([
        "openssl", "x509", "-req", "-in", str(TEST_CERTS_DIR / "client.csr"),
        "-CA", str(CA_CERT), "-CAkey", str(CA_CERT), "-CAcreateserial",
        "-out", str(CLIENT_CERT), "-days", "1", "-sha256"
    ], check=True, capture_output=True)

    # Create invalid cert
    with open(INVALID_CERT, "w") as f:
        f.write("invalid pem data")

@pytest.fixture(scope="module", autouse=True)
def setup_test_certs():
    generate_test_certs()
    yield
    for f in TEST_CERTS_DIR.glob("*"):
        f.unlink()
    TEST_CERTS_DIR.rmdir()

def run_product_reviews_service(env_vars, timeout=5):
    """Run product reviews service with given env vars, return process and output"""
    env = os.environ.copy()
    env.update(env_vars)
    cmd = [sys.executable, "../src/product-reviews/product_reviews_server.py"]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(Path(__file__).parent))
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        return proc, None, None
    proc.wait()
    return proc, stdout, stderr

def test_ac1_plaintext_grpc_no_tls_config():
    """AC-1: No TLS env vars set, service starts normally and accepts plaintext gRPC connections"""
    proc, _, _ = run_product_reviews_service({})
    try:
        time.sleep(2)
        channel = grpc.insecure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}")
        stub = demo_pb2_grpc.ProductReviewServiceStub(channel)
        response = stub.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"))
        assert response is not None
    finally:
        proc.terminate()
        proc.wait()

def test_ac2_tls_enabled_only_accepts_encrypted_connections():
    """AC-2: Valid TLS cert/key provided, service only accepts TLS 1.2+ connections, rejects plaintext"""
    env = {
        GRPC_TLS_CERT_ENV: str(SERVER_CERT),
        GRPC_TLS_KEY_ENV: str(SERVER_KEY)
    }
    proc, _, _ = run_product_reviews_service(env)
    try:
        time.sleep(2)
        # Plaintext connection should fail
        with pytest.raises(grpc.RpcError) as excinfo:
            channel = grpc.insecure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}")
            stub = demo_pb2_grpc.ProductReviewServiceStub(channel)
            stub.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"), timeout=2)
        assert excinfo.value.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED]

        # TLS connection should work
        with open(SERVER_CERT, 'rb') as f:
            root_certs = f.read()
        credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
        secure_channel = grpc.secure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}", credentials)
        secure_stub = demo_pb2_grpc.ProductReviewServiceStub(secure_channel)
        response = secure_stub.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"), timeout=2)
        assert response is not None
    finally:
        proc.terminate()
        proc.wait()

def test_ac3_mtls_enabled_enforces_client_certs():
    """AC-3: mTLS CA path set, service only accepts connections with valid client cert"""
    env = {
        GRPC_TLS_CERT_ENV: str(SERVER_CERT),
        GRPC_TLS_KEY_ENV: str(SERVER_KEY),
        GRPC_MTLS_CA_ENV: str(CA_CERT)
    }
    proc, _, _ = run_product_reviews_service(env)
    try:
        time.sleep(2)
        # Connection without client cert should fail with UNAUTHENTICATED
        with open(SERVER_CERT, 'rb') as f:
            root_certs = f.read()
        credentials_no_client = grpc.ssl_channel_credentials(root_certificates=root_certs)
        channel_no_client = grpc.secure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}", credentials_no_client)
        stub_no_client = demo_pb2_grpc.ProductReviewServiceStub(channel_no_client)
        with pytest.raises(grpc.RpcError) as excinfo:
            stub_no_client.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"), timeout=2)
        assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED

        # Connection with valid client cert should work
        with open(CLIENT_CERT, 'rb') as f:
            client_cert = f.read()
        with open(CLIENT_KEY, 'rb') as f:
            client_key = f.read()
        credentials_with_client = grpc.ssl_channel_credentials(
            root_certificates=root_certs,
            private_key=client_key,
            certificate_chain=client_cert
        )
        channel_with_client = grpc.secure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}", credentials_with_client)
        stub_with_client = demo_pb2_grpc.ProductReviewServiceStub(channel_with_client)
        response = stub_with_client.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"), timeout=2)
        assert response is not None
    finally:
        proc.terminate()
        proc.wait()

def test_ac4_tls_cert_file_missing_fails_startup():
    """AC-4: TLS cert path points to non-existent file, service fails to start with correct error"""
    env = {
        GRPC_TLS_CERT_ENV: "/nonexistent/cert.crt",
        GRPC_TLS_KEY_ENV: str(SERVER_KEY)
    }
    proc, stdout, stderr = run_product_reviews_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "TLS configuration error:" in output
    assert "/nonexistent/cert.crt" in output
    assert "not found" in output.lower() or "unreadable" in output.lower()

def test_ac5_malformed_cert_fails_startup():
    """AC-5: Malformed TLS cert provided, service fails to start with explicit error"""
    env = {
        GRPC_TLS_CERT_ENV: str(INVALID_CERT),
        GRPC_TLS_KEY_ENV: str(SERVER_KEY)
    }
    proc, stdout, stderr = run_product_reviews_service(env)
    assert proc.returncode != 0
    output = (stdout or "") + (stderr or "")
    assert "TLS configuration error:" in output
    assert str(INVALID_CERT) in output
    assert "invalid" in output.lower() or "malformed" in output.lower()

def test_ac6_db_tls_mode_require_enforces_tls_connection():
    """AC-6: DB TLS mode set to require, client connects using TLS"""
    env = {
        DB_TLS_MODE_ENV: "require"
    }
    proc, stdout, stderr = run_product_reviews_service(env)
    try:
        time.sleep(2)
        # Verify service starts (assuming test DB supports TLS)
        channel = grpc.insecure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}")
        stub = demo_pb2_grpc.ProductReviewServiceStub(channel)
        response = stub.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"), timeout=2)
        assert response is not None
    finally:
        proc.terminate()
        proc.wait()

def test_ac7_db_tls_verify_full_validates_server_cert():
    """AC-7: DB TLS mode set to verify-full with CA cert, validates server certificate"""
    env = {
        DB_TLS_MODE_ENV: "verify-full",
        DB_TLS_CA_ENV: str(CA_CERT)
    }
    proc, stdout, stderr = run_product_reviews_service(env)
    try:
        time.sleep(2)
        channel = grpc.insecure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}")
        stub = demo_pb2_grpc.ProductReviewServiceStub(channel)
        response = stub.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"), timeout=2)
        assert response is not None
    finally:
        proc.terminate()
        proc.wait()

def test_ac8_db_mtls_client_cert_presented():
    """AC-8: All DB TLS client cert parameters set, client presents cert for mTLS"""
    env = {
        DB_TLS_MODE_ENV: "verify-full",
        DB_TLS_CA_ENV: str(CA_CERT),
        DB_TLS_CLIENT_CERT_ENV: str(CLIENT_CERT),
        DB_TLS_CLIENT_KEY_ENV: str(CLIENT_KEY)
    }
    proc, stdout, stderr = run_product_reviews_service(env)
    try:
        time.sleep(2)
        channel = grpc.insecure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}")
        stub = demo_pb2_grpc.ProductReviewServiceStub(channel)
        response = stub.ListReviews(demo_pb2.ListReviewsRequest(product_id="test"), timeout=2)
        assert response is not None
    finally:
        proc.terminate()
        proc.wait()

def test_ac9_existing_functionality_preserved_without_tls():
    """AC-9: Existing functionality unchanged when TLS is not enabled"""
    # Test existing endpoints work as expected
    proc, _, _ = run_product_reviews_service({})
    try:
        time.sleep(2)
        channel = grpc.insecure_channel(f"localhost:{PRODUCT_REVIEWS_PORT}")
        stub = demo_pb2_grpc.ProductReviewServiceStub(channel)
        
        # Test ListReviews
        list_resp = stub.ListReviews(demo_pb2.ListReviewsRequest(product_id="test-product"), timeout=2)
        assert hasattr(list_resp, 'reviews')
        
        # Test CreateReview
        create_resp = stub.CreateReview(demo_pb2.CreateReviewRequest(
            product_id="test-product",
            user_id="test-user",
            content="Great product!",
            rating=5
        ), timeout=2)
        assert create_resp.review.id is not None
        assert create_resp.review.rating == 5
    finally:
        proc.terminate()
        proc.wait()
EOF && chmod +x ./test/test_product_reviews_tls_ac.py && ls -la ./test/test_product_reviews_tls_ac.py
