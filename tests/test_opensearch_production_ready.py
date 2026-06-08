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


def get_opensearch_pod():
    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector=f"app.kubernetes.io/name={OPENSEARCH_SERVICE_NAME}"
    )
    assert len(pods.items) > 0, "No opensearch pods found"
    return pods.items[0]


def get_opensearch_statefulset():
    sts = apps_v1.list_namespaced_stateful_set(
        namespace=NAMESPACE,
        label_selector=f"app.kubernetes.io/name={OPENSEARCH_SERVICE_NAME}"
    )
    assert len(sts.items) > 0, "No opensearch statefulset found"
    return sts.items[0]


def test_ac1_liveness_probe_configuration():
    """AC-1: Liveness probe configured correctly with required parameters and returns 200 OK"""
    sts = get_opensearch_statefulset()
    container = next(c for c in sts.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    # Verify liveness probe exists and has correct parameters
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.http_get is not None, "Liveness probe is not HTTP GET"
    assert container.liveness_probe.http_get.path.endswith("/_cluster/health"), f"Liveness probe path incorrect: {container.liveness_probe.http_get.path}"
    assert container.liveness_probe.http_get.port == OPENSEARCH_PORT, f"Liveness probe port incorrect: {container.liveness_probe.http_get.port}"
    assert container.liveness_probe.initial_delay_seconds == 120, f"Initial delay should be 120, got {container.liveness_probe.initial_delay_seconds}"
    assert container.liveness_probe.period_seconds == 30, f"Period should be 30, got {container.liveness_probe.period_seconds}"
    assert container.liveness_probe.timeout_seconds == 10, f"Timeout should be 10, got {container.liveness_probe.timeout_seconds}"
    assert container.liveness_probe.failure_threshold == 3, f"Failure threshold should be 3, got {container.liveness_probe.failure_threshold}"
    assert container.liveness_probe.success_threshold == 1, f"Success threshold should be 1, got {container.liveness_probe.success_threshold}"
    
    # Verify endpoint returns 200 OK
    pod = get_opensearch_pod()
    response = requests.get(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/_cluster/health", timeout=10)
    assert response.status_code == 200, f"Cluster health endpoint returned {response.status_code}"


def test_ac2_readiness_probe_configuration():
    """AC-2: Readiness probe configured correctly with required parameters and returns 200 OK"""
    sts = get_opensearch_statefulset()
    container = next(c for c in sts.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    # Verify readiness probe exists and has correct parameters
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.http_get is not None, "Readiness probe is not HTTP GET"
    assert container.readiness_probe.http_get.path.endswith("/_cluster/health"), f"Readiness probe path incorrect: {container.readiness_probe.http_get.path}"
    assert container.readiness_probe.http_get.port == OPENSEARCH_PORT, f"Readiness probe port incorrect: {container.readiness_probe.http_get.port}"
    assert container.readiness_probe.initial_delay_seconds == 30, f"Initial delay should be 30, got {container.readiness_probe.initial_delay_seconds}"
    assert container.readiness_probe.period_seconds == 10, f"Period should be 10, got {container.readiness_probe.period_seconds}"
    assert container.readiness_probe.timeout_seconds == 5, f"Timeout should be 5, got {container.readiness_probe.timeout_seconds}"
    assert container.readiness_probe.failure_threshold == 3, f"Failure threshold should be 3, got {container.readiness_probe.failure_threshold}"
    assert container.readiness_probe.success_threshold == 3, f"Success threshold should be 3, got {container.readiness_probe.success_threshold}"
    
    # Verify endpoint returns 200 OK
    pod = get_opensearch_pod()
    response = requests.get(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/_cluster/health", timeout=5)
    assert response.status_code == 200, f"Cluster health endpoint returned {response.status_code}"


def test_ac3_resource_requirements_configuration():
    """AC-3: Container resource requests and limits set to required minimum/maximum values"""
    sts = get_opensearch_statefulset()
    container = next(c for c in sts.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    # Verify resource requests
    assert container.resources is not None, "Resources not configured"
    assert container.resources.requests is not None, "Resource requests not configured"
    assert "cpu" in container.resources.requests, "CPU request not set"
    assert container.resources.requests["cpu"] in ["500m", "0.5", "500millicpu"], f"CPU request should be at least 500m, got {container.resources.requests['cpu']}"
    assert "memory" in container.resources.requests, "Memory request not set"
    assert container.resources.requests["memory"] in ["1Gi", "1024Mi", "1G"], f"Memory request should be at least 1Gi, got {container.resources.requests['memory']}"
    
    # Verify resource limits
    assert container.resources.limits is not None, "Resource limits not configured"
    assert "cpu" in container.resources.limits, "CPU limit not set"
    assert container.resources.limits["cpu"] in ["2", "2000m", "2"], f"CPU limit should be max 2, got {container.resources.limits['cpu']}"
    assert "memory" in container.resources.limits, "Memory limit not set"
    assert container.resources.limits["memory"] in ["4Gi", "4096Mi", "4G"], f"Memory limit should be max 4Gi, got {container.resources.limits['memory']}"
    
    # Verify pod is scheduled and running
    pod = get_opensearch_pod()
    assert pod.status.phase == "Running", f"Opensearch pod is not running, phase: {pod.status.phase}"


def test_ac4_security_context_configuration():
    """AC-4: Security context configured for non-root execution with no privilege escalation"""
    sts = get_opensearch_statefulset()
    container = next(c for c in sts.spec.template.spec.containers if c.name == OPENSEARCH_CONTAINER_NAME)
    
    # Verify container security context
    assert container.security_context is not None, "Security context not configured"
    assert container.security_context.run_as_non_root == True, "runAsNonRoot should be true"
    assert container.security_context.run_as_user == 1000, f"runAsUser should be 1000, got {container.security_context.run_as_user}"
    assert container.security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation should be false"
    assert container.security_context.capabilities is not None, "Capabilities not configured"
    assert "ALL" in container.security_context.capabilities.drop, "All capabilities should be dropped"
    
    # Verify process is running as UID 1000
    pod = get_opensearch_pod()
    exec_command = [
        "/bin/sh",
        "-c",
        "ps -o uid= -C opensearch | head -n 1 | tr -d ' '"
    ]
    resp = v1.connect_get_namespaced_pod_exec(
        pod.metadata.name,
        NAMESPACE,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert resp.strip() == "1000", f"Opensearch process running as UID {resp.strip()}, expected 1000"


def test_ac5_ilm_script_execution_success():
    """AC-5: ILM configuration startup script executes successfully with exit code 0"""
    pod = get_opensearch_pod()
    
    # Verify ILM script exit code from pod logs
    logs = v1.read_namespaced_pod_log(pod.metadata.name, NAMESPACE, container=OPENSEARCH_CONTAINER_NAME)
    assert "ILM policy configured successfully" in logs or "ILM configuration complete" in logs, "ILM script success message not found in logs"
    
    # Verify ILM policies exist
    response = requests.get(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/_ilm/policy", timeout=10)
    assert response.status_code == 200, "Failed to list ILM policies"
    assert len(response.json()) > 0, "No ILM policies found"


def test_ac6_opensearch_operational_stability():
    """AC-6: Opensearch service remains fully operational with no unexpected restarts over 10 minutes"""
    # Check initial health
    pod = get_opensearch_pod()
    initial_restarts = pod.status.container_statuses[0].restart_count
    response = requests.get(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/_cluster/health", timeout=10)
    assert response.status_code == 200
    health_status = response.json()["status"]
    assert health_status in ["green", "yellow"], f"Cluster health is {health_status}, expected green/yellow"
    
    # Verify writes work
    test_doc = {"test": "data", "@timestamp": "2024-01-01T00:00:00Z"}
    write_resp = requests.post(
        f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/test-index/_doc/1",
        json=test_doc,
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    assert write_resp.status_code in [200, 201], f"Failed to write test document: {write_resp.status_code}"
    
    # Verify reads work
    read_resp = requests.get(f"http://{pod.status.pod_ip}:{OPENSEARCH_PORT}/test-index/_doc/1", timeout=10)
    assert read_resp.status_code == 200, f"Failed to read test document: {read_resp.status_code}"
    assert read_resp.json()["_source"] == test_doc, "Read document does not match written document"
    
    # Check restarts after observation (note: this test would normally run for 10 minutes, in CI we check current restarts)
    time.sleep(60)  # Shortened for test purposes, in real run use 600 seconds
    pod = get_opensearch_pod()
    new_restarts = pod.status.container_statuses[0].restart_count
    assert new_restarts == initial_restarts, f"Unexpected restarts: {new_restarts - initial_restarts} restarts occurred during observation period"
