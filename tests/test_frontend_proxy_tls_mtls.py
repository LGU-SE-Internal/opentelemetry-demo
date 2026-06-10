import pytest
import requests
from kubernetes.client import V1Deployment, V1Container
from typing import Dict


def get_frontend_proxy_container(deployment: V1Deployment) -> V1Container:
    """Helper to extract frontend-proxy container from deployment"""
    for container in deployment.spec.template.spec.containers:
        if container.name == "frontend-proxy":
            return container
    pytest.fail("frontend-proxy container not found in deployment")


def test_ac1_ingress_tls_disabled_plaintext_allowed(frontend_proxy_deployment_default: V1Deployment, frontend_proxy_endpoint: str):
    """AC-1: When FRONTEND_PROXY_INGRESS_TLS_ENABLED is unset/false, accept plaintext HTTP connections"""
    # Verify plaintext HTTP request succeeds
    resp = requests.get(f"http://{frontend_proxy_endpoint}", timeout=5)
    assert resp.status_code == 200, "Plaintext HTTP connection should succeed when ingress TLS is disabled"
    
    # Verify no TLS listener configured (check env vars in container)
    container = get_frontend_proxy_container(frontend_proxy_deployment_default)
    env_vars = {e.name: e.value for e in container.env if e.name.startswith("FRONTEND_PROXY_INGRESS_TLS")}
    assert env_vars.get("FRONTEND_PROXY_INGRESS_TLS_ENABLED", "false").lower() == "false"


def test_ac2_ingress_tls_enabled_only_tls_12_plus_accepted(frontend_proxy_deployment_ingress_tls: V1Deployment, frontend_proxy_tls_endpoint: str):
    """AC-2: When ingress TLS enabled with valid cert/key, accept only TLS 1.2+ connections"""
    # Verify plaintext HTTP fails
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://{frontend_proxy_tls_endpoint}", timeout=5, allow_redirects=False)
    
    # Verify TLS 1.0/1.1 connections are rejected
    import ssl
    for tls_version in [ssl.PROTOCOL_TLSv1, ssl.PROTOCOL_TLSv1_1]:
        context = ssl.SSLContext(tls_version)
        with pytest.raises((ssl.SSLError, ConnectionError)):
            with requests.Session() as s:
                s.mount("https://", requests.adapters.HTTPAdapter(ssl_context=context))
                s.get(f"https://{frontend_proxy_tls_endpoint}", timeout=5, verify=False)
    
    # Verify TLS 1.2+ connections succeed with valid cert
    resp = requests.get(f"https://{frontend_proxy_tls_endpoint}", timeout=5, verify="./test/certs/ca.crt")
    assert resp.status_code == 200, "TLS 1.2+ connection should succeed with valid certificate"


def test_ac3_ingress_tls_enabled_missing_cert_key_fails_startup():
    """AC-3: When ingress TLS enabled but cert/key path missing/invalid, proxy fails to start with explicit error"""
    # Attempt to deploy frontend-proxy with TLS enabled but no cert/key paths
    with pytest.raises(Exception) as excinfo:
        # Fixture that tries to deploy with invalid config
        deployment = frontend_proxy_deployment_ingress_tls_invalid_config
    assert "FRONTEND_PROXY_INGRESS_TLS_CERT_PATH" in str(excinfo.value) or "FRONTEND_PROXY_INGRESS_TLS_KEY_PATH" in str(excinfo.value), \
        "Startup should fail with explicit error about missing required TLS configuration"


def test_ac4_ingress_mtls_enabled_valid_clients_accepted_invalid_rejected(frontend_proxy_deployment_ingress_mtls: V1Deployment, frontend_proxy_tls_endpoint: str):
    """AC-4: When ingress mTLS enabled, accept only clients with valid CA-signed certs"""
    # Client without cert should be rejected
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(f"https://{frontend_proxy_tls_endpoint}", timeout=5, verify="./test/certs/ca.crt")
    
    # Client with invalid/unsigned cert should be rejected
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(f"https://{frontend_proxy_tls_endpoint}", timeout=5, 
                    verify="./test/certs/ca.crt",
                    cert=("./test/certs/invalid-client.crt", "./test/certs/invalid-client.key"))
    
    # Client with valid CA-signed cert should be accepted
    resp = requests.get(f"https://{frontend_proxy_tls_endpoint}", timeout=5, 
                       verify="./test/certs/ca.crt",
                       cert=("./test/certs/valid-client.crt", "./test/certs/valid-client.key"))
    assert resp.status_code == 200, "Valid client cert should be accepted when mTLS is enabled"


def test_ac5_upstream_tls_disabled_plaintext_to_backends(frontend_proxy_deployment_default: V1Deployment, backend_service_metrics_endpoint: str):
    """AC-5: When upstream TLS disabled, all egress to backends uses plaintext HTTP"""
    # Trigger request through frontend-proxy to backend
    requests.get(f"http://{frontend_proxy_deployment_default.status.load_balancer.ingress[0].ip}/api/products", timeout=5)
    
    # Check backend service metrics to verify connection was plaintext
    resp = requests.get(f"{backend_service_metrics_endpoint}/metrics", timeout=5)
    assert "http_requests_total{security=\"plaintext\"" in resp.text, "Backend should receive plaintext connections when upstream TLS is disabled"
    assert "http_requests_total{security=\"tls\"" not in resp.text, "No TLS connections should exist when upstream TLS is disabled"


def test_ac6_upstream_tls_enabled_tls_12_plus_to_backends(frontend_proxy_deployment_upstream_tls: V1Deployment, backend_service_metrics_endpoint: str):
    """AC-6: When upstream TLS enabled, all egress uses TLS 1.2+ with certificate verification"""
    # Trigger request through frontend-proxy to backend
    requests.get(f"http://{frontend_proxy_deployment_upstream_tls.status.load_balancer.ingress[0].ip}/api/products", timeout=5)
    
    # Check backend service metrics to verify connection was TLS 1.2+
    resp = requests.get(f"{backend_service_metrics_endpoint}/metrics", timeout=5)
    assert "http_requests_total{security=\"tls\",tls_version=\"1.2\"" in resp.text or "http_requests_total{security=\"tls\",tls_version=\"1.3\"" in resp.text, \
        "Backend should receive TLS 1.2+ connections when upstream TLS is enabled"
    
    # Verify connection fails when backend presents invalid cert (check proxy logs)
    with pytest.raises(requests.exceptions.InternalServerError):
        requests.get(f"http://{frontend_proxy_deployment_upstream_tls.status.load_balancer.ingress[0].ip}/api/invalid-cert-backend", timeout=5)


def test_ac7_upstream_mtls_enabled_client_cert_presented(frontend_proxy_deployment_upstream_mtls: V1Deployment, mtls_backend_metrics_endpoint: str):
    """AC-7: When upstream mTLS enabled, proxy presents configured client cert to backends"""
    # Trigger request through frontend-proxy to mTLS-enabled backend
    requests.get(f"http://{frontend_proxy_deployment_upstream_mtls.status.load_balancer.ingress[0].ip}/api/orders", timeout=5)
    
    # Check mTLS backend metrics to verify client cert was presented and validated
    resp = requests.get(f"{mtls_backend_metrics_endpoint}/metrics", timeout=5)
    assert "mtls_client_cert_valid{status=\"accepted\"" in resp.text, "Backend should receive valid client cert when upstream mTLS is enabled"


def test_ac8_default_config_no_functionality_change(frontend_proxy_deployment_default: V1Deployment, frontend_proxy_endpoint: str):
    """AC-8: All TLS/mTLS vars at default, existing functionality remains unchanged"""
    # Verify all standard endpoints work as expected
    for path in ["/", "/api/products", "/api/cart", "/api/checkout"]:
        resp = requests.get(f"http://{frontend_proxy_endpoint}{path}", timeout=5)
        assert resp.status_code == 200 or resp.status_code == 302, f"Endpoint {path} should work as expected with default config"
    
    # Verify no TLS config is present in Envoy config
    container = get_frontend_proxy_container(frontend_proxy_deployment_default)
    exec_resp = container.exec_run("cat /etc/envoy/envoy.yaml | grep -i tls")
    assert exec_resp.exit_code == 1, "No TLS configuration should exist in default Envoy config"
