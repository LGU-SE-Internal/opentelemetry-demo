import pytest
from kubernetes import client, config
import subprocess
import time

@pytest.fixture(scope="module")
def k8s_client():
    config.load_kube_config()
    return client.AppsV1Api()

@pytest.fixture(scope="module")
def core_v1_client():
    config.load_kube_config()
    return client.CoreV1Api()

@pytest.fixture(scope="module")
def image_provider_deployment(k8s_client):
    deployment = k8s_client.read_namespaced_deployment(name="image-provider", namespace="default")
    return deployment

@pytest.fixture(scope="module")
def running_image_provider_pod(core_v1_client):
    pods = core_v1_client.list_namespaced_pod(namespace="default", label_selector="app.kubernetes.io/name=image-provider")
    assert len(pods.items) > 0, "No running image-provider pods found"
    pod = pods.items[0]
    assert pod.status.phase == "Running", "Image-provider pod is not running"
    return pod

def test_ac1_liveness_probe_configured(image_provider_deployment):
    """AC-1: Verify livenessProbe is configured for HTTP GET /health on port 8080 with correct parameters"""
    container = image_provider_deployment.spec.template.spec.containers[0]
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.http_get is not None, "Liveness probe is not HTTP GET"
    assert container.liveness_probe.http_get.path == "/health", f"Liveness probe path is {container.liveness_probe.http_get.path}, expected /health"
    assert container.liveness_probe.http_get.port == 8080, f"Liveness probe port is {container.liveness_probe.http_get.port}, expected 8080"
    assert container.liveness_probe.period_seconds == 10, f"Liveness probe period is {container.liveness_probe.period_seconds}, expected 10"
    assert container.liveness_probe.failure_threshold == 3, f"Liveness probe failure threshold is {container.liveness_probe.failure_threshold}, expected 3"

def test_ac2_readiness_probe_configured(image_provider_deployment):
    """AC-2: Verify readinessProbe is configured with correct initial delay and parameters"""
    container = image_provider_deployment.spec.template.spec.containers[0]
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.http_get is not None, "Readiness probe is not HTTP GET"
    assert container.readiness_probe.http_get.path == "/health", f"Readiness probe path is {container.readiness_probe.http_get.path}, expected /health"
    assert container.readiness_probe.http_get.port == 8080, f"Readiness probe port is {container.readiness_probe.http_get.port}, expected 8080"
    assert container.readiness_probe.initial_delay_seconds == 2, f"Readiness probe initial delay is {container.readiness_probe.initial_delay_seconds}, expected 2"
    assert container.readiness_probe.period_seconds == 10, f"Readiness probe period is {container.readiness_probe.period_seconds}, expected 10"
    assert container.readiness_probe.failure_threshold == 3, f"Readiness probe failure threshold is {container.readiness_probe.failure_threshold}, expected 3"

def test_ac3_resource_limits_configured(image_provider_deployment):
    """AC-3: Verify CPU and memory resource requests/limits are set to specified values"""
    container = image_provider_deployment.spec.template.spec.containers[0]
    resources = container.resources
    assert resources is not None, "Resource requirements not configured"
    assert resources.requests is not None, "Resource requests not configured"
    assert resources.limits is not None, "Resource limits not configured"
    
    assert resources.requests["cpu"] == "10m", f"CPU request is {resources.requests['cpu']}, expected 10m"
    assert resources.limits["cpu"] == "100m", f"CPU limit is {resources.limits['cpu']}, expected 100m"
    assert resources.requests["memory"] == "32Mi", f"Memory request is {resources.requests['memory']}, expected 32Mi"
    assert resources.limits["memory"] == "128Mi", f"Memory limit is {resources.limits['memory']}, expected 128Mi"

def test_ac4_non_root_user_configured(running_image_provider_pod):
    """AC-4: Verify container runs as non-root user (UID 101) and root filesystem is read-only"""
    pod_name = running_image_provider_pod.metadata.name
    
    # Check whoami returns nginx user with UID 101
    result = subprocess.run(
        ["kubectl", "exec", pod_name, "--", "id", "-u"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "Failed to exec into pod to check user ID"
    assert result.stdout.strip() == "101", f"Running as UID {result.stdout.strip()}, expected 101"
    
    # Check root filesystem is read-only
    result = subprocess.run(
        ["kubectl", "exec", pod_name, "--", "touch", "/test-file"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "Able to write to root filesystem, expected read-only error"
    assert "Read-only file system" in result.stderr, "Expected read-only filesystem error not found"

def test_ac5_nginx_config_compatibility(running_image_provider_pod):
    """AC-5: Verify deployment applies successfully and service serves assets correctly on port 8080"""
    pod_name = running_image_provider_pod.metadata.name
    
    # Check nginx process is running
    result = subprocess.run(
        ["kubectl", "exec", pod_name, "--", "ps", "aux"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "Failed to check running processes"
    assert "nginx: master process" in result.stdout, "Nginx master process not running"
    assert "nginx: worker process" in result.stdout, "Nginx worker processes not running"
    
    # Test service responds on port 8080
    result = subprocess.run(
        ["kubectl", "exec", pod_name, "--", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8080/health"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "Failed to connect to nginx on port 8080"
    assert result.stdout.strip() == "200", f"Health endpoint returned status {result.stdout.strip()}, expected 200"
