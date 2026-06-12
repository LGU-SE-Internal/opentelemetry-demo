import os
import yaml
import subprocess
import pytest
from kubernetes import client, config

# Constants matching the spec
DEPLOYMENT_PATH = "kubernetes/frontend-proxy/deployment.yaml"
SERVICE_PATH = "kubernetes/frontend-proxy/service.yaml"
EXPECTED_DEPLOYMENT_NAME = "frontend-proxy"
EXPECTED_SERVICE_NAME = "frontend-proxy"
EXPECTED_IMAGE = "nginx:stable-alpine"
EXPECTED_CONTAINER_PORT = 8080
EXPECTED_SERVICE_PORT = 80
EXPECTED_SERVICE_TARGET_PORT = 8080
EXPECTED_CONFIG_MAP_PREFIX = "frontend-proxy-nginx-config-"
EXPECTED_VOLUME_MOUNT_PATH = "/etc/nginx/conf.d"
EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 101,
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]}
}
EXPECTED_LIVENESS_PROBE = {
    "httpGet": {"path": "/healthz", "port": 8080},
    "initialDelaySeconds": 5,
    "periodSeconds": 10,
    "timeoutSeconds": 1,
    "failureThreshold": 3
}
EXPECTED_READINESS_PROBE = {
    "httpGet": {"path": "/healthz", "port": 8080},
    "initialDelaySeconds": 2,
    "periodSeconds": 5,
    "timeoutSeconds": 1,
    "failureThreshold": 2
}
EXPECTED_RESOURCES = {
    "requests": {"cpu": "100m", "memory": "128Mi"},
    "limits": {"cpu": "500m", "memory": "256Mi"}
}

def load_yaml_file(path):
    """Helper to load YAML file, raises if file does not exist"""
    assert os.path.exists(path), f"File {path} does not exist"
    with open(path, "r") as f:
        return list(yaml.safe_load_all(f))

def test_ac1_non_root_security_context():
    """AC-1: Deployment runs as non-root user with least privilege security context"""
    manifests = load_yaml_file(DEPLOYMENT_PATH)
    deployment = next(m for m in manifests if m["kind"] == "Deployment" and m["metadata"]["name"] == EXPECTED_DEPLOYMENT_NAME)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    security_context = container.get("securityContext", {})
    
    assert security_context.get("runAsNonRoot") == EXPECTED_SECURITY_CONTEXT["runAsNonRoot"]
    assert security_context.get("runAsUser") == EXPECTED_SECURITY_CONTEXT["runAsUser"]
    assert security_context.get("allowPrivilegeEscalation") == EXPECTED_SECURITY_CONTEXT["allowPrivilegeEscalation"]
    assert security_context.get("readOnlyRootFilesystem") == EXPECTED_SECURITY_CONTEXT["readOnlyRootFilesystem"]
    assert "drop" in security_context.get("capabilities", {})
    assert "ALL" in security_context["capabilities"]["drop"]
    assert "add" not in security_context.get("capabilities", {}), "No additional capabilities allowed"

def test_ac2_liveness_probe_config():
    """AC-2: Deployment has correct liveness probe configuration"""
    manifests = load_yaml_file(DEPLOYMENT_PATH)
    deployment = next(m for m in manifests if m["kind"] == "Deployment" and m["metadata"]["name"] == EXPECTED_DEPLOYMENT_NAME)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    liveness_probe = container.get("livenessProbe", {})
    
    assert liveness_probe.get("httpGet", {}).get("path") == EXPECTED_LIVENESS_PROBE["httpGet"]["path"]
    assert liveness_probe.get("httpGet", {}).get("port") == EXPECTED_LIVENESS_PROBE["httpGet"]["port"]
    assert liveness_probe.get("initialDelaySeconds") == EXPECTED_LIVENESS_PROBE["initialDelaySeconds"]
    assert liveness_probe.get("periodSeconds") == EXPECTED_LIVENESS_PROBE["periodSeconds"]
    assert liveness_probe.get("timeoutSeconds") == EXPECTED_LIVENESS_PROBE["timeoutSeconds"]
    assert liveness_probe.get("failureThreshold") == EXPECTED_LIVENESS_PROBE["failureThreshold"]

def test_ac3_readiness_probe_config():
    """AC-3: Deployment has correct readiness probe configuration"""
    manifests = load_yaml_file(DEPLOYMENT_PATH)
    deployment = next(m for m in manifests if m["kind"] == "Deployment" and m["metadata"]["name"] == EXPECTED_DEPLOYMENT_NAME)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    readiness_probe = container.get("readinessProbe", {})
    
    assert readiness_probe.get("httpGet", {}).get("path") == EXPECTED_READINESS_PROBE["httpGet"]["path"]
    assert readiness_probe.get("httpGet", {}).get("port") == EXPECTED_READINESS_PROBE["httpGet"]["port"]
    assert readiness_probe.get("initialDelaySeconds") == EXPECTED_READINESS_PROBE["initialDelaySeconds"]
    assert readiness_probe.get("periodSeconds") == EXPECTED_READINESS_PROBE["periodSeconds"]
    assert readiness_probe.get("timeoutSeconds") == EXPECTED_READINESS_PROBE["timeoutSeconds"]
    assert readiness_probe.get("failureThreshold") == EXPECTED_READINESS_PROBE["failureThreshold"]

def test_ac4_resource_limits_requests():
    """AC-4: Deployment has correct resource requests and limits"""
    manifests = load_yaml_file(DEPLOYMENT_PATH)
    deployment = next(m for m in manifests if m["kind"] == "Deployment" and m["metadata"]["name"] == EXPECTED_DEPLOYMENT_NAME)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    assert resources.get("requests", {}).get("cpu") == EXPECTED_RESOURCES["requests"]["cpu"]
    assert resources.get("requests", {}).get("memory") == EXPECTED_RESOURCES["requests"]["memory"]
    assert resources.get("limits", {}).get("cpu") == EXPECTED_RESOURCES["limits"]["cpu"]
    assert resources.get("limits", {}).get("memory") == EXPECTED_RESOURCES["limits"]["memory"]

def test_ac5_service_configuration():
    """AC-5: Service exposes port 80 as ClusterIP with correct selector and target port"""
    manifests = load_yaml_file(SERVICE_PATH)
    service = next(m for m in manifests if m["kind"] == "Service" and m["metadata"]["name"] == EXPECTED_SERVICE_NAME)
    
    assert service["spec"]["type"] == "ClusterIP"
    assert any(port["port"] == EXPECTED_SERVICE_PORT and port["targetPort"] == EXPECTED_SERVICE_TARGET_PORT for port in service["spec"]["ports"])
    assert service["spec"]["selector"].get("app.kubernetes.io/name") == EXPECTED_DEPLOYMENT_NAME

def test_ac6_config_map_volume_mount():
    """AC-6: Deployment mounts environment-specific config map to Nginx conf.d directory"""
    manifests = load_yaml_file(DEPLOYMENT_PATH)
    deployment = next(m for m in manifests if m["kind"] == "Deployment" and m["metadata"]["name"] == EXPECTED_DEPLOYMENT_NAME)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
    
    # Check volume mount exists
    volume_mount = next((vm for vm in container.get("volumeMounts", []) if vm["mountPath"] == EXPECTED_VOLUME_MOUNT_PATH), None)
    assert volume_mount is not None, f"No volume mount found for path {EXPECTED_VOLUME_MOUNT_PATH}"
    
    # Check volume references config map with correct prefix
    volume = next((v for v in volumes if v["name"] == volume_mount["name"]), None)
    assert volume is not None, f"Volume {volume_mount['name']} not found in volumes list"
    assert "configMap" in volume, "Volume is not a config map type"
    assert volume["configMap"]["name"].startswith(EXPECTED_CONFIG_MAP_PREFIX), f"Config map name does not start with {EXPECTED_CONFIG_MAP_PREFIX}"

def test_ac7_manifest_validation():
    """AC-7: All manifests pass kubectl apply --dry-run=server validation"""
    # Check deployment manifest
    result = subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "--dry-run=server", "--namespace", "default"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Deployment manifest validation failed: {result.stderr}"
    
    # Check service manifest
    result = subprocess.run(
        ["kubectl", "apply", "-f", SERVICE_PATH, "--dry-run=server", "--namespace", "default"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Service manifest validation failed: {result.stderr}"
