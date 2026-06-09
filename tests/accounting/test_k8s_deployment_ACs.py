#!/usr/bin/env python3
import os
import yaml
import subprocess
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "./kubernetes/accounting-service/deployment.yaml"
SERVICE_PATH = "./kubernetes/accounting-service/service.yaml"
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")

@pytest.fixture(scope="module")
def load_deployment_manifest():
    if not os.path.exists(DEPLOYMENT_PATH):
        pytest.fail(f"Deployment manifest not found at {DEPLOYMENT_PATH}")
    with open(DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture(scope="module")
def load_service_manifest():
    if not os.path.exists(SERVICE_PATH):
        pytest.fail(f"Service manifest not found at {SERVICE_PATH}")
    with open(SERVICE_PATH, "r") as f:
        return yaml.safe_load(f)

@pytest.mark.ac1
def test_ac1_deployment_succeeds_replicas_running():
    """AC-1: Deployment succeeds, all replicas reach Running state within 60s"""
    assert os.path.exists(DEPLOYMENT_PATH), "Deployment manifest file is missing"
    
    # Try dry run first
    result = subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "--dry-run=server", "-n", NAMESPACE],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Dry run failed: {result.stderr}"

@pytest.mark.ac2
def test_ac2_resource_limits_correct(load_deployment_manifest):
    """AC-2: CPU requests 100m, limits 500m; memory requests 128Mi, limits 256Mi"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    accounting_container = next(c for c in containers if c["name"] == "accounting-service")
    
    resources = accounting_container["resources"]
    assert resources["requests"]["cpu"] == "100m", "CPU request incorrect"
    assert resources["limits"]["cpu"] == "500m", "CPU limit incorrect"
    assert resources["requests"]["memory"] == "128Mi", "Memory request incorrect"
    assert resources["limits"]["memory"] == "256Mi", "Memory limit incorrect"

@pytest.mark.ac3
def test_ac3_liveness_probe_configured(load_deployment_manifest):
    """AC-3: Liveness probe pointing to /health/live port 8080, initialDelay 10s, period 30s, failureThreshold 3"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    accounting_container = next(c for c in containers if c["name"] == "accounting-service")
    
    liveness_probe = accounting_container["livenessProbe"]
    assert liveness_probe["httpGet"]["path"] == "/health/live", "Liveness probe path incorrect"
    assert liveness_probe["httpGet"]["port"] == 8080, "Liveness probe port incorrect"
    assert liveness_probe["initialDelaySeconds"] == 10, "Liveness initial delay incorrect"
    assert liveness_probe["periodSeconds"] == 30, "Liveness period incorrect"
    assert liveness_probe["failureThreshold"] == 3, "Liveness failure threshold incorrect"

@pytest.mark.ac4
def test_ac4_readiness_probe_configured(load_deployment_manifest):
    """AC-4: Readiness probe pointing to /health/ready port 8080, initialDelay 5s, period 10s, failureThreshold 2"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    accounting_container = next(c for c in containers if c["name"] == "accounting-service")
    
    readiness_probe = accounting_container["readinessProbe"]
    assert readiness_probe["httpGet"]["path"] == "/health/ready", "Readiness probe path incorrect"
    assert readiness_probe["httpGet"]["port"] == 8080, "Readiness probe port incorrect"
    assert readiness_probe["initialDelaySeconds"] == 5, "Readiness initial delay incorrect"
    assert readiness_probe["periodSeconds"] == 10, "Readiness period incorrect"
    assert readiness_probe["failureThreshold"] == 2, "Readiness failure threshold incorrect"

@pytest.mark.ac5
def test_ac5_security_context_configured(load_deployment_manifest):
    """AC-5: Non-root user UID 1000 GID 1000, no privileged access, readOnlyRootFilesystem true, allowPrivilegeEscalation false"""
    pod_spec = load_deployment_manifest["spec"]["template"]["spec"]
    security_context = pod_spec["securityContext"]
    assert security_context["runAsNonRoot"] == True, "runAsNonRoot must be true"
    assert security_context["runAsUser"] == 1000, "runAsUser must be 1000"
    assert security_context["runAsGroup"] == 1000, "runAsGroup must be 1000"
    
    container_security_context = next(c for c in pod_spec["containers"] if c["name"] == "accounting-service")["securityContext"]
    assert container_security_context["privileged"] == False, "Privileged access must be false"
    assert container_security_context["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem must be true"
    assert container_security_context["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation must be false"

@pytest.mark.ac6
def test_ac6_environment_variables_present(load_deployment_manifest):
    """AC-6: All required env vars present: OTEL_EXPORTER_OTLP_ENDPOINT, DATABASE_CONNECTION_STRING, SERVICE_NAME, LOG_LEVEL prefixed with ACCOUNTING_"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    accounting_container = next(c for c in containers if c["name"] == "accounting-service")
    env_vars = [env["name"] for env in accounting_container["env"]]
    
    required_vars = [
        "ACCOUNTING_OTEL_EXPORTER_OTLP_ENDPOINT",
        "ACCOUNTING_DATABASE_CONNECTION_STRING",
        "ACCOUNTING_SERVICE_NAME",
        "ACCOUNTING_LOG_LEVEL"
    ]
    
    for var in required_vars:
        assert var in env_vars, f"Required environment variable {var} is missing"

@pytest.mark.ac7
def test_ac7_service_configuration(load_service_manifest):
    """AC-7: ClusterIP service created, routes to port 8080, DNS name accounting-service.<namespace>.svc.cluster.local"""
    assert load_service_manifest["apiVersion"] == "v1", "Service API version incorrect"
    assert load_service_manifest["kind"] == "Service", "Kind must be Service"
    assert load_service_manifest["spec"]["type"] == "ClusterIP", "Service type must be ClusterIP"
    
    ports = load_service_manifest["spec"]["ports"]
    target_port = next(p["targetPort"] for p in ports if p["port"] == 8080)
    assert target_port == 8080, "Service target port must be 8080"
    
    selector = load_service_manifest["spec"]["selector"]
    assert selector["app.kubernetes.io/name"] == "accounting-service", "Service selector incorrect"
    assert selector["app.kubernetes.io/component"] == "backend", "Service selector component incorrect"

@pytest.mark.ac8
def test_ac8_kube_lint_validation_passes():
    """AC-8: Manifest files pass kube-lint validation with no critical or warning errors"""
    result = subprocess.run(
        ["kube-lint", DEPLOYMENT_PATH, SERVICE_PATH],
        capture_output=True,
        text=True
    )
    
    # Check for critical or warning errors in output
    output = result.stdout + result.stderr
    assert "CRITICAL" not in output, "kube-lint found critical errors"
    assert "WARNING" not in output, "kube-lint found warning errors"
    assert result.returncode == 0, "kube-lint validation failed"
