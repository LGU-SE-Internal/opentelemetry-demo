import yaml
import subprocess
import os
import pytest
import re

MANIFEST_PATH = "./kubernetes/manifests/product-catalog/deployment.yaml"
EXPECTED_PORT = 3550  # Default product catalog service port
EXPECTED_UID = 1000
EXPECTED_SERVICE_VERSION = "1.8.0"  # Default demo version


def test_ac1_deployment_passes_kubectl_dry_run():
    """AC-1: Manifest passes kubectl apply --dry-run=server validation for Kubernetes 1.24+"""
    assert os.path.exists(MANIFEST_PATH), f"Deployment manifest not found at {MANIFEST_PATH}"
    
    result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFEST_PATH, "--dry-run=server"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl dry run failed: {result.stderr}"


def test_ac2_all_resource_fields_are_positive():
    """AC-2: All 4 resource fields (cpu/mem request/limit) have non-zero positive values"""
    with open(MANIFEST_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    # Check requests
    assert "requests" in resources, "Missing resources.requests"
    assert "cpu" in resources["requests"], "Missing resources.requests.cpu"
    assert "memory" in resources["requests"], "Missing resources.requests.memory"
    # Check limits
    assert "limits" in resources, "Missing resources.limits"
    assert "cpu" in resources["limits"], "Missing resources.limits.cpu"
    assert "memory" in resources["limits"], "Missing resources.limits.memory"
    
    # Verify values are non-zero
    def is_positive_resource(val):
        if isinstance(val, str):
            # Parse k8s resource values like 100m, 128Mi
            num = float(re.match(r"[\d.]+", val).group())
            return num > 0
        return val > 0
    
    assert is_positive_resource(resources["requests"]["cpu"]), "CPU request must be positive"
    assert is_positive_resource(resources["requests"]["memory"]), "Memory request must be positive"
    assert is_positive_resource(resources["limits"]["cpu"]), "CPU limit must be positive"
    assert is_positive_resource(resources["limits"]["memory"]), "Memory limit must be positive"


def test_ac3_probes_are_correctly_configured():
    """AC-3: Liveness and readiness probes configured for GET /health with correct parameters"""
    with open(MANIFEST_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "Missing livenessProbe"
    assert "readinessProbe" in container, "Missing readinessProbe"
    
    for probe_name in ["livenessProbe", "readinessProbe"]:
        probe = container[probe_name]
        assert "httpGet" in probe, f"{probe_name} must use HTTP GET"
        assert probe["httpGet"]["path"] == "/health", f"{probe_name} path must be /health"
        assert probe["httpGet"]["port"] == EXPECTED_PORT, f"{probe_name} port must match service port"
        assert probe.get("initialDelaySeconds", 0) >= 3, f"{probe_name} initialDelaySeconds must be >=3"
        assert probe.get("periodSeconds", 999) <= 30, f"{probe_name} periodSeconds must be <=30"
        assert probe.get("timeoutSeconds", 999) <= 5, f"{probe_name} timeoutSeconds must be <=5"
        assert probe.get("failureThreshold", 999) <= 5, f"{probe_name} failureThreshold must be <=5"


def test_ac4_security_context_follows_best_practices():
    """AC-4: Pod and container security context configured with non-root, read-only FS, dropped capabilities"""
    with open(MANIFEST_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    pod_spec = manifest["spec"]["template"]["spec"]
    pod_sc = pod_spec.get("securityContext", {})
    container = pod_spec["containers"][0]
    container_sc = container.get("securityContext", {})
    
    # Pod-level checks
    assert pod_sc.get("runAsNonRoot") == True, "runAsNonRoot must be true at pod level"
    assert pod_sc.get("runAsUser") == EXPECTED_UID, f"runAsUser must be {EXPECTED_UID}"
    
    # Container-level checks
    assert container_sc.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem must be true"
    assert container_sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation must be false"
    assert "drop" in container_sc.get("capabilities", {}), "Must drop capabilities"
    assert "ALL" in container_sc["capabilities"]["drop"], "Must drop ALL capabilities"


def test_ac5_all_configurable_parameters_as_env_vars():
    """AC-5: All service configurable parameters exposed as environment variables with defaults"""
    with open(MANIFEST_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    env = container.get("env", [])
    env_names = [e["name"] for e in env]
    
    expected_env_vars = [
        "PRODUCT_CATALOG_PORT",
        "PRODUCT_CATALOG_DB_URL",
        "PRODUCT_CATALOG_FEATURE_FLAG_DISABLE_REVIEWS",
        "PRODUCT_CATALOG_FEATURE_FLAG_ENABLE_SLOW_QUERIES"
    ]
    
    for var in expected_env_vars:
        assert var in env_names, f"Missing required environment variable {var}"


def test_ac6_required_annotations_present():
    """AC-6: Prometheus and OpenTelemetry auto-instrumentation annotations present"""
    with open(MANIFEST_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    annotations = manifest["metadata"].get("annotations", {})
    pod_annotations = manifest["spec"]["template"]["metadata"].get("annotations", {})
    
    # Prometheus annotations
    assert annotations.get("prometheus.io/scrape") == "true", "Missing prometheus.io/scrape annotation"
    assert annotations.get("prometheus.io/port") == str(EXPECTED_PORT), "Missing prometheus.io/port annotation"
    
    # OTel instrumentation annotation
    assert pod_annotations.get("instrumentation.opentelemetry.io/inject-go") == "true", "Missing OTel Go instrumentation annotation"


def test_ac7_required_recommended_labels_present():
    """AC-7: All Kubernetes recommended labels present on Deployment and pod template"""
    with open(MANIFEST_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    deployment_labels = manifest["metadata"].get("labels", {})
    pod_labels = manifest["spec"]["template"]["metadata"].get("labels", {})
    
    expected_labels = {
        "app.kubernetes.io/name": "product-catalog",
        "app.kubernetes.io/part-of": "opentelemetry-demo",
        "app.kubernetes.io/component": "service",
        "app.kubernetes.io/version": EXPECTED_SERVICE_VERSION
    }
    
    # Check deployment labels
    for key, value in expected_labels.items():
        assert deployment_labels.get(key) == value, f"Deployment missing label {key}={value}"
    
    # Check pod template labels
    for key, value in expected_labels.items():
        assert pod_labels.get(key) == value, f"Pod template missing label {key}={value}"
