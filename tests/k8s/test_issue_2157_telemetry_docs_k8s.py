"""
Integration tests for telemetry-docs service Kubernetes manifests (issue #2157)
"""
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
import subprocess
import os

# Constants from spec
DEPLOYMENT_NAME = "telemetry-docs"
SERVICE_NAME = "telemetry-docs"
NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
EXPECTED_PROBE_PATH = "/"
EXPECTED_CONTAINER_PORT = 80
EXPECTED_PROBE_PARAMS = {
    "failureThreshold": 3,
    "periodSeconds": 10,
    "initialDelaySeconds": 5
}
EXPECTED_RESOURCES = {
    "requests": {"cpu": "10m", "memory": "32Mi"},
    "limits": {"cpu": "100m", "memory": "64Mi"}
}
EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 101,
    "readOnlyRootFilesystem": True,
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]}
}

@pytest.fixture(scope="module")
def k8s_client():
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    yield apps_v1, core_v1

def test_ac1_deployment_pod_runs_within_60s(k8s_client):
    """AC-1: Deployment applied, at least 1 pod reaches Running state within 60s"""
    apps_v1, core_v1 = k8s_client

    # Check deployment exists first
    try:
        deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    except ApiException as e:
        pytest.fail(f"Deployment {DEPLOYMENT_NAME} not found: {e}")
    
    # Wait for pod to be running
    start_time = time.time()
    while time.time() - start_time < 60:
        pods = core_v1.list_namespaced_pod(
            NAMESPACE,
            label_selector=f"app.kubernetes.io/name={DEPLOYMENT_NAME}"
        )
        if len(pods.items) > 0:
            for pod in pods.items:
                if pod.status.phase == "Running" and all(c.ready for c in pod.status.container_statuses):
                    return
        time.sleep(2)
    
    pytest.fail(f"No running pods for deployment {DEPLOYMENT_NAME} after 60 seconds")

def test_ac2_deployment_has_correct_probes(k8s_client):
    """AC-2: Deployment has livenessProbe and readinessProbe configured correctly"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deploy.spec.template.spec.containers[0]

    # Check liveness probe
    assert container.liveness_probe is not None, "livenessProbe missing"
    assert container.liveness_probe.http_get.path == EXPECTED_PROBE_PATH
    assert container.liveness_probe.http_get.port == EXPECTED_CONTAINER_PORT
    assert container.liveness_probe.failure_threshold == EXPECTED_PROBE_PARAMS["failureThreshold"]
    assert container.liveness_probe.period_seconds == EXPECTED_PROBE_PARAMS["periodSeconds"]
    assert container.liveness_probe.initial_delay_seconds == EXPECTED_PROBE_PARAMS["initialDelaySeconds"]

    # Check readiness probe
    assert container.readiness_probe is not None, "readinessProbe missing"
    assert container.readiness_probe.http_get.path == EXPECTED_PROBE_PATH
    assert container.readiness_probe.http_get.port == EXPECTED_CONTAINER_PORT
    assert container.readiness_probe.failure_threshold == EXPECTED_PROBE_PARAMS["failureThreshold"]
    assert container.readiness_probe.period_seconds == EXPECTED_PROBE_PARAMS["periodSeconds"]
    assert container.readiness_probe.initial_delay_seconds == EXPECTED_PROBE_PARAMS["initialDelaySeconds"]

def test_ac3_deployment_has_correct_resource_limits(k8s_client):
    """AC-3: Deployment has correct resource requests and limits"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deploy.spec.template.spec.containers[0]

    res = container.resources.to_dict()
    assert "requests" in res
    assert "limits" in res
    assert res["requests"]["cpu"] == EXPECTED_RESOURCES["requests"]["cpu"]
    assert res["requests"]["memory"] == EXPECTED_RESOURCES["requests"]["memory"]
    assert res["limits"]["cpu"] == EXPECTED_RESOURCES["limits"]["cpu"]
    assert res["limits"]["memory"] == EXPECTED_RESOURCES["limits"]["memory"]

def test_ac4_deployment_has_correct_security_context(k8s_client):
    """AC-4: Deployment has production security context configured"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    sc = container.security_context.to_dict()

    assert sc["run_as_non_root"] == EXPECTED_SECURITY_CONTEXT["runAsNonRoot"]
    assert sc["run_as_user"] == EXPECTED_SECURITY_CONTEXT["runAsUser"]
    assert sc["read_only_root_filesystem"] == EXPECTED_SECURITY_CONTEXT["readOnlyRootFilesystem"]
    assert sc["allow_privilege_escalation"] == EXPECTED_SECURITY_CONTEXT["allowPrivilegeEscalation"]
    assert "drop" in sc["capabilities"]
    assert "ALL" in sc["capabilities"]["drop"]

def test_ac5_service_configured_correctly(k8s_client):
    """AC-5: Service exists, is ClusterIP, exposes port 80 to container port 80"""
    _, core_v1 = k8s_client
    svc = core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)

    assert svc.spec.type == "ClusterIP"
    assert len(svc.spec.ports) == 1
    port = svc.spec.ports[0]
    assert port.port == 80
    assert port.target_port == EXPECTED_CONTAINER_PORT
    assert svc.spec.selector["app.kubernetes.io/name"] == DEPLOYMENT_NAME

def test_ac6_service_responds_200_ok_from_cluster(k8s_client):
    """AC-6: Curl from another pod in cluster returns 200 OK with static site content"""
    # Check service exists first
    _, core_v1 = k8s_client
    try:
        core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    except ApiException as e:
        pytest.fail(f"Service {SERVICE_NAME} not found: {e}")
    
    # Run curl test pod
    cmd = [
        "kubectl", "run", "-n", NAMESPACE, "--rm", "-i", "--restart=Never", "curl-test",
        "--image=curlimages/curl:latest", "--",
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://{SERVICE_NAME}:80"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"Curl command failed: {result.stderr}"
        assert result.stdout.strip() == "200", f"Expected 200 response, got {result.stdout.strip()}"
    finally:
        # Clean up test pod if it's still running
        subprocess.run(["kubectl", "delete", "pod", "-n", NAMESPACE, "curl-test", "--ignore-not-found"], capture_output=True)
