#!/usr/bin/env python3
"""Integration tests for frontend-proxy Kubernetes manifests (AC-1 to AC-8)"""

import os
import subprocess
import time
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import pytest

# Constants from spec
DEPLOYMENT_PATH = "kubernetes/frontend-proxy/deployment.yaml"
SERVICE_PATH = "kubernetes/frontend-proxy/service.yaml"
APP_LABEL = "app.kubernetes.io/name: frontend-proxy"
DEFAULT_REPLICAS = 2
DEFAULT_RUN_AS_USER = 1000
DEFAULT_RUN_AS_GROUP = 1000
ENVOY_ADMIN_PORT = 9901
ENVOY_FRONTEND_PORT = 8080
SERVICE_FRONTEND_PORT = 80

@pytest.fixture(scope="module")
def k8s_client():
    """Load kubernetes config and return API clients"""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    return apps_v1, core_v1

@pytest.fixture(scope="module")
def apply_manifests():
    """Apply the frontend-proxy manifests to the test cluster"""
    # Check if manifest files exist first
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment manifest missing at {DEPLOYMENT_PATH}"
    assert os.path.exists(SERVICE_PATH), f"Service manifest missing at {SERVICE_PATH}"
    
    # Apply manifests
    subprocess.run(["kubectl", "apply", "-f", DEPLOYMENT_PATH], check=True, capture_output=True, text=True)
    subprocess.run(["kubectl", "apply", "-f", SERVICE_PATH], check=True, capture_output=True, text=True)
    
    yield
    
    # Cleanup after tests
    subprocess.run(["kubectl", "delete", "-f", DEPLOYMENT_PATH, "--ignore-not-found=true"], capture_output=True)
    subprocess.run(["kubectl", "delete", "-f", SERVICE_PATH, "--ignore-not-found=true"], capture_output=True)

def test_ac1_deployment_pods_start_successfully(k8s_client, apply_manifests):
    """AC-1: Frontend-proxy pods start and reach Running state within 60 seconds"""
    apps_v1, core_v1 = k8s_client
    namespace = "default"
    
    # Wait for deployment to become available
    start_time = time.time()
    while time.time() - start_time < 60:
        try:
            deploy = apps_v1.read_namespaced_deployment("frontend-proxy", namespace)
            if deploy.status.available_replicas == DEFAULT_REPLICAS:
                break
        except ApiException:
            pass
        time.sleep(5)
    
    # Verify all replicas are available
    deploy = apps_v1.read_namespaced_deployment("frontend-proxy", namespace)
    assert deploy.status.available_replicas == DEFAULT_REPLICAS, f"Only {deploy.status.available_replicas} of {DEFAULT_REPLICAS} replicas available after 60s"
    
    # Verify all pods are in Running state
    pods = core_v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=frontend-proxy")
    assert len(pods.items) == DEFAULT_REPLICAS, f"Expected {DEFAULT_REPLICAS} pods, found {len(pods.items)}"
    for pod in pods.items:
        assert pod.status.phase == "Running", f"Pod {pod.metadata.name} is in {pod.status.phase} state"

def test_ac2_liveness_probe_returns_200(k8s_client, apply_manifests):
    """AC-2: Liveness probe returns HTTP 200 on /healthz endpoint port 9901"""
    _, core_v1 = k8s_client
    namespace = "default"
    
    # Get running pods
    pods = core_v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=frontend-proxy")
    assert len(pods.items) > 0, "No frontend-proxy pods found"
    
    for pod in pods.items:
        # Forward port to pod admin endpoint
        proc = subprocess.Popen(
            ["kubectl", "port-forward", pod.metadata.name, f"{ENVOY_ADMIN_PORT}:{ENVOY_ADMIN_PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2)  # Wait for port forward to establish
        
        try:
            response = requests.get(f"http://localhost:{ENVOY_ADMIN_PORT}/healthz", timeout=5)
            assert response.status_code == 200, f"Liveness probe failed for pod {pod.metadata.name}: status {response.status_code}"
        finally:
            proc.terminate()
            proc.wait()

def test_ac3_readiness_probe_returns_200_and_pods_ready(k8s_client, apply_manifests):
    """AC-3: Readiness probe returns 200 and pods are marked Ready"""
    _, core_v1 = k8s_client
    namespace = "default"
    
    # Get running pods
    pods = core_v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=frontend-proxy")
    assert len(pods.items) > 0, "No frontend-proxy pods found"
    
    for pod in pods.items:
        # Check pod readiness condition
        ready = next((c for c in pod.status.conditions if c.type == "Ready"), None)
        assert ready is not None, f"No Ready condition found for pod {pod.metadata.name}"
        assert ready.status == "True", f"Pod {pod.metadata.name} is not marked Ready"
        
        # Forward port to pod admin endpoint
        proc = subprocess.Popen(
            ["kubectl", "port-forward", pod.metadata.name, f"{ENVOY_ADMIN_PORT}:{ENVOY_ADMIN_PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2)
        
        try:
            response = requests.get(f"http://localhost:{ENVOY_ADMIN_PORT}/healthz", timeout=5)
            assert response.status_code == 200, f"Readiness probe failed for pod {pod.metadata.name}: status {response.status_code}"
        finally:
            proc.terminate()
            proc.wait()

def test_ac4_security_context_non_root_no_privilege(k8s_client, apply_manifests):
    """AC-4: Containers run as non-root user, no privileged access, read-only filesystem"""
    apps_v1, _ = k8s_client
    namespace = "default"
    
    deploy = apps_v1.read_namespaced_deployment("frontend-proxy", namespace)
    pod_spec = deploy.spec.template.spec
    
    # Pod level security context checks
    assert pod_spec.security_context.run_as_non_root == True, "Pod security context missing runAsNonRoot: true"
    assert pod_spec.security_context.run_as_user == DEFAULT_RUN_AS_USER, f"Pod should run as UID {DEFAULT_RUN_AS_USER}"
    assert pod_spec.security_context.run_as_group == DEFAULT_RUN_AS_GROUP, f"Pod should run as GID {DEFAULT_RUN_AS_GROUP}"
    assert pod_spec.security_context.fs_group == DEFAULT_RUN_AS_GROUP, f"Pod fsGroup should be {DEFAULT_RUN_AS_GROUP}"
    
    # Container level security context checks
    container = next(c for c in pod_spec.containers if c.name == "frontend-proxy")
    assert container.security_context.privileged == False, "Container should not have privileged: true"
    assert container.security_context.allow_privilege_escalation == False, "Container should disable privilege escalation"
    assert container.security_context.read_only_root_filesystem == True, "Container should have read-only root filesystem"
    assert "ALL" in container.security_context.capabilities.drop, "Container should drop all capabilities"

def test_ac5_resource_requests_limits_set(k8s_client, apply_manifests):
    """AC-5: Resource requests and limits are explicitly set on container"""
    apps_v1, _ = k8s_client
    namespace = "default"
    
    deploy = apps_v1.read_namespaced_deployment("frontend-proxy", namespace)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == "frontend-proxy")
    
    # Check requests are present
    assert hasattr(container.resources, "requests"), "Resource requests not set"
    assert "cpu" in container.resources.requests, "CPU request not set"
    assert "memory" in container.resources.requests, "Memory request not set"
    
    # Check limits are present
    assert hasattr(container.resources, "limits"), "Resource limits not set"
    assert "cpu" in container.resources.limits, "CPU limit not set"
    assert "memory" in container.resources.limits, "Memory limit not set"

def test_ac6_service_exposes_port_80_and_configurable_type(k8s_client, apply_manifests):
    """AC-6: Service exposes port 80 internally by default, supports LoadBalancer/NodePort override"""
    _, core_v1 = k8s_client
    namespace = "default"
    
    svc = core_v1.read_namespaced_service("frontend-proxy", namespace)
    
    # Default type should be ClusterIP
    assert svc.spec.type == "ClusterIP", f"Default service type should be ClusterIP, got {svc.spec.type}"
    
    # Check port 80 is exposed for frontend traffic
    frontend_port = next(p for p in svc.spec.ports if p.port == SERVICE_FRONTEND_PORT)
    assert frontend_port.target_port == ENVOY_FRONTEND_PORT, f"Port {SERVICE_FRONTEND_PORT} should target container port {ENVOY_FRONTEND_PORT}"
    
    # Check admin port is exposed internally
    admin_port = next(p for p in svc.spec.ports if p.port == ENVOY_ADMIN_PORT)
    assert admin_port.target_port == ENVOY_ADMIN_PORT, f"Port {ENVOY_ADMIN_PORT} should target container port {ENVOY_ADMIN_PORT}"
    
    # Verify selector matches deployment labels
    assert svc.spec.selector["app.kubernetes.io/name"] == "frontend-proxy", "Service selector does not match deployment labels"

def test_ac7_env_vars_configure_envoy_parameters(k8s_client, apply_manifests):
    """AC-7: All Envoy template parameters are configurable via ENVOY_* environment variables"""
    apps_v1, _ = k8s_client
    namespace = "default"
    
    deploy = apps_v1.read_namespaced_deployment("frontend-proxy", namespace)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == "frontend-proxy")
    
    # Check that env vars with ENVOY_ prefix exist
    envoy_env_vars = [env for env in container.env if env.name.startswith("ENVOY_")]
    assert len(envoy_env_vars) > 0, "No ENVOY_* environment variables defined in deployment"
    
    # Test that changing an env var updates the config (modify deployment and check restart)
    original_env = container.env.copy()
    test_env_var = "ENVOY_TEST_PARAM"
    test_value = "test-value-123"
    
    # Add test env var to deployment
    container.env.append(client.V1EnvVar(name=test_env_var, value=test_value))
    deploy.spec.template.spec.containers[0] = container
    apps_v1.replace_namespaced_deployment("frontend-proxy", namespace, deploy)
    
    # Wait for pods to restart
    time.sleep(20)
    
    # Verify new pods have the env var set
    _, core_v1 = k8s_client
    pods = core_v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=frontend-proxy")
    for pod in pods.items:
        for c in pod.spec.containers:
            if c.name == "frontend-proxy":
                test_env = next(e for e in c.env if e.name == test_env_var)
                assert test_env.value == test_value, f"Env var {test_env_var} not set correctly in new pod"

def test_ac8_service_routes_traffic_to_frontend(k8s_client, apply_manifests):
    """AC-8: Frontend-proxy routes traffic correctly to downstream frontend service"""
    _, core_v1 = k8s_client
    namespace = "default"
    
    # Check if frontend service exists (assumed present in test cluster)
    try:
        core_v1.read_namespaced_service("frontend", namespace)
    except ApiException as e:
        pytest.skip("Frontend service not present in test cluster, skipping AC-8 test")
    
    # Forward service port
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "service/frontend-proxy", "8080:80"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(2)
    
    try:
        # Send test request to frontend proxy
        response = requests.get("http://localhost:8080", timeout=10)
        assert response.status_code in [200, 302], f"Expected 200/302 response from proxy, got {response.status_code}"
    finally:
        proc.terminate()
        proc.wait()
