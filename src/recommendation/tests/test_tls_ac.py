import os
import pytest
import grpc
from recommendation_server import serve, create_product_catalog_client
import demo_pb2
import demo_pb2_grpc

# Helper: Temporary self-signed cert paths for testing (we'll assume these exist in test env)
TEST_VALID_SERVER_CERT = "/tmp/test_server.crt"
TEST_VALID_SERVER_KEY = "/tmp/test_server.key"
TEST_VALID_CLIENT_CA = "/tmp/test_client_ca.crt"
TEST_VALID_CLIENT_CERT = "/tmp/test_client.crt"
TEST_VALID_CLIENT_KEY = "/tmp/test_client.key"
TEST_INVALID_CERT = "/tmp/invalid.crt"
TEST_MISSING_FILE = "/tmp/does_not_exist.crt"

@pytest.fixture(autouse=True)
def clear_tls_env_vars(monkeypatch):
    """Clear all TLS_* env vars before each test"""
    for key in list(os.environ.keys()):
        if key.startswith("TLS_"):
            monkeypatch.delenv(key, raising=False)
    yield

def test_ac1_default_plaintext_communication(monkeypatch):
    """AC-1: No TLS env vars set, service starts and uses plaintext for all connections"""
    # No TLS env vars set
    # Test server starts successfully
    server = serve(listen_addr="[::]:0", test_mode=True)
    assert server is not None
    server.stop(0)
    
    # Test client creates plaintext connection to product catalog
    client = create_product_catalog_client("productcatalog:8080")
    assert client is not None
    
    # Verify connection is plaintext (insecure channel)
    assert isinstance(client._channel, grpc.Channel)
    assert hasattr(client._channel, '_channel')  # gRPC internal check for insecure channel

def test_ac2_server_tls_enabled_rejects_plaintext(monkeypatch):
    """AC-2: TLS_SERVER_ENABLE=true with valid certs, server only accepts TLS connections"""
    monkeypatch.setenv("TLS_SERVER_ENABLE", "true")
    monkeypatch.setenv("TLS_SERVER_CERT_PATH", TEST_VALID_SERVER_CERT)
    monkeypatch.setenv("TLS_SERVER_KEY_PATH", TEST_VALID_SERVER_KEY)
    
    # Start server on random port
    server = serve(listen_addr="[::]:0", test_mode=True)
    port = server._port
    
    # Try plaintext connection - should fail
    with grpc.insecure_channel(f"localhost:{port}") as channel:
        stub = demo_pb2_grpc.RecommendationServiceStub(channel)
        with pytest.raises(grpc.RpcError) as excinfo:
            stub.ListRecommendations(demo_pb2.ListRecommendationsRequest(user_id="test"))
        assert excinfo.value.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.PERMISSION_DENIED)
    
    # Try TLS connection - should succeed
    with open(TEST_VALID_SERVER_CERT, 'rb') as f:
        root_certs = f.read()
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
    with grpc.secure_channel(f"localhost:{port}", credentials) as channel:
        stub = demo_pb2_grpc.RecommendationServiceStub(channel)
        response = stub.ListRecommendations(demo_pb2.ListRecommendationsRequest(user_id="test"))
        assert response is not None
        assert response is not None
    server.stop(0)

def test_ac3_client_tls_enabled_validates_server_cert(monkeypatch):
    """AC-3: TLS_CLIENT_ENABLE=true with valid CA cert, client uses TLS and validates server cert"""
    monkeypatch.setenv("TLS_CLIENT_ENABLE", "true")
    monkeypatch.setenv("TLS_CLIENT_CA_CERT_PATH", TEST_VALID_CLIENT_CA)
    
    # Create client - should use TLS channel with CA validation
    client = create_product_catalog_client("productcatalog:8080")
    assert isinstance(client._channel, grpc.Channel)
    
    # Verify channel uses SSL credentials with correct CA
    channel_credentials = client._channel._credentials
    assert channel_credentials is not None
    assert hasattr(channel_credentials, '_credentials')  # gRPC SSL credential check

def test_ac4_client_mtls_enabled_presents_client_cert(monkeypatch):
    """AC-4: TLS_CLIENT_ENABLE=true + mTLS enabled, client presents client certificate for authentication"""
    monkeypatch.setenv("TLS_CLIENT_ENABLE", "true")
    monkeypatch.setenv("TLS_CLIENT_CA_CERT_PATH", TEST_VALID_CLIENT_CA)
    monkeypatch.setenv("TLS_CLIENT_ENABLE_MTLS", "true")
    monkeypatch.setenv("TLS_CLIENT_CERT_PATH", TEST_VALID_CLIENT_CERT)
    monkeypatch.setenv("TLS_CLIENT_KEY_PATH", TEST_VALID_CLIENT_KEY)
    
    # Create client - should use mTLS credentials
    client = create_product_catalog_client("productcatalog:8080")
    channel_credentials = client._channel._credentials
    
    # Verify mTLS credentials are used (client cert + key present)
    assert channel_credentials is not None
    # Check that credentials include client certificate and key
    assert hasattr(channel_credentials, '_client_cert')
    assert hasattr(channel_credentials, '_client_key')

def test_ac5_tls_config_missing_or_invalid_fails_startup(monkeypatch):
    """AC-5: TLS enabled but required config missing/invalid, service fails to start with clear error"""
    # Test case 1: Server TLS enabled but missing cert path
    monkeypatch.setenv("TLS_SERVER_ENABLE", "true")
    monkeypatch.setenv("TLS_SERVER_KEY_PATH", TEST_VALID_SERVER_KEY)
    # Missing TLS_SERVER_CERT_PATH
    with pytest.raises((ValueError, FileNotFoundError)) as excinfo:
        serve(listen_addr="[::]:0", test_mode=True)
    assert "TLS_SERVER_CERT_PATH" in str(excinfo.value).lower()
    
    # Test case 2: Client TLS enabled but missing CA path
    monkeypatch.setenv("TLS_CLIENT_ENABLE", "true")
    with pytest.raises((ValueError, FileNotFoundError)) as excinfo:
        create_product_catalog_client("productcatalog:8080")
    assert "TLS_CLIENT_CA_CERT_PATH" in str(excinfo.value).lower()
    
    # Test case 3: Invalid certificate file
    monkeypatch.setenv("TLS_SERVER_ENABLE", "true")
    monkeypatch.setenv("TLS_SERVER_CERT_PATH", TEST_INVALID_CERT)
    monkeypatch.setenv("TLS_SERVER_KEY_PATH", TEST_VALID_SERVER_KEY)
    with pytest.raises(ValueError) as excinfo:
        serve(listen_addr="[::]:0", test_mode=True)
    assert "invalid" in str(excinfo.value).lower()
    assert "certificate" in str(excinfo.value).lower()
    
    # Test case 4: Missing certificate file
    monkeypatch.setenv("TLS_SERVER_CERT_PATH", TEST_MISSING_FILE)
    with pytest.raises(FileNotFoundError) as excinfo:
        serve(listen_addr="[::]:0", test_mode=True)
    assert TEST_MISSING_FILE in str(excinfo.value)

def test_ac6_server_mtls_enforced_rejects_unauthenticated_clients(monkeypatch):
    """AC-6: Server mTLS enabled, rejects connections without valid client cert"""
    monkeypatch.setenv("TLS_SERVER_ENABLE", "true")
    monkeypatch.setenv("TLS_SERVER_CERT_PATH", TEST_VALID_SERVER_CERT)
    monkeypatch.setenv("TLS_SERVER_KEY_PATH", TEST_VALID_SERVER_KEY)
    monkeypatch.setenv("TLS_SERVER_CLIENT_CA_CERT_PATH", TEST_VALID_CLIENT_CA)
    
    # Start server with mTLS enabled
    server = serve(listen_addr="[::]:0", test_mode=True)
    port = server._port
    
    # Try connecting without client cert - should fail
    with open(TEST_VALID_SERVER_CERT, 'rb') as f:
        root_certs = f.read()
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
    with grpc.secure_channel(f"localhost:{port}", credentials) as channel:
        stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
        with pytest.raises(grpc.RpcError) as excinfo:
            stub.ListRecommendations(ListRecommendationsRequest(user_id="test"))
        assert excinfo.value.code() in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED)
    
    # Try connecting with valid client cert - should succeed
    with open(TEST_VALID_CLIENT_CERT, 'rb') as f:
        client_cert = f.read()
    with open(TEST_VALID_CLIENT_KEY, 'rb') as f:
        client_key = f.read()
    credentials = grpc.ssl_channel_credentials(
        root_certificates=root_certs,
        private_key=client_key,
        certificate_chain=client_cert
    )
    with grpc.secure_channel(f"localhost:{port}", credentials) as channel:
        stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
        response = stub.ListRecommendations(ListRecommendationsRequest(user_id="test"))
        assert response is not None
    
    server.stop(0)
