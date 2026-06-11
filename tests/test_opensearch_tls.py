import os
import pytest
import requests
import socket
import ssl
from kubernetes import client, config
from time import sleep

# Test constants matching spec interface
ENV_HTTP_TLS_ENABLED = "OPENSEARCH_HTTP_TLS_ENABLED"
ENV_TRANSPORT_TLS_ENABLED = "OPENSEARCH_TRANSPORT_TLS_ENABLED"
ENV_HTTP_CLIENT_AUTH_REQUIRED = "OPENSEARCH_HTTP_CLIENT_AUTH_REQUIRED"
ENV_TRANSPORT_CLIENT_AUTH_REQUIRED = "OPENSEARCH_TRANSPORT_CLIENT_AUTH_REQUIRED"

CERT_PATHS = {
    "ca": "/etc/opensearch/certs/ca/ca.crt",
    "http_cert": "/etc/opensearch/certs/http/tls.crt",
    "http_key": "/etc/opensearch/certs/http/tls.key",
    "transport_cert": "/etc/opensearch/certs/transport/tls.crt",
    "transport_key": "/etc/opensearch/certs/transport/tls.key",
    "client_ca": "/etc/opensearch/certs/client-ca/ca.crt"
}

OPENSEARCH_HTTP_PORT = 9200
OPENSEARCH_TRANSPORT_PORT = 9300

@pytest.fixture(scope="module")
def k8s_client():
    config.load_incluster_config()
    return client.CoreV1Api()

@pytest.fixture
def opensearch_service_host():
    # Adjust service name to match actual deployment
    return "opensearch"

def test_ac1_default_no_tls(opensearch_service_host):
    """AC-1: Default state (no TLS env vars set) allows plaintext connections on both ports, no certs required"""
    # Test plaintext HTTP connection works
    resp = requests.get(f"http://{opensearch_service_host}:{OPENSEARCH_HTTP_PORT}", timeout=5)
    assert resp.status_code == 200, "Plaintext HTTP connection should succeed on default deployment"
    
    # Test plaintext transport connection works
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        conn_result = s.connect_ex((opensearch_service_host, OPENSEARCH_TRANSPORT_PORT))
        assert conn_result == 0, "Plaintext transport connection should succeed on default deployment"
    
    # Verify no certificate paths are required (check deployment doesn't mount them by default)
    # (This is validated via deployment manifest check in separate test)

def test_ac2_http_tls_enabled_rejects_plaintext(opensearch_service_host):
    """AC-2: When HTTP TLS enabled, plaintext connections on 9200 are rejected, TLS 1.2+ connections work"""
    # First verify plaintext connection fails
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://{opensearch_service_host}:{OPENSEARCH_HTTP_PORT}", timeout=5, allow_redirects=False)
    
    # Verify TLS 1.2+ connection works and returns correct cert
    context = ssl.create_default_context(cafile=CERT_PATHS["ca"])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((opensearch_service_host, OPENSEARCH_HTTP_PORT), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=opensearch_service_host) as secure_sock:
            assert secure_sock.version() in ["TLSv1.2", "TLSv1.3"], "Only TLS 1.2+ should be supported"
            cert = secure_sock.getpeercert(binary_form=True)
            # Verify cert matches expected mounted cert (compare hashes or content)
            with open(CERT_PATHS["http_cert"], "rb") as f:
                expected_cert = f.read()
            assert cert == expected_cert, "Server certificate should match mounted HTTP cert"

def test_ac3_http_mtls_required_rejects_unauthenticated(opensearch_service_host):
    """AC-3: When HTTP mTLS required, requests without client cert are rejected, valid client certs are accepted"""
    # Test request without client cert is rejected
    context_no_client_cert = ssl.create_default_context(cafile=CERT_PATHS["ca"])
    with pytest.raises(ssl.SSLError):
        with socket.create_connection((opensearch_service_host, OPENSEARCH_HTTP_PORT), timeout=5) as sock:
            with context_no_client_cert.wrap_socket(sock, server_hostname=opensearch_service_host):
                pass
    
    # Test request with valid client cert is accepted
    context_with_client_cert = ssl.create_default_context(cafile=CERT_PATHS["ca"])
    context_with_client_cert.load_cert_chain(
        certfile=CERT_PATHS["client_ca"],  # Use client cert/key here in actual test
        keyfile=CERT_PATHS["client_ca"].replace(".crt", ".key")
    )
    with socket.create_connection((opensearch_service_host, OPENSEARCH_HTTP_PORT), timeout=5) as sock:
        with context_with_client_cert.wrap_socket(sock, server_hostname=opensearch_service_host) as secure_sock:
            secure_sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            resp = secure_sock.recv(1024).decode()
            assert resp.startswith("HTTP/1.1 200") or resp.startswith("HTTP/1.1 202"), "Valid client cert should be accepted"

def test_ac4_transport_tls_enabled_rejects_plaintext(opensearch_service_host):
    """AC-4: When transport TLS enabled, plaintext connections on 9300 are rejected, TLS 1.2+ connections work"""
    # First verify plaintext transport connection fails
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        conn_result = s.connect_ex((opensearch_service_host, OPENSEARCH_TRANSPORT_PORT))
        assert conn_result != 0, "Plaintext transport connection should be rejected when TLS is enabled"
    
    # Verify TLS 1.2+ transport connection works and returns correct cert
    context = ssl.create_default_context(cafile=CERT_PATHS["ca"])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((opensearch_service_host, OPENSEARCH_TRANSPORT_PORT), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=opensearch_service_host) as secure_sock:
            assert secure_sock.version() in ["TLSv1.2", "TLSv1.3"], "Only TLS 1.2+ should be supported for transport"
            cert = secure_sock.getpeercert(binary_form=True)
            with open(CERT_PATHS["transport_cert"], "rb") as f:
                expected_cert = f.read()
            assert cert == expected_cert, "Transport server certificate should match mounted transport cert"

def test_ac5_transport_mtls_required_rejects_unauthenticated(opensearch_service_host):
    """AC-5: When transport mTLS required, connections without valid client cert are rejected"""
    # Test connection without client cert is rejected
    context_no_client_cert = ssl.create_default_context(cafile=CERT_PATHS["ca"])
    with pytest.raises(ssl.SSLError):
        with socket.create_connection((opensearch_service_host, OPENSEARCH_TRANSPORT_PORT), timeout=5) as sock:
            with context_no_client_cert.wrap_socket(sock, server_hostname=opensearch_service_host):
                pass
    
    # Test connection with valid client cert is accepted
    context_with_client_cert = ssl.create_default_context(cafile=CERT_PATHS["ca"])
    context_with_client_cert.load_cert_chain(
        certfile=CERT_PATHS["client_ca"],
        keyfile=CERT_PATHS["client_ca"].replace(".crt", ".key")
    )
    with socket.create_connection((opensearch_service_host, OPENSEARCH_TRANSPORT_PORT), timeout=5) as sock:
        with context_with_client_cert.wrap_socket(sock, server_hostname=opensearch_service_host) as secure_sock:
            # Transport protocol handshake should succeed
            assert secure_sock.getpeername() is not None, "Valid client cert should be accepted for transport"

def test_ac6_missing_certs_causes_start_failure(k8s_client):
    """AC-6: If TLS enabled but required certificates missing, container fails to start with clear error"""
    # Get opensearch pod(s)
    pods = k8s_client.list_namespaced_pod(namespace="default", label_selector="app.kubernetes.io/name=opensearch")
    assert len(pods.items) > 0, "No opensearch pods found"
    pod = pods.items[0]
    
    # Check pod is not running
    assert pod.status.phase != "Running", "Pod should not be running when required certificates are missing"
    
    # Get pod logs and verify error message indicates missing cert
    logs = k8s_client.read_namespaced_pod_log(pod.metadata.name, namespace=pod.metadata.namespace)
    assert "missing certificate file" in logs.lower() or "file not found" in logs.lower(), "Error message should indicate missing certificate file"
    
    # Verify no ports are open
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        http_result = s.connect_ex((pod.status.pod_ip, OPENSEARCH_HTTP_PORT))
        transport_result = s.connect_ex((pod.status.pod_ip, OPENSEARCH_TRANSPORT_PORT))
    assert http_result != 0, "Port 9200 should not be open when pod fails to start"
    assert transport_result != 0, "Port 9300 should not be open when pod fails to start"

def test_ac7_deployment_manifest_has_documentation():
    """AC-7: Kubernetes deployment manifest includes required documentation comments"""
    deployment_path = "kubernetes/opensearch/deployment.yaml"
    assert os.path.exists(deployment_path), f"Deployment file {deployment_path} not found"
    
    with open(deployment_path, "r") as f:
        content = f.read()
    
    # Check env var documentation exists
    assert ENV_HTTP_TLS_ENABLED in content, f"Documentation for {ENV_HTTP_TLS_ENABLED} missing from deployment"
    assert ENV_TRANSPORT_TLS_ENABLED in content, f"Documentation for {ENV_TRANSPORT_TLS_ENABLED} missing from deployment"
    assert ENV_HTTP_CLIENT_AUTH_REQUIRED in content, f"Documentation for {ENV_HTTP_CLIENT_AUTH_REQUIRED} missing from deployment"
    assert ENV_TRANSPORT_CLIENT_AUTH_REQUIRED in content, f"Documentation for {ENV_TRANSPORT_CLIENT_AUTH_REQUIRED} missing from deployment"
    
    # Check mount path documentation exists
    for path_name, path_value in CERT_PATHS.items():
        assert path_value in content, f"Documentation for {path_name} mount path {path_value} missing from deployment"
    
    # Check example secret commands exist
    assert "kubectl create secret" in content.lower(), "Example secret creation commands missing from deployment comments"
