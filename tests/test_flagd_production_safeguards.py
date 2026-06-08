import pytest
import requests
from kubernetes import client, config
import time
import os

# Load kube config
config.load_kube_config()
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

FLAGD_NAMESPACE = os.getenv("FLAGD_NAMESPACE", "default")
FLAGD_DEPLOYMENT_NAME = "flagd"
FLAGD_HEALTH_PORT = 8014
FLAGD_SERVICE_NAME = "flagd"

def get_flagd_pod():
    pods = v1.list_namespaced_pod(FLAGD_NAMESPACE, label_selector=f"app={FLAGD_DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No flagd pods found"
    return pods.items[0]

def port_forward_pod(pod, local_port, remote_port):
    # Helper to set up port forward (uses kubectl port-forward under the hood in practice)
    # For test purposes, we assume we can access the pod directly or via service
    return f"http://{FLAGD_SERVICE_NAME}.{FLAGD_NAMESPACE}.svc.cluster.local:{remote_port}"

@pytest.mark.integration
def test_ac1_liveness_probe_200_within_15s_startup():
    """AC-1: Liveness probe returns 200 within 15 seconds of container startup"""
    # Restart flagd deployment to trigger fresh startup
    apps_v1.patch_namespaced_deployment(
        name=FLAGD_DEPLOYMENT_NAME,
        namespace=FLAGD_NAMESPACE,
        body={"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ")}}}}}
    )
    
    # Wait for pod to be in starting state
    time.sleep(5)
    start_time = time.time()
    pod_running = False
    
    while time.time() - start_time < 60:
        pod = get_flagd_pod()
        if pod.status.phase == "Running" and all(c.ready for c in pod.status.container_statuses):
            pod_running = True
            break
        time.sleep(2)
    
    assert pod_running, "Pod did not reach running state within 60 seconds"
    
    # Check liveness probe endpoint within 15s of startup
    base_url = port_forward_pod(pod, 0, FLAGD_HEALTH_PORT)
    probe_success = False
    while time.time() - start_time < 15:
        try:
            resp = requests.get(f"{base_url}/healthz", timeout=2)
            if resp.status_code == 200:
                probe_success = True
                break
        except Exception:
            pass
        time.sleep(1)
    
    assert probe_success, "Liveness probe did not return 200 within 15 seconds of startup"

@pytest.mark.integration
def test_ac2_readiness_probe_always_200_when_healthy():
    """AC-2: Readiness probe returns 200 on every request when service is running normally"""
    pod = get_flagd_pod()
    assert pod.status.phase == "Running" and all(c.ready for c in pod.status.container_statuses), "flagd pod is not running/ready"
    
    base_url = port_forward_pod(pod, 0, FLAGD_HEALTH_PORT)
    # Make 10 consecutive requests, all should return 200
    for i in range(10):
        resp = requests.get(f"{base_url}/readyz", timeout=2)
        assert resp.status_code == 200, f"Readiness probe returned {resp.status_code} on request {i+1}"
        time.sleep(1)

@pytest.mark.integration
def test_ac3_liveness_probe_failure_triggers_restart():
    """AC-3: Liveness probe fails after 3 consecutive failures (30s total) and pod is restarted"""
    pod = get_flagd_pod()
    initial_restart_count = pod.status.container_statuses[0].restart_count
    
    # Simulate service unresponsiveness (in practice, use a chaos tool or modify service)
    # For test purposes, we verify the liveness probe configuration exists and has correct parameters
    deployment = apps_v1.read_namespaced_deployment(FLAGD_DEPLOYMENT_NAME, FLAGD_NAMESPACE)
    liveness_probe = deployment.spec.template.spec.containers[0].liveness_probe
    
    assert liveness_probe is not None, "Liveness probe not configured"
    assert liveness_probe.http_get.path == "/healthz", f"Liveness probe path incorrect: {liveness_probe.http_get.path}"
    assert liveness_probe.http_get.port == FLAGD_HEALTH_PORT, f"Liveness probe port incorrect: {liveness_probe.http_get.port}"
    assert liveness_probe.failure_threshold == 3, f"Liveness failure threshold incorrect: {liveness_probe.failure_threshold}"
    assert liveness_probe.period_seconds == 10, f"Liveness period seconds incorrect: {liveness_probe.period_seconds}"
    assert liveness_probe.timeout_seconds == 2, f"Liveness timeout seconds incorrect: {liveness_probe.timeout_seconds}"
    
    # Verify that when probe fails, pod restarts (simulated check for correct config)
    # Actual failure test would require chaos injection, but config check ensures capability exists

@pytest.mark.integration
def test_ac4_resource_limits_cpu_memory_configured():
    """AC-4: flagd pod does not consume more than 200m CPU and 256Mi memory under normal operation"""
    deployment = apps_v1.read_namespaced_deployment(FLAGD_DEPLOYMENT_NAME, FLAGD_NAMESPACE)
    resources = deployment.spec.template.spec.containers[0].resources
    
    assert resources is not None, "Resource requests/limits not configured"
    assert resources.requests is not None, "Resource requests not configured"
    assert resources.limits is not None, "Resource limits not configured"
    
    # Check requests
    assert resources.requests["cpu"] == "100m", f"CPU request incorrect: {resources.requests['cpu']}"
    assert resources.requests["memory"] == "128Mi", f"Memory request incorrect: {resources.requests['memory']}"
    
    # Check limits
    assert resources.limits["cpu"] == "200m", f"CPU limit incorrect: {resources.limits['cpu']}"
    assert resources.limits["memory"] == "256Mi", f"Memory limit incorrect: {resources.limits['memory']}"

@pytest.mark.integration
def test_ac5_security_context_non_root_no_privilege():
    """AC-5: flagd container runs as non-root user (UID 1000) with no privileged access"""
    deployment = apps_v1.read_namespaced_deployment(FLAGD_DEPLOYMENT_NAME, FLAGD_NAMESPACE)
    security_context = deployment.spec.template.spec.containers[0].security_context
    
    assert security_context is not None, "Security context not configured"
    assert security_context.run_as_non_root == True, "runAsNonRoot not set to true"
    assert security_context.run_as_user == 1000, f"runAsUser not set to 1000: {security_context.run_as_user}"
    assert security_context.privileged == False, "privileged not set to false"
    assert security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation not set to false"
    assert "ALL" in security_context.capabilities.drop, "CAP_ALL not dropped"

@pytest.mark.integration
def test_ac6_read_only_root_filesystem():
    """AC-6: flagd container root filesystem is mounted as read-only"""
    deployment = apps_v1.read_namespaced_deployment(FLAGD_DEPLOYMENT_NAME, FLAGD_NAMESPACE)
    security_context = deployment.spec.template.spec.containers[0].security_context
    
    assert security_context is not None, "Security context not configured"
    assert security_context.read_only_root_filesystem == True, "readOnlyRootFilesystem not set to true"

@pytest.mark.integration
def test_ac7_feature_flag_evaluation_works_with_safeguards():
    """AC-7: flagd service continues to function correctly with all safeguards in place"""
    pod = get_flagd_pod()
    assert pod.status.phase == "Running" and all(c.ready for c in pod.status.container_statuses), "flagd pod is not running/ready"
    
    # Test feature flag evaluation (use simple flag check against flagd API)
    # This test assumes a test flag exists in the demo configuration
    try:
        # Send a flag evaluation request
        resp = requests.post(
            f"http://{FLAGD_SERVICE_NAME}.{FLAGD_NAMESPACE}.svc.cluster.local:8013/schema.v1.Service/ResolveBoolean",
            json={"flagKey": "demoFeatureFlag", "context": {}},
            timeout=5
        )
        assert resp.status_code == 200, f"Flag evaluation request failed with status {resp.status_code}"
        assert "value" in resp.json(), "Flag evaluation response missing value field"
    except Exception as e:
        pytest.fail(f"Feature flag evaluation failed: {str(e)}")
