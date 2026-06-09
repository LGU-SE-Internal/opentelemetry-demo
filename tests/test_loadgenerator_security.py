import os
import subprocess
import yaml
import pytest
import requests
from kubernetes import client, config

DOCKERFILE_PATH = "src/loadgenerator/Dockerfile"
K8S_DEPLOYMENT_PATH = "k8s/loadgenerator.yaml"
LOADGEN_IMAGE_NAME = "loadgenerator:test"
LOADGEN_SERVICE_PORT = 8089

@pytest.fixture(scope="module")
def loadgen_deployment_manifest():
    assert os.path.exists(K8S_DEPLOYMENT_PATH), f"Deployment file {K8S_DEPLOYMENT_PATH} missing"
    with open(K8S_DEPLOYMENT_PATH, "r") as f:
        docs = list(yaml.safe_load_all(f))
        for doc in docs:
            if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "loadgenerator":
                return doc
    pytest.fail("Loadgenerator Deployment not found in manifest")

@pytest.fixture(scope="module")
def loadgen_container_spec(loadgen_deployment_manifest):
    containers = loadgen_deployment_manifest["spec"]["template"]["spec"]["containers"]
    for c in containers:
        if c.get("name") == "loadgenerator":
            return c
    pytest.fail("Loadgenerator container not found in deployment")

def test_ac1_docker_runs_as_non_root():
    # Test AC-1: Docker runs as non-root user UID != 0
    assert os.path.exists(DOCKERFILE_PATH), f"Dockerfile {DOCKERFILE_PATH} missing"
    
    # Build test image first
    build_result = subprocess.run(
        ["docker", "build", "-t", LOADGEN_IMAGE_NAME, "-f", DOCKERFILE_PATH, "."],
        capture_output=True, text=True
    )
    assert build_result.returncode == 0, f"Docker build failed: {build_result.stderr}"
    
    # Run id command in container
    run_result = subprocess.run(
        ["docker", "run", "--rm", LOADGEN_IMAGE_NAME, "id", "-u"],
        capture_output=True, text=True
    )
    assert run_result.returncode == 0, f"Failed to run id command: {run_result.stderr}"
    
    uid = run_result.stdout.strip()
    assert uid != "0", f"Container runs as root user (UID {uid}), expected non-root"
    assert uid == "10001", f"Expected UID 10001, got {uid}"
    
    # Check no sudo privileges
    sudo_result = subprocess.run(
        ["docker", "run", "--rm", LOADGEN_IMAGE_NAME, "sudo", "-n", "id", "-u"],
        capture_output=True, text=True
    )
    assert sudo_result.returncode != 0, "Container user has sudo/root elevation privileges"

def test_ac2_liveness_probe_configured(loadgen_container_spec):
    # Test AC-2: Liveness probe configured correctly
    assert "livenessProbe" in loadgen_container_spec, "Liveness probe missing"
    liveness = loadgen_container_spec["livenessProbe"]
    
    assert "httpGet" in liveness, "Liveness probe is not HTTP GET"
    assert liveness["httpGet"]["path"] == "/health/live", f"Liveness probe path incorrect: {liveness['httpGet']['path']}"
    assert liveness["httpGet"]["port"] == LOADGEN_SERVICE_PORT, f"Liveness probe port incorrect: {liveness['httpGet']['port']}"
    assert liveness.get("failureThreshold", 0) >=3, f"Liveness failure threshold too low: {liveness.get('failureThreshold')}"
    assert liveness.get("periodSeconds", 0) >=10, f"Liveness periodSeconds too low: {liveness.get('periodSeconds')}"

def test_ac3_readiness_probe_configured(loadgen_container_spec):
    # Test AC-3: Readiness probe configured correctly
    assert "readinessProbe" in loadgen_container_spec, "Readiness probe missing"
    readiness = loadgen_container_spec["readinessProbe"]
    
    assert "httpGet" in readiness, "Readiness probe is not HTTP GET"
    assert readiness["httpGet"]["path"] == "/health/ready", f"Readiness probe path incorrect: {readiness['httpGet']['path']}"
    assert readiness["httpGet"]["port"] == LOADGEN_SERVICE_PORT, f"Readiness probe port incorrect: {readiness['httpGet']['port']}"
    assert readiness.get("failureThreshold", 0) >=3, f"Readiness failure threshold too low: {readiness.get('failureThreshold')}"
    assert readiness.get("periodSeconds", 0) >=10, f"Readiness periodSeconds too low: {readiness.get('periodSeconds')}"

def test_ac4_resource_requests_limits_configured(loadgen_container_spec):
    # Test AC-4: Resource requests and limits set correctly
    assert "resources" in loadgen_container_spec, "Resources section missing"
    resources = loadgen_container_spec["resources"]
    
    assert "requests" in resources, "Resource requests missing"
    assert resources["requests"]["cpu"] == "0.25" or resources["requests"]["cpu"] == "250m", f"CPU request incorrect: {resources['requests']['cpu']}"
    assert resources["requests"]["memory"] == "128Mi", f"Memory request incorrect: {resources['requests']['memory']}"
    
    assert "limits" in resources, "Resource limits missing"
    assert resources["limits"]["cpu"] == "0.75" or resources["limits"]["cpu"] == "750m", f"CPU limit incorrect: {resources['limits']['cpu']}"
    assert resources["limits"]["memory"] == "384Mi", f"Memory limit incorrect: {resources['limits']['memory']}"

def test_ac5_security_context_configured(loadgen_deployment_manifest, loadgen_container_spec):
    # Test AC-5: Security context hardening configured correctly
    pod_spec = loadgen_deployment_manifest["spec"]["template"]["spec"]
    
    # Check pod-level security context
    assert "securityContext" in pod_spec, "Pod-level security context missing"
    pod_sc = pod_spec["securityContext"]
    assert pod_sc.get("runAsNonRoot") == True, "Pod runAsNonRoot not set to true"
    assert pod_sc.get("runAsUser") == 10001, f"Pod runAsUser incorrect: {pod_sc.get('runAsUser')}"
    
    # Check container-level security context
    assert "securityContext" in loadgen_container_spec, "Container-level security context missing"
    container_sc = loadgen_container_spec["securityContext"]
    assert container_sc.get("runAsNonRoot") == True, "Container runAsNonRoot not set to true"
    assert container_sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation not set to false"
    assert container_sc.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem not set to true"
    assert container_sc.get("privileged") == False, "privileged not set to false"
    assert "capabilities" in container_sc, "Capabilities section missing"
    assert "drop" in container_sc["capabilities"], "Capabilities drop list missing"
    assert "ALL" in container_sc["capabilities"]["drop"], "Capabilities do not drop ALL"

@pytest.mark.integration
@pytest.mark.k8s
def test_ac6_deployment_functions_correctly():
    # Test AC-6: Deployment works correctly in Kubernetes
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    
    # Get deployment
    deploy = apps_v1.read_namespaced_deployment(name="loadgenerator", namespace="default")
    assert deploy.status.available_replicas == deploy.spec.replicas, "Not all replicas are available"
    
    # Get pod names
    pods = core_v1.list_namespaced_pod(namespace="default", label_selector="app.kubernetes.io/name=loadgenerator")
    assert len(pods.items) > 0, "No loadgenerator pods found"
    
    for pod in pods.items:
        # Check pod status is running
        assert pod.status.phase == "Running", f"Pod {pod.metadata.name} is not running"
        
        # Check no permission errors in logs
        logs = core_v1.read_namespaced_pod_log(name=pod.metadata.name, namespace="default", tail_lines=100)
        assert "Permission denied" not in logs, f"Permission errors found in pod {pod.metadata.name} logs"
        
        # Check liveness endpoint works
        pod_ip = pod.status.pod_ip
        try:
            resp = requests.get(f"http://{pod_ip}:{LOADGEN_SERVICE_PORT}/health/live", timeout=5)
            assert resp.status_code == 200, f"Liveness probe failed for pod {pod.metadata.name}: {resp.status_code}"
        except Exception as e:
            pytest.fail(f"Failed to connect to liveness endpoint on pod {pod.metadata.name}: {e}")
        
        # Check readiness endpoint works
        try:
            resp = requests.get(f"http://{pod_ip}:{LOADGEN_SERVICE_PORT}/health/ready", timeout=5)
            assert resp.status_code == 200, f"Readiness probe failed for pod {pod.metadata.name}: {resp.status_code}"
        except Exception as e:
            pytest.fail(f"Failed to connect to readiness endpoint on pod {pod.metadata.name}: {e}")
