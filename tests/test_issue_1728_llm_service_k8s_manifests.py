#!/usr/bin/env python3
import os
import subprocess
import yaml
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "kubernetes/llm-service/deployment.yaml"
SERVICE_PATH = "kubernetes/llm-service/service.yaml"
MANIFESTS_DIR = "kubernetes/llm-service/"

def test_ac1_deployment_exists_with_resources():
    """AC-1: Deployment manifest exists with valid schema and resource requests/limits"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "llm-service"
    assert deployment["metadata"]["labels"]["app.kubernetes.io/name"] == "llm-service"
    assert deployment["metadata"]["labels"]["app.kubernetes.io/part-of"] == "opentelemetry-demo"
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "llm-service"
    assert "resources" in container
    assert "requests" in container["resources"]
    assert "cpu" in container["resources"]["requests"]
    assert "memory" in container["resources"]["requests"]
    assert "limits" in container["resources"]
    assert "cpu" in container["resources"]["limits"]
    assert "memory" in container["resources"]["limits"]
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert container["resources"]["requests"]["memory"] == "128Mi"
    assert container["resources"]["limits"]["cpu"] == "500m"
    assert container["resources"]["limits"]["memory"] == "256Mi"

def test_ac2_service_exists_with_correct_spec():
    """AC-2: Service manifest exists with valid schema exposing port 8080 as ClusterIP"""
    assert os.path.exists(SERVICE_PATH), f"Service file missing at {SERVICE_PATH}"
    
    with open(SERVICE_PATH, "r") as f:
        service = yaml.safe_load(f)
    
    assert service["apiVersion"] == "v1"
    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "llm-service"
    assert service["spec"]["type"] == "ClusterIP"
    
    ports = service["spec"]["ports"]
    http_port = next(p for p in ports if p["name"] == "http")
    assert http_port["port"] == 8080
    assert http_port["targetPort"] == 8080
    
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "llm-service"

def test_ac3_probes_configured_correctly():
    """AC-3: Deployment has liveness and readiness probes pointing to /healthz on 8080"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container
    assert "readinessProbe" in container
    
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/healthz"
    assert liveness["httpGet"]["port"] == 8080
    assert liveness["initialDelaySeconds"] > 0
    assert liveness["periodSeconds"] > 0
    assert liveness["initialDelaySeconds"] == 5
    assert liveness["periodSeconds"] == 10
    
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/healthz"
    assert readiness["httpGet"]["port"] == 8080
    assert readiness["initialDelaySeconds"] > 0
    assert readiness["periodSeconds"] > 0
    assert readiness["initialDelaySeconds"] == 2
    assert readiness["periodSeconds"] == 5

def test_ac4_security_context_configured():
    """AC-4: Security context has non-root user and no privilege escalation"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "securityContext" in container
    sc = container["securityContext"]
    assert sc["runAsNonRoot"] == True
    assert sc["runAsUser"] == 10001
    assert sc["allowPrivilegeEscalation"] == False
    assert "drop" in sc["capabilities"]
    assert "ALL" in sc["capabilities"]["drop"]

def test_ac5_environment_variables_present():
    """AC-5: All supported LLM service environment variables are present with defaults"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_vars = {e["name"]: e.get("value", "") for e in container["env"]}
    
    assert "LLM_RATE_LIMIT" in env_vars
    assert env_vars["LLM_RATE_LIMIT"] == "100"
    assert "LLM_SHUTDOWN_TIMEOUT" in env_vars
    assert env_vars["LLM_SHUTDOWN_TIMEOUT"] == "30s"
    assert "LLM_TLS_CERT_PATH" in env_vars
    assert env_vars["LLM_TLS_CERT_PATH"] == ""
    assert "LLM_TLS_KEY_PATH" in env_vars
    assert env_vars["LLM_TLS_KEY_PATH"] == ""

def test_ac6_kubectl_apply_succeeds():
    """AC-6: kubectl apply of manifests completes without validation errors"""
    result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR, "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl apply failed: {result.stderr}"

@pytest.mark.e2e
def test_ac7_pod_runs_and_responds():
    """AC-7: Pod transitions to Running state, passes probes, and responds to requests"""
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
    namespace = "default"  # Use default namespace for demo
    
    pod_running = False
    for _ in range(60):
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=llm-service")
            if pods.items:
                pod = pods.items[0]
                if pod.status.phase == "Running":
                    # Check if all containers are ready
                    all_ready = all(c.ready for c in pod.status.container_statuses)
                    if all_ready:
                        pod_running = True
                        pod_name = pod.metadata.name
                        break
        except ApiException as e:
            pass
        time.sleep(1)
    
    assert pod_running, "Pod did not reach Running state with ready containers within 60s"
    
    # Test connection to service
    service = v1.read_namespaced_service("llm-service", namespace)
    service_ip = service.spec.cluster_ip
    
    # Test HTTP request to service
    curl_result = subprocess.run(
        ["kubectl", "run", "-it", "--rm", "curl-test", "--image=curlimages/curl:latest", "--restart=Never", "--", f"curl -s -o /dev/null -w '%{{http_code}}' http://{service_ip}:8080/healthz"],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Clean up manifests
    subprocess.run(["kubectl", "delete", "-f", MANIFESTS_DIR], capture_output=True)
    
    assert curl_result.returncode == 0, f"Failed to connect to service: {curl_result.stderr}"
    assert curl_result.stdout.strip() == "200", f"Expected 200 response, got {curl_result.stdout}"
