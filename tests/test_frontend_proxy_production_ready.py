import pytest
from kubernetes.client import V1Deployment, V1Container, V1Probe, V1HTTPGetAction, V1ResourceRequirements


# Helper function to find frontend-proxy container from deployment
def get_frontend_proxy_container(deployment: V1Deployment) -> V1Container:
    for container in deployment.spec.template.spec.containers:
        if container.name == "frontend-proxy":
            return container
    pytest.fail("frontend-proxy container not found in deployment")


def test_ac1_liveness_probe_config(frontend_proxy_deployment: V1Deployment):
    """AC-1: Liveness probe is configured with correct endpoint and minimum timing values"""
    container = get_frontend_proxy_container(frontend_proxy_deployment)
    
    # Check liveness probe exists
    assert container.liveness_probe is not None, "livenessProbe not configured for frontend-proxy"
    assert isinstance(container.liveness_probe, V1Probe)
    
    # Check HTTP GET action configuration
    assert container.liveness_probe.http_get is not None, "livenessProbe must use HTTP GET action"
    http_get: V1HTTPGetAction = container.liveness_probe.http_get
    assert http_get.port == 9901, f"livenessProbe port must be 9901, got {http_get.port}"
    assert http_get.path == "/ready", f"livenessProbe path must be /ready, got {http_get.path}"
    
    # Check timing requirements
    assert container.liveness_probe.initial_delay_seconds >= 5, \
        f"livenessProbe initialDelaySeconds must be >=5, got {container.liveness_probe.initial_delay_seconds}"
    assert container.liveness_probe.period_seconds >= 10, \
        f"livenessProbe periodSeconds must be >=10, got {container.liveness_probe.period_seconds}"
    assert container.liveness_probe.failure_threshold >= 3, \
        f"livenessProbe failureThreshold must be >=3, got {container.liveness_probe.failure_threshold}"


def test_ac2_readiness_probe_config(frontend_proxy_deployment: V1Deployment):
    """AC-2: Readiness probe is configured with correct endpoint and minimum timing values"""
    container = get_frontend_proxy_container(frontend_proxy_deployment)
    
    # Check readiness probe exists
    assert container.readiness_probe is not None, "readinessProbe not configured for frontend-proxy"
    assert isinstance(container.readiness_probe, V1Probe)
    
    # Check HTTP GET action configuration
    assert container.readiness_probe.http_get is not None, "readinessProbe must use HTTP GET action"
    http_get: V1HTTPGetAction = container.readiness_probe.http_get
    assert http_get.port == 9901, f"readinessProbe port must be 9901, got {http_get.port}"
    assert http_get.path == "/ready", f"readinessProbe path must be /ready, got {http_get.path}"
    
    # Check timing requirements
    assert container.readiness_probe.initial_delay_seconds >= 2, \
        f"readinessProbe initialDelaySeconds must be >=2, got {container.readiness_probe.initial_delay_seconds}"
    assert container.readiness_probe.period_seconds >= 5, \
        f"readinessProbe periodSeconds must be >=5, got {container.readiness_probe.period_seconds}"
    assert container.readiness_probe.failure_threshold >= 2, \
        f"readinessProbe failureThreshold must be >=2, got {container.readiness_probe.failure_threshold}"


def test_ac3_resource_requirements_config(frontend_proxy_deployment: V1Deployment):
    """AC-3: Resource requirements (requests/limits) are configured within allowed ranges"""
    container = get_frontend_proxy_container(frontend_proxy_deployment)
    
    # Check resources section exists
    assert container.resources is not None, "resources not configured for frontend-proxy"
    assert isinstance(container.resources, V1ResourceRequirements)
    
    # Check requests
    assert container.resources.requests is not None, "resources.requests not configured"
    requests = container.resources.requests
    assert "cpu" in requests, "CPU request not configured"
    assert requests["cpu"] >= "100m", f"CPU request must be >= 100m, got {requests['cpu']}"
    assert "memory" in requests, "Memory request not configured"
    assert requests["memory"] >= "128Mi", f"Memory request must be >= 128Mi, got {requests['memory']}"
    
    # Check limits
    assert container.resources.limits is not None, "resources.limits not configured"
    limits = container.resources.limits
    assert "cpu" in limits, "CPU limit not configured"
    assert limits["cpu"] <= "500m", f"CPU limit must be <= 500m, got {limits['cpu']}"
    assert "memory" in limits, "Memory limit not configured"
    assert limits["memory"] <= "256Mi", f"Memory limit must be <= 256Mi, got {limits['memory']}"


def test_ac4_healthy_probe_behavior(frontend_proxy_pod, k8s_client):
    """AC-4: When Envoy /ready returns 200 OK, pod is marked Ready and no restarts occur"""
    # Verify pod is in Running state
    assert frontend_proxy_pod.status.phase == "Running", f"Expected pod Running, got {frontend_proxy_pod.status.phase}"
    
    # Verify Ready condition is True
    ready_condition = next(c for c in frontend_proxy_pod.status.conditions if c.type == "Ready")
    assert ready_condition.status == "True", "Pod is not marked as Ready when Envoy is healthy"
    
    # Verify no restarts have been triggered
    container_status = next(s for s in frontend_proxy_pod.status.container_statuses if s.name == "frontend-proxy")
    assert container_status.restart_count == 0, f"Unexpected restarts: {container_status.restart_count}"


def test_ac5_unhealthy_probe_behavior(frontend_proxy_unhealthy_pod, k8s_client):
    """AC-5: When /ready returns non-200 for >= failureThreshold, pod is Not Ready and restarts"""
    # Verify Ready condition is False
    ready_condition = next(c for c in frontend_proxy_unhealthy_pod.status.conditions if c.type == "Ready")
    assert ready_condition.status == "False", "Pod should be marked Not Ready when Envoy is unhealthy"
    
    # Verify liveness probe triggers restart after failure threshold is exceeded
    container_status = next(s for s in frontend_proxy_unhealthy_pod.status.container_statuses if s.name == "frontend-proxy")
    assert container_status.restart_count >= 1, "Pod was not restarted after liveness probe failures"


def test_ac6_plaintext_probe_scheme(frontend_proxy_deployment_plaintext: V1Deployment):
    """AC-6: On plaintext deployments, probes use http scheme"""
    container = get_frontend_proxy_container(frontend_proxy_deployment_plaintext)
    
    # Check liveness probe scheme
    assert container.liveness_probe.http_get.scheme == "HTTP", \
        f"Liveness probe should use HTTP scheme for plaintext deployments, got {container.liveness_probe.http_get.scheme}"
    
    # Check readiness probe scheme
    assert container.readiness_probe.http_get.scheme == "HTTP", \
        f"Readiness probe should use HTTP scheme for plaintext deployments, got {container.readiness_probe.http_get.scheme}"


def test_ac7_tls_enabled_probe_scheme(frontend_proxy_deployment_tls: V1Deployment):
    """AC-7: On TLS-enabled deployments, probes use https scheme"""
    container = get_frontend_proxy_container(frontend_proxy_deployment_tls)
    
    # Check liveness probe scheme
    assert container.liveness_probe.http_get.scheme == "HTTPS", \
        f"Liveness probe should use HTTPS scheme for TLS-enabled deployments, got {container.liveness_probe.http_get.scheme}"
    
    # Check readiness probe scheme
    assert container.readiness_probe.http_get.scheme == "HTTPS", \
        f"Readiness probe should use HTTPS scheme for TLS-enabled deployments, got {container.readiness_probe.http_get.scheme}"
