import yaml
import pytest
from pathlib import Path

DEPLOYMENT_PATH = Path(__file__).parent.parent.parent.parent / "k8s" / "recommendation-service" / "deployment.yaml"

@pytest.fixture(scope="module")
def deployment_manifest():
    assert DEPLOYMENT_PATH.exists(), f"Deployment file not found at {DEPLOYMENT_PATH}"
    with open(DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture(scope="module")
def recommendation_container(deployment_manifest):
    containers = deployment_manifest["spec"]["template"]["spec"]["containers"]
    rec_container = next(c for c in containers if c["name"] == "recommendation-service")
    assert rec_container is not None, "recommendation-service container not found in deployment"
    return rec_container

def test_ac1_resource_fields_present(deployment_manifest, recommendation_container):
    """AC-1: Valid non-empty values for all four resource fields: requests.cpu, requests.memory, limits.cpu, limits.memory"""
    assert "resources" in recommendation_container, "resources field missing from container"
    resources = recommendation_container["resources"]
    
    assert "requests" in resources, "resources.requests missing"
    requests = resources["requests"]
    assert "cpu" in requests and requests["cpu"], "resources.requests.cpu missing or empty"
    assert "memory" in requests and requests["memory"], "resources.requests.memory missing or empty"
    
    assert "limits" in resources, "resources.limits missing"
    limits = resources["limits"]
    assert "cpu" in limits and limits["cpu"], "resources.limits.cpu missing or empty"
    assert "memory" in limits and limits["memory"], "resources.limits.memory missing or empty"

def test_ac2_liveness_readiness_probes_configured(recommendation_container):
    """AC-2: Both livenessProbe and readinessProbe configured with HTTP GET on /health endpoint"""
    for probe_name in ["livenessProbe", "readinessProbe"]:
        assert probe_name in recommendation_container, f"{probe_name} missing from container"
        probe = recommendation_container[probe_name]
        
        assert "httpGet" in probe, f"{probe_name}.httpGet missing"
        http_get = probe["httpGet"]
        assert http_get.get("path") == "/health", f"{probe_name}.httpGet.path should be /health"
        assert "port" in http_get and http_get["port"], f"{probe_name}.httpGet.port missing or empty"
        
        assert probe.get("initialDelaySeconds", 0) > 0, f"{probe_name}.initialDelaySeconds must be positive integer"
        assert probe.get("periodSeconds", 0) > 0, f"{probe_name}.periodSeconds must be positive integer"
        assert probe.get("timeoutSeconds", 0) > 0, f"{probe_name}.timeoutSeconds must be positive integer"

def test_ac3_pod_security_context_run_as_non_root(deployment_manifest):
    """AC-3: Pod security context sets runAsNonRoot: true"""
    pod_spec = deployment_manifest["spec"]["template"]["spec"]
    assert "securityContext" in pod_spec, "Pod securityContext missing"
    security_context = pod_spec["securityContext"]
    assert security_context.get("runAsNonRoot") is True, "runAsNonRoot must be set to true"

def test_ac4_container_security_context_hardened(recommendation_container):
    """AC-4: Container security context sets readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, drops all capabilities"""
    assert "securityContext" in recommendation_container, "Container securityContext missing"
    sec_ctx = recommendation_container["securityContext"]
    
    assert sec_ctx.get("readOnlyRootFilesystem") is True, "readOnlyRootFilesystem must be true"
    assert sec_ctx.get("allowPrivilegeEscalation") is False, "allowPrivilegeEscalation must be false"
    
    assert "capabilities" in sec_ctx, "capabilities field missing in container securityContext"
    assert "drop" in sec_ctx["capabilities"], "capabilities.drop missing"
    assert "ALL" in sec_ctx["capabilities"]["drop"], "ALL capabilities must be dropped"

def test_ac5_standard_kubernetes_labels_present(deployment_manifest):
    """AC-5: Pod template includes all standard Kubernetes recommended labels with correct values"""
    pod_metadata = deployment_manifest["spec"]["template"]["metadata"]
    assert "labels" in pod_metadata, "Pod template labels missing"
    labels = pod_metadata["labels"]
    
    required_labels = [
        "app.kubernetes.io/name",
        "app.kubernetes.io/instance",
        "app.kubernetes.io/version",
        "app.kubernetes.io/component",
        "app.kubernetes.io/part-of",
        "app.kubernetes.io/managed-by"
    ]
    for label in required_labels:
        assert label in labels and labels[label], f"Required label {label} missing or empty"
    assert labels["app.kubernetes.io/component"] == "recommendation-service", "app.kubernetes.io/component should be recommendation-service"

def test_ac6_prometheus_annotations_present(deployment_manifest):
    """AC-6: Pod template includes Prometheus scraping annotations"""
    pod_metadata = deployment_manifest["spec"]["template"]["metadata"]
    assert "annotations" in pod_metadata, "Pod template annotations missing"
    annotations = pod_metadata["annotations"]
    
    assert annotations.get("prometheus.io/scrape") == "true", "prometheus.io/scrape should be 'true'"
    assert "prometheus.io/port" in annotations and annotations["prometheus.io/port"], "prometheus.io/port missing or empty"
    assert annotations.get("prometheus.io/path") == "/metrics", "prometheus.io/path should be /metrics"

def test_ac7_termination_grace_period_set(deployment_manifest):
    """AC-7: terminationGracePeriodSeconds set exactly to 10"""
    pod_spec = deployment_manifest["spec"]["template"]["spec"]
    assert "terminationGracePeriodSeconds" in pod_spec, "terminationGracePeriodSeconds missing"
    assert pod_spec["terminationGracePeriodSeconds"] == 10, "terminationGracePeriodSeconds should be exactly 10"
