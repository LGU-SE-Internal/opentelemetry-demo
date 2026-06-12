import os
import pytest
import requests
import grpc
from kubernetes import client, config

# Import quote service gRPC stubs (existing)
from pb.demo.quote.v1 import quote_pb2, quote_pb2_grpc

# Env var names from spec
HTTP_TLS_ENABLED = "QUOTESERVICE_HTTP_TLS_ENABLED"
HTTP_TLS_CERT_PATH = "QUOTESERVICE_HTTP_TLS_CERT_PATH"
HTTP_TLS_KEY_PATH = "QUOTESERVICE_HTTP_TLS_KEY_PATH"
HTTP_TLS_CLIENT_CA_PATH = "QUOTESERVICE_HTTP_TLS_CLIENT_CA_PATH"
GRPC_TLS_ENABLED = "QUOTESERVICE_GRPC_TLS_ENABLED"
GRPC_TLS_CERT_PATH = "QUOTESERVICE_GRPC_TLS_CERT_PATH"
GRPC_TLS_KEY_PATH = "QUOTESERVICE_GRPC_TLS_KEY_PATH"
GRPC_TLS_CLIENT_CA_PATH = "QUOTESERVICE_GRPC_TLS_CLIENT_CA_PATH"

SERVICE_HTTP_PORT = 8080
SERVICE_GRPC_PORT = 8081
BASE_HTTP_URL = "http://localhost:{}".format(SERVICE_HTTP_PORT)
BASE_HTTPS_URL = "https://localhost:{}".format(SERVICE_HTTP_PORT)
LIVENESS_PATH = "/health/live"
READINESS_PATH = "/health/ready"

# Test cert paths (used for test runs, these are valid test certs available in test env)
TEST_SERVER_CERT = "/tmp/test-certs/server.crt"
TEST_SERVER_KEY = "/tmp/test-certs/server.key"
TEST_CLIENT_CA = "/tmp/test-certs/ca.crt"
TEST_VALID_CLIENT_CERT = "/tmp/test-certs/client.crt"
TEST_VALID_CLIENT_KEY = "/tmp/test-certs/client.key"
TEST_INVALID_CLIENT_CERT = "/tmp/test-certs/invalid-client.crt"
TEST_INVALID_CLIENT_KEY = "/tmp/test-certs/invalid-client.key"


def test_ac1_http_tls_enabled_rejects_plaintext_accepts_https():
    """AC-1: HTTP endpoint only accepts HTTPS when TLS enabled with valid cert/key"""
    # Assume test fixture starts service with HTTP_TLS_ENABLED=true, valid cert/key paths set
    # Test plaintext HTTP connection fails
    with pytest.raises((requests.exceptions.ConnectionError, requests.exceptions.SSLError)):
        requests.get(f"{BASE_HTTP_URL}{LIVENESS_PATH}", timeout=5)
    
    # Test HTTPS connection succeeds (verify cert is valid)
    resp = requests.get(f"{BASE_HTTPS_URL}{LIVENESS_PATH}", verify=TEST_CLIENT_CA, timeout=5)
    assert resp.status_code == 200


def test_ac2_grpc_tls_enabled_rejects_plaintext_accepts_tls():
    """AC-2: gRPC endpoint only accepts TLS connections when TLS enabled with valid cert/key"""
    # Assume test fixture starts service with GRPC_TLS_ENABLED=true, valid cert/key paths set
    # Test plaintext gRPC connection fails
    plaintext_channel = grpc.insecure_channel(f"localhost:{SERVICE_GRPC_PORT}")
    stub = quote_pb2_grpc.QuoteServiceStub(plaintext_channel)
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.GetQuote(quote_pb2.GetQuoteRequest(), timeout=5)
    assert exc_info.value.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.UNAUTHENTICATED]

    # Test TLS gRPC connection succeeds
    with open(TEST_CLIENT_CA, 'rb') as f:
        ca_cert = f.read()
    credentials = grpc.ssl_channel_credentials(root_certificates=ca_cert)
    tls_channel = grpc.secure_channel(f"localhost:{SERVICE_GRPC_PORT}", credentials)
    tls_stub = quote_pb2_grpc.QuoteServiceStub(tls_channel)
    # Health check or dummy call should succeed
    resp = tls_stub.GetQuote(quote_pb2.GetQuoteRequest(nb_items=1), timeout=5)
    assert resp.cost_usd > 0


def test_ac3_http_mtls_enforces_valid_client_cert():
    """AC-3: HTTP endpoint returns 401 for requests without valid client cert when client CA is set"""
    # Assume test fixture starts service with HTTP_TLS_ENABLED=true, valid cert/key, HTTP_TLS_CLIENT_CA_PATH set to valid CA
    # Test request without client cert fails with 401
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(f"{BASE_HTTPS_URL}{LIVENESS_PATH}", verify=TEST_CLIENT_CA, timeout=5)
    
    # Test request with invalid client cert fails with 401 or SSL error
    with pytest.raises((requests.exceptions.SSLError, requests.exceptions.HTTPError)) as exc_info:
        resp = requests.get(
            f"{BASE_HTTPS_URL}{LIVENESS_PATH}",
            verify=TEST_CLIENT_CA,
            cert=(TEST_INVALID_CLIENT_CERT, TEST_INVALID_CLIENT_KEY),
            timeout=5
        )
        resp.raise_for_status()
    if 'response' in dir(exc_info.value):
        assert exc_info.value.response.status_code == 401
    
    # Test request with valid client cert succeeds
    resp = requests.get(
        f"{BASE_HTTPS_URL}{LIVENESS_PATH}",
        verify=TEST_CLIENT_CA,
        cert=(TEST_VALID_CLIENT_CERT, TEST_VALID_CLIENT_KEY),
        timeout=5
    )
    assert resp.status_code == 200


def test_ac4_grpc_mtls_enforces_valid_client_cert():
    """AC-4: gRPC endpoint returns UNAUTHENTICATED for requests without valid client cert when client CA is set"""
    # Assume test fixture starts service with GRPC_TLS_ENABLED=true, valid cert/key, GRPC_TLS_CLIENT_CA_PATH set to valid CA
    # Test gRPC call without client cert fails with UNAUTHENTICATED
    with open(TEST_CLIENT_CA, 'rb') as f:
        ca_cert = f.read()
    credentials_no_client = grpc.ssl_channel_credentials(root_certificates=ca_cert)
    channel_no_client = grpc.secure_channel(f"localhost:{SERVICE_GRPC_PORT}", credentials_no_client)
    stub_no_client = quote_pb2_grpc.QuoteServiceStub(channel_no_client)
    with pytest.raises(grpc.RpcError) as exc_info:
        stub_no_client.GetQuote(quote_pb2.GetQuoteRequest(), timeout=5)
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED

    # Test gRPC call with invalid client cert fails with UNAUTHENTICATED
    with open(TEST_INVALID_CLIENT_CERT, 'rb') as f:
        invalid_cert = f.read()
    with open(TEST_INVALID_CLIENT_KEY, 'rb') as f:
        invalid_key = f.read()
    invalid_creds = grpc.ssl_channel_credentials(
        root_certificates=ca_cert,
        private_key=invalid_key,
        certificate_chain=invalid_cert
    )
    channel_invalid = grpc.secure_channel(f"localhost:{SERVICE_GRPC_PORT}", invalid_creds)
    stub_invalid = quote_pb2_grpc.QuoteServiceStub(channel_invalid)
    with pytest.raises(grpc.RpcError) as exc_info:
        stub_invalid.GetQuote(quote_pb2.GetQuoteRequest(), timeout=5)
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED

    # Test gRPC call with valid client cert succeeds
    with open(TEST_VALID_CLIENT_CERT, 'rb') as f:
        valid_cert = f.read()
    with open(TEST_VALID_CLIENT_KEY, 'rb') as f:
        valid_key = f.read()
    valid_creds = grpc.ssl_channel_credentials(
        root_certificates=ca_cert,
        private_key=valid_key,
        certificate_chain=valid_cert
    )
    channel_valid = grpc.secure_channel(f"localhost:{SERVICE_GRPC_PORT}", valid_creds)
    stub_valid = quote_pb2_grpc.QuoteServiceStub(channel_valid)
    resp = stub_valid.GetQuote(quote_pb2.GetQuoteRequest(nb_items=1), timeout=5)
    assert resp.cost_usd > 0


def test_ac5_default_no_tls_plaintext_endpoints_work():
    """AC-5: Default configuration (no TLS vars set) uses plaintext HTTP/gRPC endpoints"""
    # Assume test fixture starts service with default env vars (all TLS_ENABLED = false)
    # Test plaintext HTTP works
    resp = requests.get(f"{BASE_HTTP_URL}{LIVENESS_PATH}", timeout=5)
    assert resp.status_code == 200

    # Test plaintext gRPC works
    channel = grpc.insecure_channel(f"localhost:{SERVICE_GRPC_PORT}")
    stub = quote_pb2_grpc.QuoteServiceStub(channel)
    resp = stub.GetQuote(quote_pb2.GetQuoteRequest(nb_items=1), timeout=5)
    assert resp.cost_usd > 0


def test_ac6_probes_use_https_when_http_tls_enabled():
    """AC-6: Liveness/readiness probes use HTTPS when HTTP TLS is enabled"""
    # Load Kubernetes deployment config for quoteservice
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    deployment = apps_v1.read_namespaced_deployment(name="quoteservice", namespace="default")
    
    # Get container spec
    container = [c for c in deployment.spec.template.spec.containers if c.name == "quoteservice"][0]
    
    # Check that when HTTP_TLS_ENABLED=true is set in env, probes use HTTPS scheme
    env_vars = {e.name: e.value for e in container.env}
    if env_vars.get(HTTP_TLS_ENABLED) == "true":
        assert container.liveness_probe.http_get.scheme == "HTTPS"
        assert container.readiness_probe.http_get.scheme == "HTTPS"
    
    # Verify probes still work with HTTPS
    resp = requests.get(
        f"{BASE_HTTPS_URL}{LIVENESS_PATH}",
        verify=TEST_CLIENT_CA,
        timeout=5
    )
    assert resp.status_code == 200
    resp = requests.get(
        f"{BASE_HTTPS_URL}{READINESS_PATH}",
        verify=TEST_CLIENT_CA,
        timeout=5
    )
    assert resp.status_code == 200


def test_ac7_k8s_deployment_supports_tls_secret_mounts():
    """AC-7: Kubernetes deployment supports mounting TLS certs/keys and client CA via secrets"""
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    deployment = apps_v1.read_namespaced_deployment(name="quoteservice", namespace="default")
    
    # Check volumes exist for tls-certs and client-ca
    volume_names = [v.name for v in deployment.spec.template.spec.volumes]
    assert "tls-certs" in volume_names
    assert "client-ca" in volume_names
    
    # Check volume mounts exist in container
    container = [c for c in deployment.spec.template.spec.containers if c.name == "quoteservice"][0]
    volume_mounts = {m.name: m.mount_path for m in container.volume_mounts}
    assert volume_mounts.get("tls-certs") == "/etc/quoteservice/tls/"
    assert volume_mounts.get("client-ca") == "/etc/quoteservice/client-ca/"
    
    # Check volumes are backed by secrets
    tls_volume = [v for v in deployment.spec.template.spec.volumes if v.name == "tls-certs"][0]
    assert tls_volume.secret is not None
    client_ca_volume = [v for v in deployment.spec.template.spec.volumes if v.name == "client-ca"][0]
    assert client_ca_volume.secret is not None


def test_ac8_missing_certs_when_tls_enabled_causes_startup_failure():
    """AC-8: Service fails to start with error log when TLS enabled but cert/key is missing/invalid"""
    # Test case 1: HTTP TLS enabled, no cert path set
    env = {
        HTTP_TLS_ENABLED: "true",
        HTTP_TLS_KEY_PATH: TEST_SERVER_KEY
    }
    # Run service container with this env, check exit code is 1
    # Assume test helper runs container and returns exit code + logs
    exit_code, logs = run_quoteservice_container(env)
    assert exit_code == 1
    assert "TLS enabled but certificate/key path missing or invalid" in logs

    # Test case 2: gRPC TLS enabled, invalid cert path
    env = {
        GRPC_TLS_ENABLED: "true",
        GRPC_TLS_CERT_PATH: "/nonexistent/path.crt",
        GRPC_TLS_KEY_PATH: TEST_SERVER_KEY
    }
    exit_code, logs = run_quoteservice_container(env)
    assert exit_code == 1
    assert "TLS enabled but certificate/key path missing or invalid" in logs

    # Test case 3: Client CA path set to invalid file
    env = {
        HTTP_TLS_ENABLED: "true",
        HTTP_TLS_CERT_PATH: TEST_SERVER_CERT,
        HTTP_TLS_KEY_PATH: TEST_SERVER_KEY,
        HTTP_TLS_CLIENT_CA_PATH: "/nonexistent/ca.crt"
    }
    exit_code, logs = run_quoteservice_container(env)
    assert exit_code == 1
    assert "Client CA path invalid or unreadable" in logs


def run_quoteservice_container(env_vars):
    """Helper function to run quoteservice container with given env vars, return exit code and logs"""
    # Implementation of helper omitted for test spec purposes, test harness provides this
    pass
