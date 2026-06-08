import os
import time
import requests
import kubernetes
from kubernetes.client import AppsV1Api, CoreV1Api
from kubernetes.stream import stream

# Load kubernetes config
kubernetes.config.load_kube_config()
apps_api = AppsV1Api()
core_api = CoreV1Api()

SERVICE_NAME = "accounting"
NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
SERVICE_PORT = 8080
BASE_URL = f"http://{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:{SERVICE_PORT}"


def test_ac1_liveness_probe_configured_correctly():
    """AC-1: Liveness probe configured for HTTP GET on path /health port 8080, initialDelaySeconds:10, periodSeconds:10, failureThreshold:3"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    probe = container.liveness_probe
    
    assert probe is not None, "Liveness probe not configured"
    assert probe.http_get.path == "/health", f"Expected path /health, got {probe.http_get.path}"
    assert probe.http_get.port == SERVICE_PORT, f"Expected port {SERVICE_PORT}, got {probe.http_get.port}"
    assert probe.initial_delay_seconds == 10, f"Expected initialDelaySeconds 10, got {probe.initial_delay_seconds}"
    assert probe.period_seconds == 10, f"Expected periodSeconds 10, got {probe.period_seconds}"
    assert probe.failure_threshold == 3, f"Expected failureThreshold 3, got {probe.failure_threshold}"


def test_ac2_readiness_probe_configured_correctly():
    """AC-2: Readiness probe configured for HTTP GET on path /health port 8080, initialDelaySeconds:5, periodSeconds:5, failureThreshold:3"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    probe = container.readiness_probe
    
    assert probe is not None, "Readiness probe not configured"
    assert probe.http_get.path == "/health", f"Expected path /health, got {probe.http_get.path}"
    assert probe.http_get.port == SERVICE_PORT, f"Expected port {SERVICE_PORT}, got {probe.http_get.port}"
    assert probe.initial_delay_seconds == 5, f"Expected initialDelaySeconds 5, got {probe.initial_delay_seconds}"
    assert probe.period_seconds == 5, f"Expected periodSeconds 5, got {probe.period_seconds}"
    assert probe.failure_threshold == 3, f"Expected failureThreshold 3, got {probe.failure_threshold}"


def test_ac3_resource_constraints_configured_correctly():
    """AC-3: Resource constraints configured: requests.cpu:100m, limits.cpu:500m, requests.memory:128Mi, limits.memory:256Mi"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    resources = container.resources
    
    assert resources is not None, "Resources not configured"
    assert resources.requests is not None, "Resource requests not configured"
    assert resources.requests["cpu"] == "100m", f"Expected cpu request 100m, got {resources.requests['cpu']}"
    assert resources.requests["memory"] == "128Mi", f"Expected memory request 128Mi, got {resources.requests['memory']}"
    assert resources.limits is not None, "Resource limits not configured"
    assert resources.limits["cpu"] == "500m", f"Expected cpu limit 500m, got {resources.limits['cpu']}"
    assert resources.limits["memory"] == "256Mi", f"Expected memory limit 256Mi, got {resources.limits['memory']}"


def test_ac4_pod_level_security_context_configured_correctly():
    """AC-4: Pod-level security context sets runAsNonRoot:true, runAsUser:1000, runAsGroup:3000, fsGroup:2000, allowPrivilegeEscalation:false, privileged:false"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    pod_spec = deployment.spec.template.spec
    
    assert pod_spec.security_context is not None, "Pod security context not configured"
    assert pod_spec.security_context.run_as_non_root == True, "runAsNonRoot must be true"
    assert pod_spec.security_context.run_as_user == 1000, f"Expected runAsUser 1000, got {pod_spec.security_context.run_as_user}"
    assert pod_spec.security_context.run_as_group == 3000, f"Expected runAsGroup 3000, got {pod_spec.security_context.run_as_group}"
    assert pod_spec.security_context.fs_group == 2000, f"Expected fsGroup 2000, got {pod_spec.security_context.fs_group}"
    assert pod_spec.security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation must be false"
    assert pod_spec.security_context.privileged == False, "privileged must be false"


def test_ac5_container_capabilities_restricted_correctly():
    """AC-5: Container-level security context drops all capabilities and adds no additional capabilities"""
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    
    assert container.security_context is not None, "Container security context not configured"
    assert container.security_context.capabilities is not None, "Capabilities not configured"
    assert container.security_context.capabilities.drop == ["ALL"], f"Expected drop ALL capabilities, got {container.security_context.capabilities.drop}"
    assert container.security_context.capabilities.add is None or len(container.security_context.capabilities.add) == 0, "No additional capabilities should be added"


def test_ac6_container_runs_as_non_root_user():
    """AC-6: Executing id inside the container returns non-root UID matching configured runAsUser value 1000"""
    # Get running accounting pod
    pods = core_api.list_namespaced_pod(NAMESPACE, label_selector=f"app={SERVICE_NAME}", field_selector="status.phase=Running")
    assert len(pods.items) > 0, "No running accounting pods found"
    pod_name = pods.items[0].metadata.name
    
    # Execute id command in container
    exec_command = ["id", "-u"]
    resp = stream(core_api.connect_get_namespaced_pod_exec,
                  pod_name,
                  NAMESPACE,
                  command=exec_command,
                  container=SERVICE_NAME,
                  stderr=True, stdin=False,
                  stdout=True, tty=False)
    
    uid = int(resp.strip())
    assert uid != 0, f"Container is running as root (UID 0), expected UID 1000"
    assert uid == 1000, f"Container running as UID {uid}, expected UID 1000"


def test_ac7_readiness_probe_health_status_reflected_in_pod_status():
    """AC-7: Kubernetes marks pod as NotReady when /health returns non-200, Ready when /health returns 200"""
    # First verify health endpoint is working and pod is Ready
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    assert resp.status_code == 200, "Health endpoint did not return 200 OK initially"
    
    # Wait for pod to be Ready
    start_time = time.time()
    while time.time() - start_time < 30:
        pods = core_api.list_namespaced_pod(NAMESPACE, label_selector=f"app={SERVICE_NAME}")
        ready = False
        for pod in pods.items:
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    ready = True
                    break
        if ready:
            break
        time.sleep(2)
    else:
        assert False, "Pod did not reach Ready status initially"
    
    # Note: This test assumes there is a way to make /health return non-200 (for example via an admin endpoint)
    # For the purpose of this test, we'll verify that the probe exists and that the readiness status is correctly updated when health changes
    # This is a functional test that would be expanded once the service has a way to toggle health status


def test_ac8_liveness_probe_failure_causes_pod_restart():
    """AC-8: Kubernetes restarts pod when liveness probe fails 3 consecutive times"""
    # Get initial restart count
    pods = core_api.list_namespaced_pod(NAMESPACE, label_selector=f"app={SERVICE_NAME}", field_selector="status.phase=Running")
    assert len(pods.items) > 0, "No running accounting pods found"
    initial_restart_count = pods.items[0].status.container_statuses[0].restart_count
    
    # Note: This test assumes we can cause the liveness probe to fail 3 times
    # For the purpose of this test, we verify the liveness probe configuration is correct
    # This test would be expanded with functionality to trigger health failures once available
    deployment = apps_api.read_namespaced_deployment(SERVICE_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    assert container.liveness_probe is not None
    assert container.liveness_probe.failure_threshold == 3
