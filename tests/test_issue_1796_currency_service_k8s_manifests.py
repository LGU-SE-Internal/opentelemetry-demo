#!/usr/bin/env python3
import os
import subprocess
import yaml
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "kubernetes/currency-service/deployment.yaml"
SERVICE_PATH = "kubernetes/currency-service/service.yaml"
MANIFESTS_DIR = "kubernetes/currency-service/"

def test_ac1_deployment_exists_with_correct_spec():
    """AC-1: Deployment manifest exists with valid schema, minimum 1 replica configured"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "currency-service"
    assert deployment["metadata"]["labels"]["app.kubernetes.io/name"] == "currency-service"
    assert deployment["metadata"]["labels"]["app.kubernetes.io/part-of"] == "opentelemetry-demo"
    assert deployment["spec"]["replicas"] >= 1, "Deployment must have minimum 1 replica"
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "currency-service"

def test_ac2_probes_configured_correctly():
    """AC-2: livenessProbe and readinessProbe configured for /health on port 8081 with correct parameters"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "Missing livenessProbe"
    assert "readinessProbe" in container, "Missing readinessProbe"
    
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/health"
    assert liveness["httpGet"]["port"] == 8081
    assert liveness["initialDelaySeconds"] == 5
    assert liveness["periodSeconds"] == 10
    assert liveness["timeoutSeconds"] == 1
    assert liveness["failureThreshold"] == 3
    
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/health"
    assert readiness["httpGet"]["port"] == 8081
    assert readiness["initialDelaySeconds"] == 5
    assert readiness["periodSeconds"] == 10
    assert readiness["timeoutSeconds"] == 1
    assert readiness["failureThreshold"] == 3

def test_ac3_resource_limits_configured():
    """AC-3: Deployment defines compute resources with correct requests and limits"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container, "Missing resources definition"
    assert "requests" in container["resources"], "Missing resource requests"
    assert "limits" in container["resources"], "Missing resource limits"
    
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert container["resources"]["requests"]["memory"] == "128Mi"
    assert container["resources"]["limits"]["cpu"] == "500m"
    assert container["resources"]["limits"]["memory"] == "256Mi"

def test_ac4_security_context_configured():
    """AC-4: Pod security context configured with non-root user, read-only filesystem, no privilege escalation"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    pod_spec = deployment["spec"]["template"]["spec"]
    assert "securityContext" in pod_spec, "Missing pod securityContext"
    
    pod_sc = pod_spec["securityContext"]
    assert pod_sc["runAsNonRoot"] == True
    assert pod_sc["runAsUser"] == 1000
    assert pod_sc["readOnlyRootFilesystem"] == True
    assert pod_sc["allowPrivilegeEscalation"] == False
    assert "drop" in pod_sc["capabilities"]
    assert "ALL" in pod_sc["capabilities"]["drop"]

def test_ac5_service_exists_with_correct_spec():
    """AC-5: Service manifest exists with correct ClusterIP spec exposing ports 7000 and 8081"""
    assert os.path.exists(SERVICE_PATH), f"Service file missing at {SERVICE_PATH}"
    
    with open(SERVICE_PATH, "r") as f:
        service = yaml.safe_load(f)
    
    assert service["apiVersion"] == "v1"
    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "currency-service"
    assert service["spec"]["type"] == "ClusterIP"
    
    ports = service["spec"]["ports"]
    grpc_port = next(p for p in ports if p["name"] == "grpc")
    assert grpc_port["port"] == 7000
    assert grpc_port["targetPort"] == 7000
    
    health_port = next(p for p in ports if p["name"] == "health")
    assert health_port["port"] == 8081
    assert health_port["targetPort"] == 8081
    
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "currency-service"

def test_ac1_manifest_validation_succeeds():
    """AC-1: kubectl apply dry-run succeeds for Deployment manifest"""
    result = subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Deployment dry-run failed: {result.stderr}"

def test_ac5_service_validation_succeeds():
    """AC-5: kubectl apply dry-run succeeds for Service manifest"""
    result = subprocess.run(
        ["kubectl", "apply", "-f", SERVICE_PATH, "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Service dry-run failed: {result.stderr}"

@pytest.mark.e2e
def test_ac6_pod_readiness_and_health_check():
    """AC-6: Pod passes readiness checks within 30s, health endpoint returns 200 OK"""
    # Apply manifests
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR],
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"Failed to apply manifests: {apply_result.stderr}"
    
    # Wait for pod to be ready
    config.load_kube_config()
    v1 = client.CoreV1Api()
    namespace = "default"
    
    pod_ready = False
    pod_name = ""
    for _ in range(30):
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=currency-service")
            if pods.items:
                pod = pods.items[0]
                if pod.status.phase == "Running":
                    all_ready = all(c.ready for c in pod.status.container_statuses)
                    if all_ready:
                        pod_ready = True
                        pod_name = pod.metadata.name
                        break
        except ApiException as e:
            pass
        time.sleep(1)
    
    assert pod_ready, "Pod did not become ready within 30 seconds"
    
    # Test health endpoint via port-forward
    port_forward = subprocess.Popen(
        ["kubectl", "port-forward", pod_name, "8081:8081"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)  # Wait for port-forward to establish
    
    try:
        curl_result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8081/health"],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert curl_result.returncode == 0, "Failed to connect to health endpoint"
        assert curl_result.stdout.strip() == "200", f"Expected 200 OK, got {curl_result.stdout}"
    finally:
        port_forward.terminate()
        port_forward.wait()
        # Clean up manifests
        subprocess.run(["kubectl", "delete", "-f", MANIFESTS_DIR], capture_output=True)

@pytest.mark.e2e
def test_ac7_security_context_enforced():
    """AC-7: Pod runs as non-root user 1000, root filesystem is read-only"""
    # Apply manifests
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR],
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"Failed to apply manifests: {apply_result.stderr}"
    
    # Wait for pod to be running
    config.load_kube_config()
    v1 = client.CoreV1Api()
    namespace = "default"
    
    pod_running = False
    pod_name = ""
    for _ in range(30):
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=currency-service")
            if pods.items:
                pod = pods.items[0]
                if pod.status.phase == "Running":
                    pod_running = True
                    pod_name = pod.metadata.name
                    break
        except ApiException as e:
            pass
        time.sleep(1)
    
    assert pod_running, "Pod did not start within 30 seconds"
    
    # Test user ID
    id_result = subprocess.run(
        ["kubectl", "exec", pod_name, "--", "id", "-u"],
        capture_output=True,
        text=True
    )
    assert id_result.returncode == 0, "Failed to run id command"
    assert id_result.stdout.strip() == "1000", f"Expected user ID 1000, got {id_result.stdout.strip()}"
    
    # Test read-only filesystem
    touch_result = subprocess.run(
        ["kubectl", "exec", pod_name, "--", "touch", "/test-file"],
        capture_output=True,
        text=True
    )
    assert touch_result.returncode != 0, "touch command succeeded unexpectedly, filesystem is not read-only"
    assert "Permission denied" in touch_result.stderr, "Expected Permission denied error when writing to root filesystem"
    
    # Clean up manifests
    subprocess.run(["kubectl", "delete", "-f", MANIFESTS_DIR], capture_output=True)
