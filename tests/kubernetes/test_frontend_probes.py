import yaml
import pytest
import requests

DEPLOYMENT_FILE = "kubernetes/frontend-deployment.yaml"

def get_frontend_deployment():
    with open(DEPLOYMENT_FILE, "r") as f:
        docs = list(yaml.safe_load_all(f))
        for doc in docs:
            if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "frontend":
                return doc
    raise ValueError("Frontend deployment not found")

def test_ac1_liveness_probe_path_correct():
    """AC-1: LivenessProbe.httpGet.path is set to /api/health/live"""
    deployment = get_frontend_deployment()
    liveness_probe = deployment["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]
    assert liveness_probe["httpGet"]["path"] == "/api/health/live"

def test_ac2_readiness_probe_path_correct():
    """AC-2: ReadinessProbe.httpGet.path is set to /api/health/ready"""
    deployment = get_frontend_deployment()
    readiness_probe = deployment["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
    assert readiness_probe["httpGet"]["path"] == "/api/health/ready"

def test_ac3_liveness_probe_other_properties_unchanged():
    """AC-3: All other livenessProbe properties remain unchanged"""
    deployment = get_frontend_deployment()
    liveness_probe = deployment["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]
    assert liveness_probe["httpGet"]["port"] == 8080
    assert liveness_probe["initialDelaySeconds"] == 30
    assert liveness_probe["periodSeconds"] == 10
    assert liveness_probe["timeoutSeconds"] == 1
    assert liveness_probe["failureThreshold"] == 3

def test_ac4_readiness_probe_other_properties_unchanged():
    """AC-4: All other readinessProbe properties remain unchanged"""
    deployment = get_frontend_deployment()
    readiness_probe = deployment["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
    assert readiness_probe["httpGet"]["port"] == 8080
    assert readiness_probe["initialDelaySeconds"] == 5
    assert readiness_probe["periodSeconds"] == 5
    assert readiness_probe["timeoutSeconds"] == 1
    assert readiness_probe["failureThreshold"] == 3

@pytest.mark.e2e
def test_ac5_liveness_endpoint_returns_200(service_endpoint):
    """AC-5: GET /api/health/live returns 200 when service is running"""
    response = requests.get(f"{service_endpoint}/api/health/live", timeout=5)
    assert response.status_code == 200

@pytest.mark.e2e
def test_ac6_readiness_endpoint_returns_200_when_healthy(service_endpoint):
    """AC-6: GET /api/health/ready returns 200 when all dependencies are healthy"""
    response = requests.get(f"{service_endpoint}/api/health/ready", timeout=5)
    assert response.status_code == 200
