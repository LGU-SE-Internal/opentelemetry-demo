import os
import yaml
import subprocess
import pytest

DEPLOYMENT_PATH = "kubernetes/load-generator/deployment.yaml"
EXPECTED_ENV_VARS = ["TARGET_HOST", "OTEL_EXPORTER_OTLP_ENDPOINT", "LOAD_RATE", "LOAD_DURATION"]

def test_ac1_deployment_manifest_exists_and_valid_meta():
    # AC-1: Valid Deployment manifest exists with correct apiVersion, kind, name
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert manifest["apiVersion"] == "apps/v1", f"Expected apiVersion apps/v1, got {manifest['apiVersion']}"
    assert manifest["kind"] == "Deployment", f"Expected kind Deployment, got {manifest['kind']}"
    assert manifest["metadata"]["name"] == "load-generator", f"Expected metadata name load-generator, got {manifest['metadata']['name']}"
    assert manifest["metadata"]["labels"]["app.kubernetes.io/name"] == "load-generator", "Missing required metadata label"

def test_ac2_resource_limits_and_requests_configured():
    # AC-2: Resources block with correct CPU/memory requests and limits
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container, "No resources block found in container spec"
    
    resources = container["resources"]
    assert "requests" in resources, "No resources.requests block"
    assert "limits" in resources, "No resources.limits block"
    
    assert resources["requests"]["cpu"] == "250m", f"Expected CPU request 250m, got {resources['requests']['cpu']}"
    assert resources["requests"]["memory"] == "256Mi", f"Expected memory request 256Mi, got {resources['requests']['memory']}"
    assert resources["limits"]["cpu"] == "500m", f"Expected CPU limit 500m, got {resources['limits']['cpu']}"
    assert resources["limits"]["memory"] == "512Mi", f"Expected memory limit 512Mi, got {resources['limits']['memory']}"

def test_ac3_liveness_probe_configured():
    # AC-3: Liveness probe with correct endpoint, port, timing settings
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "No livenessProbe found"
    
    probe = container["livenessProbe"]
    assert probe["httpGet"]["path"] == "/health/live", f"Expected liveness path /health/live, got {probe['httpGet']['path']}"
    assert probe["httpGet"]["port"] == 8080, f"Expected liveness port 8080, got {probe['httpGet']['port']}"
    assert probe["initialDelaySeconds"] == 5, f"Expected initialDelaySeconds 5, got {probe['initialDelaySeconds']}"
    assert probe["periodSeconds"] == 10, f"Expected periodSeconds 10, got {probe['periodSeconds']}"
    assert probe["failureThreshold"] == 3, f"Expected failureThreshold 3, got {probe['failureThreshold']}"

def test_ac4_readiness_probe_configured():
    # AC-4: Readiness probe with correct endpoint, port, timing settings
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "readinessProbe" in container, "No readinessProbe found"
    
    probe = container["readinessProbe"]
    assert probe["httpGet"]["path"] == "/health/ready", f"Expected readiness path /health/ready, got {probe['httpGet']['path']}"
    assert probe["httpGet"]["port"] == 8080, f"Expected readiness port 8080, got {probe['httpGet']['port']}"
    assert probe["initialDelaySeconds"] == 5, f"Expected initialDelaySeconds 5, got {probe['initialDelaySeconds']}"
    assert probe["periodSeconds"] == 10, f"Expected periodSeconds 10, got {probe['periodSeconds']}"
    assert probe["failureThreshold"] == 3, f"Expected failureThreshold 3, got {probe['failureThreshold']}"

def test_ac5_security_context_configured():
    # AC-5: Non-root security context with all required hardening settings
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    pod_spec = manifest["spec"]["template"]["spec"]
    assert "securityContext" in pod_spec, "No pod-level securityContext found"
    pod_sc = pod_spec["securityContext"]
    assert pod_sc["runAsNonRoot"] == True, "runAsNonRoot must be true"
    assert pod_sc["runAsUser"] == 10001, f"Expected runAsUser 10001, got {pod_sc['runAsUser']}"
    assert pod_sc["runAsGroup"] == 10001, f"Expected runAsGroup 10001, got {pod_sc['runAsGroup']}"
    
    container = pod_spec["containers"][0]
    assert "securityContext" in container, "No container-level securityContext found"
    container_sc = container["securityContext"]
    assert container_sc["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation must be false"
    assert container_sc["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem must be true"
    assert "capabilities" in container_sc, "No capabilities block found"
    assert container_sc["capabilities"]["drop"] == ["ALL"], "Must drop all capabilities"

def test_ac6_environment_variables_exposed():
    # AC-6: All required environment variables are exposed with defaults, no hardcoded values
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "env" in container, "No env block found in container spec"
    
    env_vars = [env["name"] for env in container["env"]]
    for expected_var in EXPECTED_ENV_VARS:
        assert expected_var in env_vars, f"Missing required environment variable {expected_var}"
    
    # Check no hardcoded values that should be configurable
    for env in container["env"]:
        # Value should come from configmap/fieldRef or have a configurable default, not hardcoded fixed target
        if env["name"] == "TARGET_HOST":
            assert env["value"] != "frontend", "TARGET_HOST should not be hardcoded to frontend, must be configurable"

def test_ac7_dry_run_validation_passes():
    # AC-7: Manifest passes kubectl server dry-run validation for Kubernetes 1.24+
    result = subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "--dry-run=server"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Dry run validation failed: {result.stderr}"
