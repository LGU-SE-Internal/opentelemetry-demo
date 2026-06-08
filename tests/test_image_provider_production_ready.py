import os
import time
import requests
import kubernetes
from kubernetes.client import AppsV1Api, CoreV1Api

# Load kubernetes config
kubernetes.config.load_kube_config()
apps_api = AppsV1Api()
core_api = CoreV1Api()

SERVICE_NAME = "image-provider"
NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
SERVICE_PORT = 8080
BASE_URL = f"http://{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:{SERVICE_PORT}"


def test_ac1_healthz_endpoint_returns_200_ok():
    """AC-1: /healthz endpoint returns 200 OK with no redirects"""
    resp = requests.get(f"{BASE_URL}/healthz", allow_redirects=False)
    assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}"
    assert resp.text.strip() == "", "Expected empty response body for /healthz"


def test_ac2_liveness_probe_configured_correctly():
    """AC-2: Liveness probe configuration matches spec"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    probe = container.liveness_probe
    
    assert probe is not None, "Liveness probe not configured"
    assert probe.http_get.path == "/healthz", f"Expected path /healthz, got {probe.http_get.path}"
    assert probe.http_get.port == SERVICE_PORT, f"Expected port {SERVICE_PORT}, got {probe.http_get.port}"
    assert probe.initial_delay_seconds == 5, f"Expected initialDelaySeconds 5, got {probe.initial_delay_seconds}"
    assert probe.period_seconds == 10, f"Expected periodSeconds 10, got {probe.period_seconds}"
    assert probe.timeout_seconds == 2, f"Expected timeoutSeconds 2, got {probe.timeout_seconds}"
    assert probe.failure_threshold == 3, f"Expected failureThreshold 3, got {probe.failure_threshold}"


def test_ac3_readiness_probe_configured_correctly():
    """AC-3: Readiness probe configuration matches spec"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    probe = container.readiness_probe
    
    assert probe is not None, "Readiness probe not configured"
    assert probe.http_get.path == "/healthz", f"Expected path /healthz, got {probe.http_get.path}"
    assert probe.http_get.port == SERVICE_PORT, f"Expected port {SERVICE_PORT}, got {probe.http_get.port}"
    assert probe.initial_delay_seconds == 2, f"Expected initialDelaySeconds 2, got {probe.initial_delay_seconds}"
    assert probe.period_seconds == 5, f"Expected periodSeconds 5, got {probe.period_seconds}"
    assert probe.timeout_seconds == 2, f"Expected timeoutSeconds 2, got {probe.timeout_seconds}"
    assert probe.failure_threshold == 3, f"Expected failureThreshold 3, got {probe.failure_threshold}"


def test_ac4_resource_constraints_configured():
    """AC-4: Resource requests and limits match spec"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    resources = container.resources
    
    assert resources is not None, "Resources not configured"
    assert resources.requests is not None, "Resource requests not configured"
    assert resources.requests["cpu"] == "10m", f"Expected cpu request 10m, got {resources.requests['cpu']}"
    assert resources.requests["memory"] == "32Mi", f"Expected memory request 32Mi, got {resources.requests['memory']}"
    assert resources.limits is not None, "Resource limits not configured"
    assert resources.limits["cpu"] == "50m", f"Expected cpu limit 50m, got {resources.limits['cpu']}"
    assert resources.limits["memory"] == "64Mi", f"Expected memory limit 64Mi, got {resources.limits['memory']}"


def test_ac5_security_context_configured():
    """AC-5: Security context matches spec at pod and container levels"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    pod_spec = deployment.spec.template.spec
    container = pod_spec.containers[0]
    
    # Pod level security context
    assert pod_spec.security_context is not None, "Pod security context not configured"
    assert pod_spec.security_context.run_as_non_root == True, "runAsNonRoot must be true"
    assert pod_spec.security_context.run_as_user == 101, f"Expected runAsUser 101, got {pod_spec.security_context.run_as_user}"
    assert pod_spec.security_context.run_as_group == 101, f"Expected runAsGroup 101, got {pod_spec.security_context.run_as_group}"
    
    # Container level security context
    assert container.security_context is not None, "Container security context not configured"
    assert container.security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation must be false"
    assert container.security_context.capabilities.drop == ["ALL"], f"Expected drop all capabilities, got {container.security_context.capabilities.drop}"
    assert container.security_context.read_only_root_filesystem == True, "readOnlyRootFilesystem must be true"


def test_ac6_pods_reach_ready_status_within_30s():
    """AC-6: All image-provider pods reach Ready status within 30 seconds, no probe failures"""
    start_time = time.time()
    while time.time() - start_time < 30:
        pods = core_api.list_namespaced_pod(NAMESPACE, label_selector=f"app={SERVICE_NAME}")
        all_ready = True
        for pod in pods.items:
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status != "True":
                    all_ready = False
                    break
        if all_ready:
            break
        time.sleep(2)
    else:
        assert False, "Pods did not reach Ready status within 30 seconds"
    
    # Check no recent probe failures
    events = core_api.list_namespaced_event(NAMESPACE, field_selector=f"involvedObject.kind=Pod,involvedObject.name={pods.items[0].metadata.name}")
    probe_failures = [e for e in events.items if "probe failed" in e.message.lower()]
    assert len(probe_failures) == 0, f"Found probe failure events: {[e.message for e in probe_failures]}"


def test_ac7_existing_product_images_return_200():
    """AC-7: Existing product image endpoints continue to function correctly"""
    # Test common product images from the demo
    test_images = [
        "/images/1.jpg",
        "/images/2.jpg",
        "/images/3.jpg",
        "/images/4.jpg",
        "/images/5.jpg"
    ]
    for img_path in test_images:
        resp = requests.get(f"{BASE_URL}{img_path}", timeout=5)
        assert resp.status_code == 200, f"Image {img_path} returned status {resp.status_code}"
        assert len(resp.content) > 0, f"Image {img_path} returned empty content"

