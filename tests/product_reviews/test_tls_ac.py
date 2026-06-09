import os
import pytest
import grpc
import asyncio
from pathlib import Path
from src.product_reviews.product_reviews_server import serve
from src.product_reviews.database import get_db_connection
from grpc import aio

# Test constants
TEST_PORT = 50052
TEST_GRPC_CERT = Path("/tmp/test_grpc_cert.pem")
TEST_GRPC_KEY = Path("/tmp/test_grpc_key.pem")
TEST_GRPC_CA_CERT = Path("/tmp/test_grpc_ca.pem")
TEST_DB_CA_CERT = Path("/tmp/test_db_ca.pem")
TEST_DB_CLIENT_CERT = Path("/tmp/test_db_client_cert.pem")
TEST_DB_CLIENT_KEY = Path("/tmp/test_db_client_key.pem")

@pytest.fixture(autouse=True)
def clean_env():
    """Clear all TLS related env vars before each test"""
    for var in list(os.environ.keys()):
        if var.startswith("PRODUCT_REVIEWS_") and ("TLS" in var or "MTLS" in var):
            del os.environ[var]
    # Remove any test cert files
    for f in [TEST_GRPC_CERT, TEST_GRPC_KEY, TEST_GRPC_CA_CERT, TEST_DB_CA_CERT, TEST_DB_CLIENT_CERT, TEST_DB_CLIENT_KEY]:
        if f.exists():
            f.unlink(missing_ok=True)
    yield

@pytest.mark.asyncio
async def test_ac1_plaintext_default_no_tls_vars():
    """AC-1: When no TLS env vars set, service starts normally and accepts plaintext connections"""
    # Start server in background
    server = await serve(TEST_PORT, run_background=True)
    try:
        # Test plaintext connection succeeds
        async with aio.insecure_channel(f"localhost:{TEST_PORT}") as channel:
            try:
                await asyncio.wait_for(channel.channel_ready(), timeout=3)
                connection_succeeded = True
            except (grpc.aio.AioRpcError, asyncio.TimeoutError):
                connection_succeeded = False
        assert connection_succeeded, "Plaintext connection should succeed when TLS is not configured"
    finally:
        await server.stop(5)

@pytest.mark.asyncio
async def test_ac2_grpc_tls_requires_encrypted_connection():
    """AC-2: When gRPC TLS cert/key are set, service only accepts TLS 1.2+ connections, rejects plaintext"""
    # Create dummy valid cert/key files (content doesn't matter for config test, but exist)
    TEST_GRPC_CERT.write_text("dummy cert content")
    TEST_GRPC_KEY.write_text("dummy key content")
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_CERT_PATH"] = str(TEST_GRPC_CERT)
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_KEY_PATH"] = str(TEST_GRPC_KEY)
    
    # Start server
    server = await serve(TEST_PORT, run_background=True)
    try:
        # Test plaintext connection fails
        async with aio.insecure_channel(f"localhost:{TEST_PORT}") as channel:
            try:
                await asyncio.wait_for(channel.channel_ready(), timeout=2)
                plaintext_succeeded = True
            except (grpc.aio.AioRpcError, asyncio.TimeoutError):
                plaintext_succeeded = False
        assert not plaintext_succeeded, "Plaintext connection should be rejected when TLS is enabled"
        
        # Test TLS connection succeeds (with dummy credentials for test)
        creds = grpc.ssl_channel_credentials(root_certificates=TEST_GRPC_CERT.read_bytes())
        async with aio.secure_channel(f"localhost:{TEST_PORT}", creds) as channel:
            try:
                await asyncio.wait_for(channel.channel_ready(), timeout=2)
                tls_succeeded = True
            except (grpc.aio.AioRpcError, asyncio.TimeoutError):
                tls_succeeded = False
        assert tls_succeeded, "TLS connection should succeed when TLS is enabled"
    finally:
        await server.stop(5)

@pytest.mark.asyncio
async def test_ac3_mtls_enforces_client_cert():
    """AC-3: When mTLS CA cert set, service only accepts connections with valid client cert signed by CA"""
    TEST_GRPC_CERT.write_text("dummy cert content")
    TEST_GRPC_KEY.write_text("dummy key content")
    TEST_GRPC_CA_CERT.write_text("dummy CA cert content")
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_CERT_PATH"] = str(TEST_GRPC_CERT)
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_KEY_PATH"] = str(TEST_GRPC_KEY)
    os.environ["PRODUCT_REVIEWS_GRPC_MTLS_CA_CERT_PATH"] = str(TEST_GRPC_CA_CERT)
    
    server = await serve(TEST_PORT, run_background=True)
    try:
        # Test connection without client cert returns UNAUTHENTICATED
        creds_no_client = grpc.ssl_channel_credentials(root_certificates=TEST_GRPC_CERT.read_bytes())
        async with aio.secure_channel(f"localhost:{TEST_PORT}", creds_no_client) as channel:
            try:
                # Make a dummy request
                stub = channel.unary_unary("/demo.ProductReviewsService/ListReviews")
                await stub(b"", timeout=2)
                assert False, "Should have failed with UNAUTHENTICATED"
            except grpc.aio.AioRpcError as e:
                assert e.code() == grpc.StatusCode.UNAUTHENTICATED, f"Expected UNAUTHENTICATED, got {e.code()}"
    finally:
        await server.stop(5)

def test_ac4_missing_cert_file_fails_startup():
    """AC-4: When gRPC TLS cert path points to non-existent file, service fails to start with explicit error"""
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_CERT_PATH"] = "/non/existent/cert.pem"
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_KEY_PATH"] = str(TEST_GRPC_KEY)
    TEST_GRPC_KEY.write_text("dummy key")
    
    with pytest.raises(Exception) as exc_info:
        asyncio.run(serve(TEST_PORT))
    assert "TLS configuration error" in str(exc_info.value)
    assert "/non/existent/cert.pem" in str(exc_info.value)
    assert "missing or unreadable" in str(exc_info.value).lower()

def test_ac5_malformed_cert_file_fails_startup():
    """AC-5: When gRPC TLS cert is malformed PEM, service fails to start with explicit error"""
    TEST_GRPC_CERT.write_text("NOT A VALID PEM CERTIFICATE")
    TEST_GRPC_KEY.write_text("NOT A VALID PEM KEY")
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_CERT_PATH"] = str(TEST_GRPC_CERT)
    os.environ["PRODUCT_REVIEWS_GRPC_TLS_KEY_PATH"] = str(TEST_GRPC_KEY)
    
    with pytest.raises(Exception) as exc_info:
        asyncio.run(serve(TEST_PORT))
    assert "TLS configuration error" in str(exc_info.value)
    assert "invalid certificate content" in str(exc_info.value).lower()

@pytest.mark.asyncio
async def test_ac6_db_tls_require_mode():
    """AC-6: When DB TLS mode is require, client connects using TLS, fails if DB doesn't support TLS"""
    os.environ["PRODUCT_REVIEWS_DB_TLS_MODE"] = "require"
    # Mock DB that doesn't support TLS, connection should fail
    with pytest.raises(Exception) as exc_info:
        conn = await get_db_connection()
        await conn.close()
    assert "TLS required but not supported by database" in str(exc_info.value).lower()

@pytest.mark.asyncio
async def test_ac7_db_tls_verify_full_validates_ca():
    """AC-7: When DB TLS mode is verify-full and CA cert provided, client validates server cert against CA"""
    TEST_DB_CA_CERT.write_text("dummy CA cert")
    os.environ["PRODUCT_REVIEWS_DB_TLS_MODE"] = "verify-full"
    os.environ["PRODUCT_REVIEWS_DB_TLS_CA_CERT_PATH"] = str(TEST_DB_CA_CERT)
    
    # Connection to DB with self-signed cert not matching CA should fail
    with pytest.raises(Exception) as exc_info:
        conn = await get_db_connection()
        await conn.close()
    assert "certificate verify failed" in str(exc_info.value).lower()

@pytest.mark.asyncio
async def test_ac8_db_mtls_client_cert_presented():
    """AC-8: When DB client cert/key/CA are set, client presents client cert for mTLS authentication"""
    TEST_DB_CA_CERT.write_text("dummy CA")
    TEST_DB_CLIENT_CERT.write_text("dummy client cert")
    TEST_DB_CLIENT_KEY.write_text("dummy client key")
    os.environ["PRODUCT_REVIEWS_DB_TLS_MODE"] = "verify-full"
    os.environ["PRODUCT_REVIEWS_DB_TLS_CA_CERT_PATH"] = str(TEST_DB_CA_CERT)
    os.environ["PRODUCT_REVIEWS_DB_TLS_CLIENT_CERT_PATH"] = str(TEST_DB_CLIENT_CERT)
    os.environ["PRODUCT_REVIEWS_DB_TLS_CLIENT_KEY_PATH"] = str(TEST_DB_CLIENT_KEY)
    
    # Check that connection attempts include client cert
    conn = await get_db_connection()
    assert conn._connection_parameters.get("sslcert") == str(TEST_DB_CLIENT_CERT)
    assert conn._connection_parameters.get("sslkey") == str(TEST_DB_CLIENT_KEY)
    await conn.close()

@pytest.mark.asyncio
async def test_ac9_existing_functionality_unchanged_no_tls():
    """AC-9: All existing functionality remains unchanged when TLS is not enabled"""
    # Start server without TLS
    server = await serve(TEST_PORT, run_background=True)
    try:
        # Test existing ListReviews endpoint returns expected response
        async with aio.insecure_channel(f"localhost:{TEST_PORT}") as channel:
            stub = channel.unary_unary(
                "/demo.ProductReviewsService/ListReviews",
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x
            )
            # Send valid request for product 1 (encoded protobuf)
            response = await stub(b'\x08\x01', timeout=2)
            assert response is not None
            # Verify response format matches existing schema
            assert len(response) > 0
    finally:
        await server.stop(5)
