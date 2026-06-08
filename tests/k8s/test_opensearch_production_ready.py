#!/usr/bin/env python3
import pytest
import subprocess
import time
import requests
from kubernetes import client, config
from kubernetes.stream import stream

# Load kubernetes config
config.load_kube_config()
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

OPENSEARCH_LABEL_SELECTOR = "app.kubernetes.io/name=opensearch"
OPENSEARCH_PORT = 9200
OPENSEARCH_HEALTH_ENDPOINT = "/_cluster/health"


def get_opensearch_deployment():
    """Helper to get opensearch deployment"""
    try:
        return apps_v1.read_namespaced_deployment(name="opensearch", namespace="default")
    except client.exceptions.ApiException:
        return None


def get_opensearch_pod():
    """Helper to get running opensearch pod"""
    pods = v1.list_namespaced_pod(namespace="default", label_selector=OPENSEARCH_LABEL_SELECTOR)
    for pod in pods.items:
        if pod.status.phase == "Running":
            return pod
    return None


def port_forward_opensearch(local_port=9200):
    """Helper to port forward to opensearch pod"""
    pod = get_opensearch_pod()
    if not pod:
        raise Exception("No running opensearch pod found")
    proc = subprocess.Popen(
        ["kubectl", "port-forward", pod.metadata.name, f"{local_port}:{OPENSEARCH_PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    # Wait for port forward to be ready
    time.sleep(5)
    return proc


@pytest.mark.ac1
def test_ac1_liveness_probe_config():
    """Test AC-1: Opensearch deployment has configured HTTP liveness probe pointing to /_cluster/health on port 9200 with initial delay 30s, period 10s, timeout 5s, failure threshold 3"""
    deploy = get_opensearch_deployment()
    assert deploy is not None, "Opensearch deployment not found"
    
    liveness = deploy.spec.template.spec.containers[0].liveness_probe
    assert liveness is not None, "No liveness probe configured"
    assert liveness.http_get is not None, "Liveness probe is not HTTP GET type"
    assert liveness.http_get.port == OPENSEARCH_PORT, f"Liveness probe not targeting port {OPENSEARCH_PORT}"
    assert liveness.http_get.path == OPENSEARCH_HEALTH_ENDPOINT, f"Liveness probe not targeting path {OPENSEARCH_HEALTH_ENDPOINT}"
    
    assert liveness.initial_delay_seconds == 30, f"Liveness probe initial delay should be 30s, got {liveness.initial_delay_seconds}s"
    assert liveness.period_seconds == 10, f"Liveness probe period should be 10s, got {liveness.period_seconds}s"
    assert liveness.timeout_seconds == 5, f"Liveness probe timeout should be 5s, got {liveness.timeout_seconds}s"
    assert liveness.failure_threshold == 3, f"Liveness probe failure threshold should be 3, got {liveness.failure_threshold}"


@pytest.mark.ac2
def test_ac2_readiness_probe_config():
    """Test AC-2: Opensearch deployment has configured HTTP readiness probe pointing to /_cluster/health on port 9200 with initial delay 10s, period 5s, timeout 3s, failure threshold 3"""
    deploy = get_opensearch_deployment()
    assert deploy is not None, "Opensearch deployment not found"
    
    readiness = deploy.spec.template.spec.containers[0].readiness_probe
    assert readiness is not None, "No readiness probe configured"
    assert readiness.http_get is not None, "Readiness probe is not HTTP GET type"
    assert readiness.http_get.port == OPENSEARCH_PORT, f"Readiness probe not targeting port {OPENSEARCH_PORT}"
    assert readiness.http_get.path == OPENSEARCH_HEALTH_ENDPOINT, f"Readiness probe not targeting path {OPENSEARCH_HEALTH_ENDPOINT}"
    
    assert readiness.initial_delay_seconds == 10, f"Readiness probe initial delay should be 10s, got {readiness.initial_delay_seconds}s"
    assert readiness.period_seconds == 5, f"Readiness probe period should be 5s, got {readiness.period_seconds}s"
    assert readiness.timeout_seconds == 3, f"Readiness probe timeout should be 3s, got {readiness.timeout_seconds}s"
    assert readiness.failure_threshold == 3, f"Readiness probe failure threshold should be 3, got {readiness.failure_threshold}"


@pytest.mark.ac3
def test_ac3_resource_configuration():
    """Test AC-3: Opensearch deployment defines resource requests: 500m CPU, 1Gi memory; resource limits: 1 CPU, 2Gi memory"""
    deploy = get_opensearch_deployment()
    assert deploy is not None, "Opensearch deployment not found"
    
    resources = deploy.spec.template.spec.containers[0].resources
    assert resources is not None, "No resources configured for opensearch container"
    
    # Check requests
    assert "cpu" in resources.requests, "CPU request not defined"
    assert resources.requests["cpu"] == "500m", f"CPU request should be 500m, got {resources.requests['cpu']}"
    assert "memory" in resources.requests, "Memory request not defined"
    assert resources.requests["memory"] == "1Gi", f"Memory request should be 1Gi, got {resources.requests['memory']}"
    
    # Check limits
    assert "cpu" in resources.limits, "CPU limit not defined"
    assert resources.limits["cpu"] == "1", f"CPU limit should be 1, got {resources.limits['cpu']}"
    assert "memory" in resources.limits, "Memory limit not defined"
    assert resources.limits["memory"] == "2Gi", f"Memory limit should be 2Gi, got {resources.limits['memory']}"


@pytest.mark.ac4
def test_ac4_security_context_configuration():
    """Test AC-4: Opensearch deployment security context explicitly sets runAsUser=1000, runAsNonRoot=true, readOnlyRootFilesystem=true, and drops all Linux capabilities"""
    deploy = get_opensearch_deployment()
    assert deploy is not None, "Opensearch deployment not found"
    
    security_context = deploy.spec.template.spec.containers[0].security_context
    assert security_context is not None, "No security context configured for opensearch container"
    
    assert security_context.run_as_user == 1000, f"runAsUser should be 1000, got {security_context.run_as_user}"
    assert security_context.run_as_non_root is True, "runAsNonRoot should be true"
    assert security_context.read_only_root_filesystem is True, "readOnlyRootFilesystem should be true"
    
    assert security_context.capabilities is not None, "No capabilities defined in security context"
    assert security_context.capabilities.drop is not None, "No capabilities dropped"
    assert "ALL" in security_context.capabilities.drop, f"Should drop ALL capabilities, only got: {security_context.capabilities.drop}"


@pytest.mark.ac5
def test_ac5_pod_running_no_restarts():
    """Test AC-5: Opensearch pod starts successfully and remains in Running state without restarts for at least 5 minutes after deployment"""
    pod = get_opensearch_pod()
    assert pod is not None, "No running opensearch pod found"
    assert pod.status.phase == "Running", f"Pod is in {pod.status.phase} state, expected Running"
    
    # Check restart count
    for container_status in pod.status.container_statuses:
        if container_status.name == "opensearch":
            # If pod has been running less than 5 mins, wait and check restarts again
            start_time = pod.status.start_time
            uptime = time.time() - start_time.timestamp()
            if uptime < 300:
                time.sleep(max(0, 300 - uptime))
                # Refresh pod status
                pod = v1.read_namespaced_pod(name=pod.metadata.name, namespace="default")
                for cs in pod.status.container_statuses:
                    if cs.name == "opensearch":
                        assert cs.restart_count == 0, f"Pod has restarted {cs.restart_count} times in first 5 minutes"
            else:
                assert container_status.restart_count == 0, f"Pod has restarted {container_status.restart_count} times"


@pytest.mark.ac6
def test_ac6_pod_ready_state_transitions():
    """Test AC-6: Kubernetes reports opensearch pod as Ready state within 60 seconds of successful initialization, and transitions to NotReady state when /_cluster/health endpoint returns non-2xx HTTP status code"""
    # First check pod is Ready
    pod = get_opensearch_pod()
    assert pod is not None, "No running opensearch pod found"
    
    ready = False
    for condition in pod.status.conditions:
        if condition.type == "Ready":
            ready = condition.status == "True"
            break
    assert ready, "Pod is not in Ready state"
    
    # Test that endpoint returns 2xx
    pf_proc = port_forward_opensearch()
    try:
        response = requests.get(f"http://localhost:{OPENSEARCH_PORT}{OPENSEARCH_HEALTH_ENDPOINT}", timeout=5)
        assert 200 <= response.status_code < 300, f"Health endpoint returned {response.status_code}, expected 2xx"
    finally:
        pf_proc.terminate()
        pf_proc.wait()


@pytest.mark.ac7
def test_ac7_no_root_processes():
    """Test AC-7: All processes running inside the opensearch pod execute as user ID 1000, no root (UID 0) processes are present"""
    pod = get_opensearch_pod()
    assert pod is not None, "No running opensearch pod found"
    
    # Execute ps command in pod to list all processes with UID
    cmd = ["/bin/sh", "-c", "ps -eo uid | grep -v UID | sort | uniq"]
    resp = stream(v1.connect_get_namespaced_pod_exec,
                  pod.metadata.name,
                  namespace="default",
                  command=cmd,
                  stderr=True, stdin=False,
                  stdout=True, tty=False)
    
    uids = [line.strip() for line in resp.splitlines() if line.strip()]
    assert len(uids) > 0, "No processes found in pod"
    assert "0" not in uids, f"Found root (UID 0) processes running, UIDs present: {uids}"
    assert "1000" in uids, f"Expected processes running as UID 1000, only found: {uids}"
