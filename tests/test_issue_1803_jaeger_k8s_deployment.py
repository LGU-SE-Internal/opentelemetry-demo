#!/usr/bin/env python3
import subprocess
import time
import pytest
import yaml
import os

JAEGER_MANIFEST_DIR = "kubernetes/jaeger/"
JAEGER_DEPLOYMENT_PATH = os.path.join(JAEGER_MANIFEST_DIR, "deployment.yaml")
TEST_NAMESPACE = "test-jaeger-{}".format(int(time.time()))

def run_kubectl(cmd, check=True, namespace=None):
    base_cmd = ["kubectl"]
    if namespace:
        base_cmd.extend(["-n", namespace])
    base_cmd.extend(cmd.split())
    result = subprocess.run(base_cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Kubectl command failed: {cmd}\nStderr: {result.stderr}")
    return result

def get_deployment_yaml():
    if not os.path.exists(JAEGER_DEPLOYMENT_PATH):
        return None
    with open(JAEGER_DEPLOYMENT_PATH, 'r') as f:
        docs = list(yaml.safe_load_all(f))
        for doc in docs:
            if doc and doc.get("kind") == "Deployment":
                return doc
    return None

@pytest.fixture(scope="module", autouse=True)
def setup_namespace():
    run_kubectl(f"create namespace {TEST_NAMESPACE}")
    yield
    run_kubectl(f"delete namespace {TEST_NAMESPACE}", check=False)

def test_ac1_resource_limits_requests():
    """AC-1: Verify deployment has correct CPU/memory requests and limits"""
    dep = get_deployment_yaml()
    assert dep is not None, "Jaeger deployment manifest not found"
    containers = dep["spec"]["template"]["spec"]["containers"]
    jaeger_container = next(c for c in containers if "jaeger" in c["name"].lower())
    
    resources = jaeger_container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    
    assert requests.get("cpu") == "100m", f"Expected CPU request 100m, got {requests.get('cpu')}"
    assert requests.get("memory") == "256Mi", f"Expected memory request 256Mi, got {requests.get('memory')}"
    assert limits.get("cpu") == "500m", f"Expected CPU limit 500m, got {limits.get('cpu')}"
    assert limits.get("memory") == "1Gi", f"Expected memory limit 1Gi, got {limits.get('memory')}"

def test_ac2_liveness_probe():
    """AC-2: Verify liveness probe configuration"""
    dep = get_deployment_yaml()
    assert dep is not None, "Jaeger deployment manifest not found"
    containers = dep["spec"]["template"]["spec"]["containers"]
    jaeger_container = next(c for c in containers if "jaeger" in c["name"].lower())
    
    liveness = jaeger_container.get("livenessProbe", {})
    assert liveness, "Liveness probe not defined"
    http_get = liveness.get("httpGet", {})
    assert http_get.get("path") == "/livez", f"Expected liveness path /livez, got {http_get.get('path')}"
    assert http_get.get("port") == 14269, f"Expected liveness port 14269, got {http_get.get('port')}"
    assert liveness.get("initialDelaySeconds") == 30, f"Expected initialDelaySeconds 30, got {liveness.get('initialDelaySeconds')}"
    assert liveness.get("periodSeconds") == 10, f"Expected periodSeconds 10, got {liveness.get('periodSeconds')}"
    assert liveness.get("failureThreshold") == 3, f"Expected failureThreshold 3, got {liveness.get('failureThreshold')}"

def test_ac3_readiness_probe():
    """AC-3: Verify readiness probe configuration"""
    dep = get_deployment_yaml()
    assert dep is not None, "Jaeger deployment manifest not found"
    containers = dep["spec"]["template"]["spec"]["containers"]
    jaeger_container = next(c for c in containers if "jaeger" in c["name"].lower())
    
    readiness = jaeger_container.get("readinessProbe", {})
    assert readiness, "Readiness probe not defined"
    http_get = readiness.get("httpGet", {})
    assert http_get.get("path") == "/readyz", f"Expected readiness path /readyz, got {http_get.get('path')}"
    assert http_get.get("port") == 14269, f"Expected readiness port 14269, got {http_get.get('port')}"
    assert readiness.get("initialDelaySeconds") == 5, f"Expected initialDelaySeconds 5, got {readiness.get('initialDelaySeconds')}"
    assert readiness.get("periodSeconds") == 5, f"Expected periodSeconds 5, got {readiness.get('periodSeconds')}"
    assert readiness.get("failureThreshold") == 3, f"Expected failureThreshold 3, got {readiness.get('failureThreshold')}"

def test_ac4_security_context():
    """AC-4: Verify non-root security context configuration"""
    dep = get_deployment_yaml()
    assert dep is not None, "Jaeger deployment manifest not found"
    pod_spec = dep["spec"]["template"]["spec"]
    pod_security = pod_spec.get("securityContext", {})
    containers = pod_spec["containers"]
    jaeger_container = next(c for c in containers if "jaeger" in c["name"].lower())
    container_security = jaeger_container.get("securityContext", {})
    
    # Check both pod and container security contexts for required settings
    all_security = {**pod_security, **container_security}
    
    assert all_security.get("runAsUser") == 10001, f"Expected runAsUser 10001, got {all_security.get('runAsUser')}"
    assert all_security.get("runAsNonRoot") == True, f"Expected runAsNonRoot true, got {all_security.get('runAsNonRoot')}"
    assert all_security.get("allowPrivilegeEscalation") == False, f"Expected allowPrivilegeEscalation false, got {all_security.get('allowPrivilegeEscalation')}"
    assert all_security.get("readOnlyRootFilesystem") == True, f"Expected readOnlyRootFilesystem true, got {all_security.get('readOnlyRootFilesystem')}"
    capabilities = all_security.get("capabilities", {})
    assert "drop" in capabilities, "No capabilities dropped in security context"
    assert "ALL" in capabilities["drop"], f"Expected ALL capabilities dropped, got {capabilities.get('drop')}"

def test_ac5_configmap_mount():
    """AC-5: Verify ConfigMap jaeger-config is mounted to /etc/jaeger/config.yml"""
    dep = get_deployment_yaml()
    assert dep is not None, "Jaeger deployment manifest not found"
    pod_spec = dep["spec"]["template"]["spec"]
    
    # Check ConfigMap volume exists
    volumes = pod_spec.get("volumes", [])
    config_volume = next((v for v in volumes if v.get("configMap", {}).get("name") == "jaeger-config"), None)
    assert config_volume is not None, "jaeger-config ConfigMap volume not found in deployment"
    
    # Check mount path in container
    containers = pod_spec["containers"]
    jaeger_container = next(c for c in containers if "jaeger" in c["name"].lower())
    volume_mounts = jaeger_container.get("volumeMounts", [])
    config_mount = next((m for m in volume_mounts if m["name"] == config_volume["name"]), None)
    assert config_mount is not None, "jaeger-config volume not mounted to container"
    assert config_mount.get("mountPath") == "/etc/jaeger/config.yml", f"Expected mount path /etc/jaeger/config.yml, got {config_mount.get('mountPath')}"
    assert config_mount.get("subPath") == "config.yml", f"Expected subPath config.yml, got {config_mount.get('subPath')}"

def test_ac6_tls_environment_variables():
    """AC-6: Verify TLS environment variables are configurable and not hardcoded"""
    dep = get_deployment_yaml()
    assert dep is not None, "Jaeger deployment manifest not found"
    containers = dep["spec"]["template"]["spec"]["containers"]
    jaeger_container = next(c for c in containers if "jaeger" in c["name"].lower())
    env = jaeger_container.get("env", [])
    
    required_vars = [
        "COLLECTOR_TLS_ENABLED",
        "COLLECTOR_TLS_CERT_PATH",
        "COLLECTOR_TLS_KEY_PATH",
        "COLLECTOR_TLS_CLIENT_CA_PATH"
    ]
    
    for var_name in required_vars:
        var = next((v for v in env if v["name"] == var_name), None)
        assert var is not None, f"Environment variable {var_name} not found in deployment"
        # Verify variable uses valueFrom or is templated, not hardcoded
        assert "valueFrom" in var or var.get("value") is None or "{{" in str(var.get("value")), f"Environment variable {var_name} is hardcoded, should be configurable"

def test_ac7_deployment_success():
    """AC-7: Verify kubectl apply creates resources and pod reaches Ready state within 60s"""
    # First check manifest directory exists
    assert os.path.exists(JAEGER_MANIFEST_DIR), f"Jaeger manifest directory {JAEGER_MANIFEST_DIR} does not exist"
    
    # Apply manifests
    run_kubectl(f"apply -f {JAEGER_MANIFEST_DIR}", namespace=TEST_NAMESPACE)
    
    # Wait for pod to be ready
    start_time = time.time()
    ready = False
    while time.time() - start_time < 60:
        result = run_kubectl("get pods -l app.kubernetes.io/name=jaeger -o jsonpath='{.items[*].status.conditions[?(@.type==\"Ready\")].status}'", 
                          namespace=TEST_NAMESPACE, check=False)
        if result.stdout.strip() == "True":
            ready = True
            break
        time.sleep(2)
    
    assert ready, "Jaeger pod did not reach Ready state within 60 seconds"

def test_ac8_configmap_rollout_trigger():
    """AC-8: Verify ConfigMap updates trigger deployment rollout restart"""
    dep = get_deployment_yaml()
    assert dep is not None, "Jaeger deployment manifest not found"
    
    # Check for config checksum annotation in pod template
    annotations = dep["spec"]["template"].get("metadata", {}).get("annotations", {})
    checksum_annotation = next((k for k in annotations.keys() if "checksum/config" in k.lower()), None)
    assert checksum_annotation is not None, "Config checksum annotation not found in pod template"
    assert "{{" in annotations[checksum_annotation], "Config checksum annotation is not templated to track ConfigMap changes"
