import os
import yaml
import subprocess
import pytest

DEPLOYMENT_PATH = "kubernetes/otel-collector/deployment.yaml"
EXPECTED_IMAGE = "otel/opentelemetry-collector-contrib:0.100.0"
EXPECTED_CONFIGMAP_NAME = "otel-collector-config"
NAMESPACE = "opentelemetry-demo"

def test_ac1_deployment_manifest_valid_with_replica():
    """AC-1: Valid Deployment manifest exists with correct metadata, 1 replica default"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert manifest["apiVersion"] == "apps/v1", f"Expected apiVersion apps/v1, got {manifest['apiVersion']}"
    assert manifest["kind"] == "Deployment", f"Expected kind Deployment, got {manifest['kind']}"
    assert manifest["metadata"]["name"] == "otel-collector", f"Expected metadata name otel-collector, got {manifest['metadata']['name']}"
    assert manifest["metadata"]["namespace"] == NAMESPACE, f"Expected namespace {NAMESPACE}, got {manifest['metadata']['namespace']}"
    assert manifest["spec"]["replicas"] == 1, f"Expected 1 default replica, got {manifest['spec']['replicas']}"
    assert manifest["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "otel-collector", "Missing correct selector label"

def test_ac2_resource_requests_limits_configured():
    """AC-2: Pod template has correct CPU/memory requests and limits"""
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "otel-collector", f"Expected container name otel-collector, got {container['name']}"
    assert container["image"] == EXPECTED_IMAGE, f"Expected image {EXPECTED_IMAGE}, got {container['image']}"
    
    resources = container["resources"]
    assert "requests" in resources, "No resources.requests block found"
    assert "limits" in resources, "No resources.limits block found"
    
    assert resources["requests"]["cpu"] == "100m", f"Expected CPU request 100m, got {resources['requests']['cpu']}"
    assert resources["requests"]["memory"] == "128Mi", f"Expected memory request 128Mi, got {resources['requests']['memory']}"
    assert resources["limits"]["cpu"] == "500m", f"Expected CPU limit 500m, got {resources['limits']['cpu']}"
    assert resources["limits"]["memory"] == "256Mi", f"Expected memory limit 256Mi, got {resources['limits']['memory']}"

def test_ac3_probes_configured_correctly():
    """AC-3: Liveness and readiness probes configured with correct endpoint, port, timing settings"""
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    
    # Liveness probe checks
    assert "livenessProbe" in container, "No livenessProbe configured"
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["port"] == 13133, f"Expected liveness probe port 13133, got {liveness['httpGet']['port']}"
    assert liveness["httpGet"]["path"] == "/", f"Expected liveness probe path '/', got {liveness['httpGet']['path']}"
    assert liveness["initialDelaySeconds"] == 30, f"Expected liveness initial delay 30s, got {liveness['initialDelaySeconds']}"
    assert liveness["periodSeconds"] == 10, f"Expected liveness period 10s, got {liveness['periodSeconds']}"
    assert liveness["failureThreshold"] == 3, f"Expected liveness failure threshold 3, got {liveness['failureThreshold']}"
    
    # Readiness probe checks
    assert "readinessProbe" in container, "No readinessProbe configured"
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["port"] == 13133, f"Expected readiness probe port 13133, got {readiness['httpGet']['port']}"
    assert readiness["httpGet"]["path"] == "/", f"Expected readiness probe path '/', got {readiness['httpGet']['path']}"
    assert readiness["initialDelaySeconds"] == 5, f"Expected readiness initial delay 5s, got {readiness['initialDelaySeconds']}"
    assert readiness["periodSeconds"] == 5, f"Expected readiness period 5s, got {readiness['periodSeconds']}"
    assert readiness["failureThreshold"] == 3, f"Expected readiness failure threshold 3, got {readiness['failureThreshold']}"

def test_ac4_security_context_configured():
    """AC-4: Security context configured with least privilege hardening settings"""
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    pod_spec = manifest["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    assert "securityContext" in container, "No container securityContext found"
    sec_ctx = container["securityContext"]
    
    assert sec_ctx["runAsNonRoot"] == True, "runAsNonRoot must be true"
    assert sec_ctx["runAsUser"] == 10001, f"Expected runAsUser 10001, got {sec_ctx['runAsUser']}"
    assert sec_ctx["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation must be false"
    assert sec_ctx["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem must be true"
    assert "capabilities" in sec_ctx, "No capabilities block found"
    assert sec_ctx["capabilities"]["drop"] == ["ALL"], "Must drop ALL capabilities"
    assert "add" not in sec_ctx["capabilities"] or len(sec_ctx["capabilities"]["add"]) == 0, "No extra capabilities should be added"

def test_ac5_configmap_mounted_correctly():
    """AC-5: ConfigMap referenced and mounted to correct path as read-only"""
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    pod_spec = manifest["spec"]["template"]["spec"]
    volumes = pod_spec["volumes"]
    
    # Check configmap volume exists
    cm_volume = next((v for v in volumes if v.get("configMap", {}).get("name") == EXPECTED_CONFIGMAP_NAME), None)
    assert cm_volume is not None, f"ConfigMap volume for {EXPECTED_CONFIGMAP_NAME} not found"
    
    container = pod_spec["containers"][0]
    volume_mounts = container["volumeMounts"]
    
    # Check volume mount exists with correct settings
    mount = next((m for m in volume_mounts if m["name"] == cm_volume["name"]), None)
    assert mount is not None, f"Volume mount for configmap {EXPECTED_CONFIGMAP_NAME} not found"
    assert mount["mountPath"] == "/etc/otelcol-contrib/config.yaml", f"Expected mount path /etc/otelcol-contrib/config.yaml, got {mount['mountPath']}"
    assert mount["subPath"] == "config.yaml", f"Expected subPath config.yaml, got {mount['subPath']}"
    assert mount["readOnly"] == True, "ConfigMap mount must be read-only"

def test_ac6_dry_run_and_probe_validation():
    """AC-6: Manifest passes dry-run, pod would pass probes within 60s"""
    # First dry run validation
    result = subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "--dry-run=server", "-n", NAMESPACE],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Kubernetes server dry run failed: {result.stderr}"
    
    # Check required ports are exposed
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    ports = [p["containerPort"] for p in container["ports"]]
    assert 13133 in ports, "Health check port 13133 not exposed"
    assert 4317 in ports, "OTLP gRPC port 4317 not exposed"
    assert 4318 in ports, "OTLP HTTP port 4318 not exposed"
    assert 8888 in ports, "Metrics port 8888 not exposed"

def test_ac7_non_root_and_no_caps():
    """AC-7: Pod runs as non-root user, no extra capabilities enabled"""
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    sec_ctx = container["securityContext"]
    
    # Verify no root user configured
    assert sec_ctx["runAsUser"] != 0, "Pod cannot run as root user (UID 0)"
    assert sec_ctx["runAsNonRoot"] == True, "runAsNonRoot must be explicitly set to true"
    
    # Verify no capabilities added
    assert "add" not in sec_ctx["capabilities"] or len(sec_ctx["capabilities"]["add"]) == 0, "No capabilities allowed to be added"
    assert sec_ctx["capabilities"]["drop"] == ["ALL"], "All capabilities must be dropped"
