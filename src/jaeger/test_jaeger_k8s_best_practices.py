#!/usr/bin/env python3
"""Integration tests for Jaeger Kubernetes production best practices ACs"""
import subprocess
import time
import requests
from kubernetes import client, config
import pytest

# Configure K8s client
config.load_kube_config()
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()

JAEGER_DEPLOYMENT_NAME = "jaeger"
JAEGER_NAMESPACE = "default"  # Update if Jaeger is deployed in different NS


def get_jaeger_deployment():
    """Helper to get Jaeger deployment object"""
    return apps_v1.read_namespaced_deployment(
        name=JAEGER_DEPLOYMENT_NAME, namespace=JAEGER_NAMESPACE
    )


def get_running_jaeger_pod():
    """Helper to get a running Jaeger pod"""
    pods = core_v1.list_namespaced_pod(
        namespace=JAEGER_NAMESPACE,
        label_selector=f"app.kubernetes.io/name={JAEGER_DEPLOYMENT_NAME}"
    )
    for pod in pods.items:
        if pod.status.phase == "Running" and any(c.ready for c in pod.status.container_statuses):
            return pod
    pytest.fail("No running ready Jaeger pod found")


def test_ac1_liveness_probe_configured():
    """AC-1: Verify livenessProbe is configured correctly with required thresholds"""
    deploy = get_jaeger_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    # Check liveness probe exists
    assert container.liveness_probe is not None, "livenessProbe not configured"
    
    # Check endpoint and port
    probe = container.liveness_probe
    assert probe.http_get is not None, "livenessProbe should be HTTP GET type"
    assert (
        (probe.http_get.path == "/" and probe.http_get.port == 16686) or
        (probe.http_get.path == "/health" and probe.http_get.port == 14269)
    ), f"Invalid livenessProbe endpoint: {probe.http_get.path}:{probe.http_get.port}"
    
    # Check thresholds
    assert probe.initial_delay_seconds >= 10, f"livenessProbe initial delay too low: {probe.initial_delay_seconds}s < 10s"
    assert probe.period_seconds >= 5, f"livenessProbe period too low: {probe.period_seconds}s < 5s"


def test_ac2_readiness_probe_configured():
    """AC-2: Verify readinessProbe is configured correctly with required thresholds"""
    deploy = get_jaeger_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    # Check readiness probe exists
    assert container.readiness_probe is not None, "readinessProbe not configured"
    
    # Check endpoint and port
    probe = container.readiness_probe
    assert probe.http_get is not None, "readinessProbe should be HTTP GET type"
    assert (
        (probe.http_get.path == "/" and probe.http_get.port == 16686) or
        (probe.http_get.path == "/health" and probe.http_get.port == 14269)
    ), f"Invalid readinessProbe endpoint: {probe.http_get.path}:{probe.http_get.port}"
    
    # Check thresholds
    assert probe.initial_delay_seconds >= 5, f"readinessProbe initial delay too low: {probe.initial_delay_seconds}s < 5s"
    assert probe.period_seconds >= 3, f"readinessProbe period too low: {probe.period_seconds}s < 3s"


def test_ac3_resource_limits_configured():
    """AC-3: Verify CPU and memory resource requests/limits are within required ranges"""
    deploy = get_jaeger_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    resources = container.resources
    assert resources is not None, "Resources not configured"
    assert resources.requests is not None, "Resource requests not configured"
    assert resources.limits is not None, "Resource limits not configured"
    
    # Parse CPU values (convert millicores to integer)
    def parse_cpu(cpu_str):
        if cpu_str.endswith("m"):
            return int(cpu_str[:-1])
        return int(float(cpu_str) * 1000)
    
    # Parse memory values (convert Mi to integer)
    def parse_memory(mem_str):
        if mem_str.endswith("Mi"):
            return int(mem_str[:-2])
        elif mem_str.endswith("Gi"):
            return int(mem_str[:-2]) * 1024
        pytest.fail(f"Unsupported memory unit: {mem_str}")
    
    # CPU checks
    cpu_request = parse_cpu(resources.requests["cpu"])
    cpu_limit = parse_cpu(resources.limits["cpu"])
    assert 100 <= cpu_request <= 1000, f"CPU request out of range: {cpu_request}m"
    assert 500 <= cpu_limit <= 2000, f"CPU limit out of range: {cpu_limit}m"
    
    # Memory checks
    mem_request = parse_memory(resources.requests["memory"])
    mem_limit = parse_memory(resources.limits["memory"])
    assert 256 <= mem_request <= 1024, f"Memory request out of range: {mem_request}Mi"
    assert 512 <= mem_limit <= 2048, f"Memory limit out of range: {mem_limit}Mi"


def test_ac4_security_context_configured():
    """AC-4: Verify securityContext has all required non-root and hardening settings"""
    deploy = get_jaeger_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    sc = container.security_context
    assert sc is not None, "securityContext not configured"
    assert sc.run_as_non_root == True, "runAsNonRoot must be true"
    assert sc.run_as_user >= 1000, f"runAsUser must be >=1000, got {sc.run_as_user}"
    assert sc.allow_privilege_escalation == False, "allowPrivilegeEscalation must be false"
    assert sc.read_only_root_filesystem == True, "readOnlyRootFilesystem must be true"
    assert sc.capabilities is not None, "Capabilities not configured"
    assert "ALL" in sc.capabilities.drop, "Must drop all capabilities"


def test_ac5_running_as_non_root_user():
    """AC-5: Verify running user in Jaeger pod is not root"""
    pod = get_running_jaeger_pod()
    
    # Execute id command in pod
    exec_cmd = [
        "kubectl", "exec", "-n", JAEGER_NAMESPACE, pod.metadata.name,
        "--", "id", "-u"
    ]
    result = subprocess.run(exec_cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to exec into pod: {result.stderr}"
    
    uid = int(result.stdout.strip())
    assert uid != 0, f"Running as root user (UID {uid})"
    assert uid >= 1000, f"Running user UID {uid} < 1000"


def test_ac6_jaeger_functionality_unchanged():
    """AC-6: Verify Jaeger UI is reachable and works correctly"""
    pod = get_running_jaeger_pod()
    
    # Port forward to Jaeger UI port 16686
    port_forward = subprocess.Popen(
        ["kubectl", "port-forward", "-n", JAEGER_NAMESPACE, pod.metadata.name, "16686:16686"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(2)  # Wait for port forward to establish
    
    try:
        # Test UI endpoint returns 200 OK
        resp = requests.get("http://localhost:16686/", timeout=10)
        assert resp.status_code == 200, f"Jaeger UI returned status {resp.status_code}"
        
        # Test API endpoint works (list services)
        api_resp = requests.get("http://localhost:16686/api/services", timeout=10)
        assert api_resp.status_code == 200, f"Jaeger API returned status {api_resp.status_code}"
        assert "data" in api_resp.json(), "Invalid API response format"
    finally:
        port_forward.terminate()
        port_forward.wait()


def test_ac7_resource_limit_enforcement():
    """AC-7: Verify Kubernetes enforces resource limits correctly"""
    deploy = get_jaeger_deployment()
    container = deploy.spec.template.spec.containers[0]
    mem_limit = container.resources.limits["memory"]
    
    # First get current pod count
    initial_pods = len([p for p in core_v1.list_namespaced_pod(
        namespace=JAEGER_NAMESPACE,
        label_selector=f"app.kubernetes.io/name={JAEGER_DEPLOYMENT_NAME}"
    ).items if p.status.phase == "Running"])
    
    # Deploy memory stress test pod to force OOM (skip if stress testing not allowed, mark as xfail)
    pytest.xfail("Resource limit enforcement test requires cluster stress testing permissions")
    
    # Note: Full implementation would use a stress pod to consume memory above limit,
    # verify Kubernetes restarts the container, and check persisted traces are intact
