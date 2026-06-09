#!/usr/bin/env python3
import os
import time
import yaml
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "./kubernetes/flagd-ui-deployment.yaml"
SERVICE_PATH = "./kubernetes/flagd-ui-service.yaml"
EXPECTED_LABELS = {
    "app.kubernetes.io/name": "flagd-ui",
    "app.kubernetes.io/part-of": "opentelemetry-demo"
}
EXPECTED_ENV_VARS = [
    "FLAGD_UI_TLS_ENABLED",
    "FLAGD_UI_TLS_CERT_PATH",
    "FLAGD_UI_TLS_KEY_PATH",
    "FLAGD_UI_MTLS_ENABLED",
    "FLAGD_UI_MTLS_CA_CERT_PATH"
]

def test_ac1_deployment_has_resource_limits_requests():
    """AC-1: Deployment manifest exists with resource requests (100m CPU / 128Mi memory) and limits (500m CPU / 512Mi memory)"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    assert dep["apiVersion"] == "apps/v1", "Deployment apiVersion should be apps/v1"
    assert dep["kind"] == "Deployment", "Kind should be Deployment"
    assert dep["metadata"]["name"] == "flagd-ui", "Deployment name should be flagd-ui"
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert resources, "Resource requirements not configured"
    
    # Validate requests
    assert "requests" in resources, "Resource requests not defined"
    assert resources["requests"]["cpu"] in ["100m", "0.1"], "CPU request should be at least 100m"
    assert resources["requests"]["memory"] in ["128Mi", "128M", "134217728"], "Memory request should be at least 128Mi"
    
    # Validate limits
    assert "limits" in resources, "Resource limits not defined"
    assert resources["limits"]["cpu"] in ["500m", "0.5"], "CPU limit should be at most 500m"
    assert resources["limits"]["memory"] in ["512Mi", "512M", "536870912"], "Memory limit should be at most 512Mi"

def test_ac2_liveness_probe_configured_correctly():
    """AC-2: Deployment includes liveness probe pointing to /health/live on port 4000 with initial delay 30s, period 10s, failure threshold 3"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    liveness = container.get("livenessProbe", {})
    assert liveness, "Liveness probe not configured"
    assert liveness["httpGet"]["port"] == 4000, "Liveness probe should use port 4000"
    assert liveness["httpGet"]["path"] == "/health/live", "Liveness probe path should be /health/live"
    assert liveness["initialDelaySeconds"] == 30, "Liveness initial delay should be 30s"
    assert liveness["periodSeconds"] == 10, "Liveness period should be 10s"
    assert liveness["failureThreshold"] == 3, "Liveness failure threshold should be 3"

def test_ac3_readiness_probe_configured_correctly():
    """AC-3: Deployment includes readiness probe pointing to /health/ready on port 4000 with initial delay 5s, period 5s, failure threshold 3"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    readiness = container.get("readinessProbe", {})
    assert readiness, "Readiness probe not configured"
    assert readiness["httpGet"]["port"] == 4000, "Readiness probe should use port 4000"
    assert readiness["httpGet"]["path"] == "/health/ready", "Readiness probe path should be /health/ready"
    assert readiness["initialDelaySeconds"] == 5, "Readiness initial delay should be 5s"
    assert readiness["periodSeconds"] == 5, "Readiness period should be 5s"
    assert readiness["failureThreshold"] == 3, "Readiness failure threshold should be 3"

def test_ac4_security_context_configured():
    """AC-4: Deployment security context runs as non-root (UID 1000, GID 1000), allowPrivilegeEscalation false, runAsNonRoot true, readOnlyRootFilesystem true, no capabilities added"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    # Check pod-level security context
    pod_sc = dep["spec"]["template"]["spec"].get("securityContext", {})
    container_sc = dep["spec"]["template"]["spec"]["containers"][0].get("securityContext", {})
    
    # Validate non-root configuration
    assert pod_sc.get("runAsNonRoot") == True or container_sc.get("runAsNonRoot") == True, "Should run as non-root user"
    assert pod_sc.get("runAsUser") == 1000 or container_sc.get("runAsUser") == 1000, "Should run as UID 1000"
    assert pod_sc.get("runAsGroup") == 1000 or container_sc.get("runAsGroup") == 1000, "Should run as GID 1000"
    
    # Validate security hardening
    assert pod_sc.get("allowPrivilegeEscalation") == False or container_sc.get("allowPrivilegeEscalation") == False, "Privilege escalation should be disabled"
    assert pod_sc.get("readOnlyRootFilesystem") == True or container_sc.get("readOnlyRootFilesystem") == True, "Root filesystem should be read-only"
    
    # Validate no capabilities added
    capabilities = container_sc.get("capabilities", {})
    assert "add" not in capabilities or len(capabilities["add"]) == 0, "No additional capabilities should be added"

def test_ac5_tls_env_vars_configured_with_defaults():
    """AC-5: Deployment includes all TLS/mTLS environment variables with defaults that disable TLS/mTLS"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env = container.get("env", [])
    env_vars = {e["name"]: e.get("value", "") for e in env}
    
    # Check all expected variables exist
    for var in EXPECTED_ENV_VARS:
        assert var in env_vars, f"Environment variable {var} missing from deployment"
    
    # Check default values disable TLS/mTLS
    assert env_vars["FLAGD_UI_TLS_ENABLED"].lower() in ["false", "0", ""], "FLAGD_UI_TLS_ENABLED default should be false"
    assert env_vars["FLAGD_UI_MTLS_ENABLED"].lower() in ["false", "0", ""], "FLAGD_UI_MTLS_ENABLED default should be false"

def test_ac6_service_exists_and_configured():
    """AC-6: Service manifest exists with selector matching deployment labels, exposes port 8080 targeting container port 4000"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    
    assert svc["apiVersion"] == "v1", "Service apiVersion should be v1"
    assert svc["kind"] == "Service", "Kind should be Service"
    assert svc["metadata"]["name"] == "flagd-ui", "Service name should be flagd-ui"
    
    # Validate selector matches deployment labels
    selector = svc["spec"]["selector"]
    assert selector["app.kubernetes.io/name"] == "flagd-ui", "Service selector should match deployment app label"
    
    # Validate port configuration
    ports = {p["port"]: p for p in svc["spec"]["ports"]}
    assert 8080 in ports, "Service should expose port 8080"
    assert ports[8080]["targetPort"] == 4000, "Service port 8080 should target container port 4000"
    
    # Validate default service type
    assert svc["spec"]["type"] in ["ClusterIP", "clusterip"], "Default service type should be ClusterIP"

def test_ac7_pod_running_and_service_reachable():
    """AC-7: Pod reaches Running state within 60s, service is reachable at http://flagd-ui:8080 from within cluster"""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"  # Adjust if demo uses different namespace
    
    # Wait for pod to be running
    start = time.time()
    pod_name = None
    while time.time() - start < 60:
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=flagd-ui")
            if len(pods.items) > 0 and pods.items[0].status.phase == "Running":
                pod_name = pods.items[0].metadata.name
                break
        except ApiException:
            pass
        time.sleep(5)
    assert pod_name is not None, "flagd-ui pod not in Running state after 60s"
    
    # Verify health checks are passing
    try:
        pod_status = v1.read_namespaced_pod_status(pod_name, namespace)
        for condition in pod_status.status.conditions:
            if condition.type == "Ready":
                assert condition.status == "True", "Pod is not in Ready state"
    except ApiException as e:
        assert False, f"Failed to check pod status: {str(e)}"
    
    # Test service connectivity
    try:
        # This test runs inside the cluster so we can access the service directly
        resp = requests.get("http://flagd-ui:8080/health/live", timeout=5)
        assert resp.status_code == 200, f"Service health check failed with status {resp.status_code}"
    except requests.exceptions.RequestException as e:
        assert False, f"Failed to connect to flagd-ui service: {str(e)}"
