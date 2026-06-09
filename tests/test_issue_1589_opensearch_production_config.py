#!/usr/bin/env python3
import pytest
import requests
from kubernetes import client, config
import time
import os

# Load kubernetes config
config.load_kube_config()
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
OPENSEARCH_SERVICE_NAME = "opensearch"
OPENSEARCH_CONTAINER_NAME = "opensearch"
OPENSEARCH_PORT = 9200


def get_opensearch_deployment():
    deployments = apps_v1.list_namespaced_deployment(
        namespace=NAMESPACE,
        label_selector=f"app.kubernetes.io/name={OPENSEARCH_SERVICE_NAME}"
    )
    if len(deployments.items) == 0:
        # Check for statefulset if deployment not found
        sts = apps_v1.list_namespaced_stateful_set(
            namespace=NAMESPACE,
            label_selector=f"app.kubernetes.io/name={OPENSEARCH_SERVICE_NAME}"
        )
        assert len(sts.items) > 0, "No opensearch deployment or statefulset found"
        return sts.items[0]
    return deployments.items[0]


def get_opensearch_pod():
    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector=f"app.kubernetes.io/name={OPENSEARCH_SERVICE_NAME}"
    )
    assert len(pods.items) > 0, "No opensearch pods found"
    return pods.items[0]


def test_ac1_resources_configured():
    """AC-1: Opensearch container has CPU and memory requests/limits configured with non-zero values"""
    resource = get_opensearch_deployment()
    container = next(c for c in resource.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    assert container.resources is not None, "Resources field missing from container configuration"
    assert container.resources.requests is not None, "Resource requests not configured"
    assert container.resources.limits is not None, "Resource limits not configured"
    
    # Check requests are non-zero
    assert "cpu" in container.resources.requests, "CPU request not set"
    assert container.resources.requests["cpu"] not in ["0", "0m"], "CPU request is zero"
    assert "memory" in container.resources.requests, "Memory request not set"
    assert container.resources.requests["memory"] not in ["0", "0Mi", "0Gi"], "Memory request is zero"
    
    # Verify values match spec
    assert container.resources.requests["cpu"] == "1", f"Expected CPU request '1', got {container.resources.requests['cpu']}"
    assert container.resources.requests["memory"] == "2Gi", f"Expected memory request '2Gi', got {container.resources.requests['memory']}"
    assert container.resources.limits["cpu"] == "2", f"Expected CPU limit '2', got {container.resources.limits['cpu']}"
    assert container.resources.limits["memory"] == "4Gi", f"Expected memory limit '4Gi', got {container.resources.limits['memory']}"


def test_ac2_pod_security_context_non_root():
    """AC-2: Pod security context explicitly sets runAsNonRoot: true and uses UID/GID 1000"""
    resource = get_opensearch_deployment()
    pod_spec = resource.spec.template.spec
    
    assert pod_spec.security_context is not None, "Pod-level security context missing"
    assert pod_spec.security_context.run_as_non_root == True, "runAsNonRoot not set to true"
    assert pod_spec.security_context.run_as_user == 1000, f"Expected runAsUser 1000, got {pod_spec.security_context.run_as_user}"
    assert pod_spec.security_context.run_as_group == 1000, f"Expected runAsGroup 1000, got {pod_spec.security_context.run_as_group}"
    assert pod_spec.security_context.fs_group == 1000, f"Expected fsGroup 1000, got {pod_spec.security_context.fs_group}"
    
    # Verify actual running user
    pod = get_opensearch_pod()
    exec_command = ["/bin/sh", "-c", "id -u"]
    resp = v1.connect_get_namespaced_pod_exec(
        pod.metadata.name,
        NAMESPACE,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert resp.strip() == "1000", f"Running as UID {resp.strip()}, expected 1000"


def test_ac3_container_security_context_hardened():
    """AC-3: Container security context has readOnlyRootFilesystem, no privilege escalation, all capabilities dropped"""
    resource = get_opensearch_deployment()
    container = next(c for c in resource.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    assert container.security_context is not None, "Container security context missing"
    assert container.security_context.read_only_root_filesystem == True, "readOnlyRootFilesystem not set to true"
    assert container.security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation not set to false"
    assert container.security_context.capabilities is not None, "Capabilities configuration missing"
    assert "ALL" in container.security_context.capabilities.drop, "All capabilities not dropped"
    
    # Verify read only root filesystem is enforced
    pod = get_opensearch_pod()
    exec_command = ["/bin/sh", "-c", "touch /testfile 2>&1 || echo 'read-only'"]
    resp = v1.connect_get_namespaced_pod_exec(
        pod.metadata.name,
        NAMESPACE,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert "Read-only file system" in resp or "read-only" in resp, "Root filesystem is not read-only"


def test_ac4_liveness_probe_configured():
    """AC-4: Liveness probe configured to query port 9200 path / with correct timing parameters"""
    resource = get_opensearch_deployment()
    container = next(c for c in resource.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    assert container.liveness_probe is not None, "Liveness probe missing"
    assert container.liveness_probe.http_get is not None, "Liveness probe is not HTTP GET"
    assert container.liveness_probe.http_get.path == "/", f"Expected liveness probe path '/', got {container.liveness_probe.http_get.path}"
    assert container.liveness_probe.http_get.port == OPENSEARCH_PORT, f"Expected liveness probe port {OPENSEARCH_PORT}, got {container.liveness_probe.http_get.port}"
    assert container.liveness_probe.http_get.scheme == "HTTP", "Liveness probe scheme should be HTTP"
    assert container.liveness_probe.initial_delay_seconds == 30, f"Expected initial delay 30, got {container.liveness_probe.initial_delay_seconds}"
    assert container.liveness_probe.period_seconds == 10, f"Expected period 10, got {container.liveness_probe.period_seconds}"
    assert container.liveness_probe.failure_threshold == 3, f"Expected failure threshold 3, got {container.liveness_probe.failure_threshold}"
    
    # Verify liveness endpoint works
    pod = get_opensearch_pod()
    response = requests.get(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/", timeout=10)
    assert response.status_code == 200, f"Liveness endpoint returned {response.status_code}"


def test_ac5_readiness_probe_configured():
    """AC-5: Readiness probe configured to query port 9200 path /_cluster/health?local=true with correct parameters"""
    resource = get_opensearch_deployment()
    container = next(c for c in resource.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    assert container.readiness_probe is not None, "Readiness probe missing"
    assert container.readiness_probe.http_get is not None, "Readiness probe is not HTTP GET"
    assert container.readiness_probe.http_get.path == "/_cluster/health?local=true", f"Expected readiness probe path '/_cluster/health?local=true', got {container.readiness_probe.http_get.path}"
    assert container.readiness_probe.http_get.port == OPENSEARCH_PORT, f"Expected readiness probe port {OPENSEARCH_PORT}, got {container.readiness_probe.http_get.port}"
    assert container.readiness_probe.http_get.scheme == "HTTP", "Readiness probe scheme should be HTTP"
    assert container.readiness_probe.initial_delay_seconds == 10, f"Expected initial delay 10, got {container.readiness_probe.initial_delay_seconds}"
    assert container.readiness_probe.period_seconds == 5, f"Expected period 5, got {container.readiness_probe.period_seconds}"
    assert container.readiness_probe.success_threshold == 2, f"Expected success threshold 2, got {container.readiness_probe.success_threshold}"
    assert container.readiness_probe.failure_threshold == 3, f"Expected failure threshold 3, got {container.readiness_probe.failure_threshold}"
    
    # Verify readiness endpoint works
    pod = get_opensearch_pod()
    response = requests.get(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/_cluster/health?local=true", timeout=10)
    assert response.status_code == 200, f"Readiness endpoint returned {response.status_code}"


def test_ac6_pod_starts_healthy_non_root():
    """AC-6: Opensearch pod starts successfully, runs as non-root, probes healthy within 2 minutes"""
    # Wait up to 2 minutes for pod to be ready
    start_time = time.time()
    pod_ready = False
    pod = None
    
    while time.time() - start_time < 120:
        try:
            pod = get_opensearch_pod()
            if pod.status.phase == "Running":
                # Check if all containers are ready
                ready = all(status.ready for status in pod.status.container_statuses)
                if ready:
                    pod_ready = True
                    break
        except Exception:
            pass
        time.sleep(5)
    
    assert pod_ready, "Opensearch pod did not become ready within 2 minutes"
    
    # Verify running as non-root
    exec_command = ["/bin/sh", "-c", "id -u"]
    resp = v1.connect_get_namespaced_pod_exec(
        pod.metadata.name,
        NAMESPACE,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert resp.strip() == "1000", f"Running as UID {resp.strip()}, expected 1000 (non-root)"
    
    # Verify probes are passing
    assert pod.status.container_statuses[0].ready == True, "Container is not reporting ready status"
    assert pod.status.container_statuses[0].started == True, "Container is not reporting started status"


def test_ac7_opensearch_functionality_unchanged():
    """AC-7: Log ingestion and query operations work as expected after configuration changes"""
    pod = get_opensearch_pod()
    
    # Test document ingestion
    test_log = {
        "message": "Test log entry",
        "service.name": "test-service",
        "@timestamp": "2024-06-09T00:00:00Z",
        "trace_id": "abc123",
        "severity": "INFO"
    }
    
    # Write test log
    write_resp = requests.post(
        f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/logs-*/_doc",
        json=test_log,
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    assert write_resp.status_code in [200, 201], f"Failed to write test log: {write_resp.status_code}"
    
    # Refresh index to make document available
    requests.post(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/_refresh", timeout=10)
    
    # Query for test log
    query = {
        "query": {
            "match": {
                "trace_id": "abc123"
            }
        }
    }
    
    query_resp = requests.post(
        f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/logs-*/_search",
        json=query,
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    assert query_resp.status_code == 200, f"Failed to query logs: {query_resp.status_code}"
    
    hits = query_resp.json()["hits"]["hits"]
    assert len(hits) > 0, "Test log not found in query results"
    assert hits[0]["_source"]["message"] == "Test log entry", "Log message content mismatch"
    assert hits[0]["_source"]["service.name"] == "test-service", "Service name mismatch"
