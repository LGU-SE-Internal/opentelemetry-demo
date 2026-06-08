#!/usr/bin/env python3
import pytest
import subprocess
import os
import time
from kubernetes import client, config
from kubernetes.stream import stream

# Load kubernetes config
try:
    config.load_kube_config()
except:
    config.load_incluster_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

PROMETHEUS_DEPLOYMENT_NAME = "prometheus"
PROMETHEUS_LABEL_SELECTOR = "app.kubernetes.io/name=prometheus"
CONFIGMAP_NAME = "prometheus-config"
NAMESPACE = "monitoring"
DEPLOYMENT_FILE_PATH = "k8s/monitoring/prometheus/deployment.yaml"
EXPECTED_IMAGE = "quay.io/prometheus/prometheus:v2.47.0"
EXPECTED_PORT = 9090


def get_prometheus_deployment():
    """Helper to get prometheus deployment object"""
    return apps_v1.read_namespaced_deployment(name=PROMETHEUS_DEPLOYMENT_NAME, namespace=NAMESPACE)


def get_prometheus_pod():
    """Helper to get running prometheus pod"""
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=PROMETHEUS_LABEL_SELECTOR)
    for pod in pods.items:
        if pod.status.phase == "Running":
            return pod
    return None


def test_ac1_deployment_file_exists_and_validates():
    """AC-1: Deployment manifest exists at correct path and passes kubectl dry-run validation"""
    # Check file exists
    assert os.path.exists(DEPLOYMENT_FILE_PATH), f"Deployment file not found at {DEPLOYMENT_FILE_PATH}"
    
    # Run dry-run validation
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-f", DEPLOYMENT_FILE_PATH],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Deployment validation failed: {result.stderr}"


def test_ac2_liveness_probe_configured_correctly():
    """AC-2: Liveness probe configured correctly with correct endpoint and parameters"""
    deployment = get_prometheus_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.http_get is not None, "Liveness probe is not HTTP GET type"
    assert container.liveness_probe.http_get.path == "/-/healthy", f"Expected liveness path /-/healthy, got {container.liveness_probe.http_get.path}"
    assert container.liveness_probe.http_get.port == EXPECTED_PORT, f"Expected liveness port {EXPECTED_PORT}, got {container.liveness_probe.http_get.port}"
    assert container.liveness_probe.initial_delay_seconds == 30, f"Expected initialDelaySeconds=30, got {container.liveness_probe.initial_delay_seconds}"
    assert container.liveness_probe.period_seconds == 15, f"Expected periodSeconds=15, got {container.liveness_probe.period_seconds}"
    assert container.liveness_probe.timeout_seconds == 5, f"Expected timeoutSeconds=5, got {container.liveness_probe.timeout_seconds}"


def test_ac3_readiness_probe_configured_correctly():
    """AC-3: Readiness probe configured correctly with correct endpoint and parameters"""
    deployment = get_prometheus_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.http_get is not None, "Readiness probe is not HTTP GET type"
    assert container.readiness_probe.http_get.path == "/-/ready", f"Expected readiness path /-/ready, got {container.readiness_probe.http_get.path}"
    assert container.readiness_probe.http_get.port == EXPECTED_PORT, f"Expected readiness port {EXPECTED_PORT}, got {container.readiness_probe.http_get.port}"
    assert container.readiness_probe.initial_delay_seconds == 10, f"Expected initialDelaySeconds=10, got {container.readiness_probe.initial_delay_seconds}"
    assert container.readiness_probe.period_seconds == 5, f"Expected periodSeconds=5, got {container.readiness_probe.period_seconds}"
    assert container.readiness_probe.timeout_seconds == 3, f"Expected timeoutSeconds=3, got {container.readiness_probe.timeout_seconds}"


def test_ac4_resource_limits_and_requests_set():
    """AC-4: Resource requests and limits set to correct values"""
    deployment = get_prometheus_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    resources = container.resources
    assert resources is not None, "Resources not configured"
    
    # Check requests
    assert resources.requests is not None, "Resource requests not configured"
    assert "cpu" in resources.requests, "CPU request not set"
    assert resources.requests["cpu"] == "100m", f"Expected CPU request 100m, got {resources.requests['cpu']}"
    assert "memory" in resources.requests, "Memory request not set"
    assert resources.requests["memory"] == "256Mi", f"Expected Memory request 256Mi, got {resources.requests['memory']}"
    
    # Check limits
    assert resources.limits is not None, "Resource limits not configured"
    assert "cpu" in resources.limits, "CPU limit not set"
    assert resources.limits["cpu"] == "500m", f"Expected CPU limit 500m, got {resources.limits['cpu']}"
    assert "memory" in resources.limits, "Memory limit not set"
    assert resources.limits["memory"] == "1Gi", f"Expected Memory limit 1Gi, got {resources.limits['memory']}"


def test_ac5_security_context_configured():
    """AC-5: Security context meets all non-root and hardening requirements"""
    deployment = get_prometheus_deployment()
    template_spec = deployment.spec.template.spec
    container = template_spec.containers[0]
    
    # Check pod-level security context (or container level, either is acceptable)
    security_context = container.security_context or template_spec.security_context
    assert security_context is not None, "Security context not configured"
    
    assert security_context.run_as_non_root is True, "runAsNonRoot must be true"
    assert security_context.run_as_user == 65534, f"Expected runAsUser=65534, got {security_context.run_as_user}"
    assert security_context.read_only_root_filesystem is True, "readOnlyRootFilesystem must be true"
    assert security_context.allow_privilege_escalation is False, "allowPrivilegeEscalation must be false"
    assert security_context.privileged is False, "privileged must be false"


def test_ac6_configmap_mounted_correctly():
    """AC-6: Prometheus config ConfigMap is mounted at correct path with read-only permissions"""
    deployment = get_prometheus_deployment()
    volumes = deployment.spec.template.spec.volumes
    volume_mounts = deployment.spec.template.spec.containers[0].volume_mounts
    
    # Check configmap volume exists
    configmap_volume = None
    for vol in volumes:
        if vol.config_map and vol.config_map.name == CONFIGMAP_NAME:
            configmap_volume = vol
            break
    assert configmap_volume is not None, f"ConfigMap volume {CONFIGMAP_NAME} not found in deployment volumes"
    
    # Check volume mount exists at correct path
    config_mount = None
    for mount in volume_mounts:
        if mount.mount_path == "/etc/prometheus/prometheus.yml":
            config_mount = mount
            break
    assert config_mount is not None, "Config not mounted at /etc/prometheus/prometheus.yml"
    assert config_mount.read_only is True, "Config mount must be read-only"


def test_ac7_pod_runs_successfully_without_restarts():
    """AC-7: Prometheus pod reaches Running status within 2 minutes, 0 restarts after 5 minutes"""
    # Wait up to 2 minutes for pod to be running
    start_time = time.time()
    pod = None
    while time.time() - start_time < 120:
        pod = get_prometheus_pod()
        if pod:
            break
        time.sleep(5)
    
    assert pod is not None, "Prometheus pod not found after 2 minutes"
    assert pod.status.phase == "Running", f"Pod not in Running state: {pod.status.phase}"
    
    # Wait for 5 minutes total to check restarts
    while time.time() - start_time < 300:
        pod = get_prometheus_pod()
        restart_count = sum(c.restart_count for c in pod.status.container_statuses)
        assert restart_count == 0, f"Pod has {restart_count} restarts, expected 0"
        time.sleep(10)
