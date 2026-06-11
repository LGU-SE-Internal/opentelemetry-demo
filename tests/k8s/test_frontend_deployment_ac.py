#!/usr/bin/env python3
import yaml
import os
import subprocess
import pytest

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "../../kubernetes/frontend-deployment.yaml")

def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        pytest.fail("Deployment manifest not found at %s" % MANIFEST_PATH)
    with open(MANIFEST_PATH, "r") as f:
        return yaml.safe_load(f)

def test_ac1_resource_requirements():
    """AC-1: Resource requests >= 100m CPU, 128Mi memory; limits <=500m CPU, 256Mi memory"""
    manifest = load_manifest()
    containers = manifest["spec"]["template"]["spec"]["containers"]
    frontend_container = next(c for c in containers if c["name"] == "frontend")
    resources = frontend_container["resources"]
    
    # Check requests
    assert "requests" in resources
    assert "cpu" in resources["requests"]
    assert "memory" in resources["requests"]
    
    cpu_req = resources["requests"]["cpu"]
    mem_req = resources["requests"]["memory"]
    # Convert to millicores
    if cpu_req.endswith("m"):
        cpu_req_m = int(cpu_req[:-1])
    else:
        cpu_req_m = int(float(cpu_req) * 1000)
    assert cpu_req_m >= 100, "CPU request must be at least 100m"
    
    # Convert to Mi
    if mem_req.endswith("Mi"):
        mem_req_mi = int(mem_req[:-2])
    elif mem_req.endswith("Gi"):
        mem_req_mi = int(mem_req[:-2]) * 1024
    else:
        # Bytes
        mem_req_mi = int(mem_req) / (1024 * 1024)
    assert mem_req_mi >= 128, "Memory request must be at least 128Mi"
    
    # Check limits
    assert "limits" in resources
    assert "cpu" in resources["limits"]
    assert "memory" in resources["limits"]
    
    cpu_limit = resources["limits"]["cpu"]
    mem_limit = resources["limits"]["memory"]
    if cpu_limit.endswith("m"):
        cpu_limit_m = int(cpu_limit[:-1])
    else:
        cpu_limit_m = int(float(cpu_limit) * 1000)
    assert cpu_limit_m <= 500, "CPU limit must be at most 500m"
    
    if mem_limit.endswith("Mi"):
        mem_limit_mi = int(mem_limit[:-2])
    elif mem_limit.endswith("Gi"):
        mem_limit_mi = int(mem_limit[:-2]) * 1024
    else:
        mem_limit_mi = int(mem_limit) / (1024 * 1024)
    assert mem_limit_mi <= 256, "Memory limit must be at most 256Mi"

def test_ac2_liveness_probe():
    """AC-2: Liveness probe points to /healthz on port 8080, initialDelay 30s, period 10s, timeout 1s, failureThreshold 3"""
    manifest = load_manifest()
    containers = manifest["spec"]["template"]["spec"]["containers"]
    frontend_container = next(c for c in containers if c["name"] == "frontend")
    liveness_probe = frontend_container["livenessProbe"]
    
    assert liveness_probe["httpGet"]["path"] == "/healthz"
    assert liveness_probe["httpGet"]["port"] == 8080
    assert liveness_probe["initialDelaySeconds"] == 30
    assert liveness_probe["periodSeconds"] == 10
    assert liveness_probe["timeoutSeconds"] == 1
    assert liveness_probe["failureThreshold"] == 3

def test_ac3_readiness_probe():
    """AC-3: Readiness probe points to /readyz on port 8080, initialDelay 5s, period 5s, timeout 1s, failureThreshold 3"""
    manifest = load_manifest()
    containers = manifest["spec"]["template"]["spec"]["containers"]
    frontend_container = next(c for c in containers if c["name"] == "frontend")
    readiness_probe = frontend_container["readinessProbe"]
    
    assert readiness_probe["httpGet"]["path"] == "/readyz"
    assert readiness_probe["httpGet"]["port"] == 8080
    assert readiness_probe["initialDelaySeconds"] == 5
    assert readiness_probe["periodSeconds"] == 5
    assert readiness_probe["timeoutSeconds"] == 1
    assert readiness_probe["failureThreshold"] == 3

def test_ac4_pod_security_context():
    """AC-4: Pod-level security context: runAsNonRoot: true, runAsUser: 1000, fsGroup: 1000"""
    manifest = load_manifest()
    pod_spec = manifest["spec"]["template"]["spec"]
    security_context = pod_spec["securityContext"]
    
    assert security_context["runAsNonRoot"] == True
    assert security_context["runAsUser"] == 1000
    assert security_context["fsGroup"] == 1000

def test_ac5_container_security_context():
    """AC-5: Container-level security context: readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, capabilities drop ALL"""
    manifest = load_manifest()
    containers = manifest["spec"]["template"]["spec"]["containers"]
    frontend_container = next(c for c in containers if c["name"] == "frontend")
    security_context = frontend_container["securityContext"]
    
    assert security_context["readOnlyRootFilesystem"] == True
    assert security_context["allowPrivilegeEscalation"] == False
    assert "ALL" in security_context["capabilities"]["drop"]

def test_ac6_required_labels():
    """AC-6: Required labels on both Deployment metadata and pod template metadata"""
    manifest = load_manifest()
    required_labels = {
        "app.kubernetes.io/name": "frontend",
        "app.kubernetes.io/component": "web",
        "app.kubernetes.io/part-of": "opentelemetry-demo"
    }
    
    # Check deployment labels
    deployment_labels = manifest["metadata"]["labels"]
    for k, v in required_labels.items():
        assert k in deployment_labels
        assert deployment_labels[k] == v
    
    # Check pod template labels
    pod_labels = manifest["spec"]["template"]["metadata"]["labels"]
    for k, v in required_labels.items():
        assert k in pod_labels
        assert pod_labels[k] == v

def test_ac7_observability_annotations():
    """AC-7: Required observability annotations on pod template metadata"""
    manifest = load_manifest()
    required_annotations = {
        "prometheus.io/scrape": "true",
        "prometheus.io/port": "8080",
        "prometheus.io/path": "/metrics"
    }
    
    pod_annotations = manifest["spec"]["template"]["metadata"]["annotations"]
    for k, v in required_annotations.items():
        assert k in pod_annotations
        assert str(pod_annotations[k]) == v

def test_ac8_environment_variables():
    """AC-8: Environment variables for OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME, NEXT_PUBLIC_BASE_URL, PORT=8080"""
    manifest = load_manifest()
    containers = manifest["spec"]["template"]["spec"]["containers"]
    frontend_container = next(c for c in containers if c["name"] == "frontend")
    env_vars = frontend_container["env"]
    
    env_names = [e["name"] for e in env_vars]
    required_envs = [
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
        "NEXT_PUBLIC_BASE_URL",
        "PORT"
    ]
    for env_name in required_envs:
        assert env_name in env_names, f"Missing environment variable {env_name}"
    
    # Check PORT is set to 8080
    port_env = next(e for e in env_vars if e["name"] == "PORT")
    assert str(port_env["value"]) == "8080"

def test_ac9_kubectl_validate_strict():
    """AC-9: Manifest passes kubectl validate --strict with zero errors/warnings"""
    if not os.path.exists(MANIFEST_PATH):
        pytest.fail("Deployment manifest not found")
    
    result = subprocess.run(
        ["kubectl", "validate", "--strict", "-f", MANIFEST_PATH],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl validate failed: {result.stderr}"
    assert len(result.stderr.strip()) == 0, f"kubectl validate produced warnings: {result.stderr}"

def test_ac10_non_root_no_write_root():
    """AC-10: Container does not run as root, no write access to root filesystem"""
    # This is covered by AC-4 and AC-5, but we verify together
    manifest = load_manifest()
    pod_sc = manifest["spec"]["template"]["spec"]["securityContext"]
    containers = manifest["spec"]["template"]["spec"]["containers"]
    frontend_container = next(c for c in containers if c["name"] == "frontend")
    container_sc = frontend_container["securityContext"]
    
    assert pod_sc["runAsNonRoot"] == True
    assert pod_sc["runAsUser"] != 0
    assert container_sc["readOnlyRootFilesystem"] == True
