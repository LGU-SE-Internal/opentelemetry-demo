import yaml
import os
import pytest

DEPLOYMENT_PATH = "./kubernetes/accounting-service/deployment.yaml"
EXPECTED_PATH_PATTERN = "./k8s/accounting-service-deployment.yaml"  # Or ./k8s/accounting-service/deployment.yaml as per other services
EXPECTED_PROBE_PATH = "/health"
EXPECTED_PORT = 8080
EXPECTED_OTEL_ENV_VARS = [
    "OTEL_SERVICE_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_TRACES_EXPORTER",
    "OTEL_METRICS_EXPORTER",
    "OTEL_LOGS_EXPORTER"
]

@pytest.fixture(scope="module")
def deployment_manifest():
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file not found at {DEPLOYMENT_PATH}"
    with open(DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

def test_ac1_deployment_exists_at_correct_path_follows_convention():
    """AC-1: Valid Deployment manifest exists at correct path following repo naming convention"""
    # Check path matches other services pattern (either k8s/<service>-deployment.yaml or k8s/<service>/deployment.yaml)
    assert os.path.exists(EXPECTED_PATH_PATTERN) or os.path.exists("./k8s/accounting-service/deployment.yaml"), \
        f"Deployment not found at standard path {EXPECTED_PATH_PATTERN} or ./k8s/accounting-service/deployment.yaml"

def test_ac2_liveness_readiness_probes_configured_correctly(deployment_manifest):
    """AC-2: Liveness and Readiness probes configured targeting /health endpoint with correct parameters"""
    container = deployment_manifest["spec"]["template"]["spec"]["containers"][0]
    
    # Check liveness probe
    assert "livenessProbe" in container, "Missing livenessProbe"
    assert container["livenessProbe"]["httpGet"]["path"] == EXPECTED_PROBE_PATH, f"Liveness probe path should be {EXPECTED_PROBE_PATH}"
    assert container["livenessProbe"]["httpGet"]["port"] == EXPECTED_PORT, f"Liveness probe port should be {EXPECTED_PORT}"
    assert container["livenessProbe"]["initialDelaySeconds"] == 30, "Liveness probe initialDelaySeconds should be 30 (matches other dotnet services)"
    assert container["livenessProbe"]["periodSeconds"] == 10, "Liveness probe periodSeconds should be 10"
    assert container["livenessProbe"]["failureThreshold"] == 3, "Liveness probe failureThreshold should be 3"
    
    # Check readiness probe
    assert "readinessProbe" in container, "Missing readinessProbe"
    assert container["readinessProbe"]["httpGet"]["path"] == EXPECTED_PROBE_PATH, f"Readiness probe path should be {EXPECTED_PROBE_PATH}"
    assert container["readinessProbe"]["httpGet"]["port"] == EXPECTED_PORT, f"Readiness probe port should be {EXPECTED_PORT}"
    assert container["readinessProbe"]["initialDelaySeconds"] == 5, "Readiness probe initialDelaySeconds should be 5 (matches other dotnet services)"
    assert container["readinessProbe"]["periodSeconds"] == 5, "Readiness probe periodSeconds should be 5"
    assert container["readinessProbe"]["failureThreshold"] == 3, "Readiness probe failureThreshold should be 3"

def test_ac3_resource_requests_limits_defined(deployment_manifest):
    """AC-3: Explicit CPU and memory resource requests and limits defined aligned with other dotnet services"""
    container = deployment_manifest["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container, "Missing resources section"
    
    requests = container["resources"].get("requests", {})
    limits = container["resources"].get("limits", {})
    
    assert "cpu" in requests, "Missing CPU request"
    assert "memory" in requests, "Missing memory request"
    assert "cpu" in limits, "Missing CPU limit"
    assert "memory" in limits, "Missing memory limit"
    
    # Check values match expected for dotnet services
    assert requests["cpu"] == "100m", "CPU request should be 100m"
    assert requests["memory"] == "128Mi", "Memory request should be 128Mi"
    assert limits["cpu"] == "500m", "CPU limit should be 500m"
    assert limits["memory"] == "256Mi", "Memory limit should be 256Mi"

def test_ac4_security_context_configured_securely(deployment_manifest):
    """AC-4: Pod and container security context configured with non-root execution and secure settings"""
    pod_spec = deployment_manifest["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Check container security context
    assert "securityContext" in container, "Missing container securityContext"
    sec_ctx = container["securityContext"]
    assert sec_ctx.get("runAsNonRoot") == True, "runAsNonRoot should be true"
    assert sec_ctx.get("runAsUser", 0) > 0, "runAsUser should be > 0 (non-root)"
    assert sec_ctx.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem should be true"
    assert sec_ctx.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation should be false"
    
    # Check capabilities are dropped
    assert "capabilities" in sec_ctx, "Missing capabilities section in securityContext"
    assert "drop" in sec_ctx["capabilities"], "Missing capabilities drop list"
    assert "ALL" in sec_ctx["capabilities"]["drop"], "All capabilities should be dropped"

def test_ac5_otel_environment_variables_present(deployment_manifest):
    """AC-5: All standard OpenTelemetry environment variables present"""
    container = deployment_manifest["spec"]["template"]["spec"]["containers"][0]
    env_vars = [env["name"] for env in container.get("env", [])]
    
    for otel_var in EXPECTED_OTEL_ENV_VARS:
        assert otel_var in env_vars, f"Missing required OTel environment variable: {otel_var}"

def test_ac6_manifest_passes_kubernetes_validation():
    """AC-6: Manifest passes kubectl validation without errors for Kubernetes 1.24+"""
    import subprocess
    result = subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "--dry-run=client", "--validate=strict"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl validation failed: {result.stderr}"
