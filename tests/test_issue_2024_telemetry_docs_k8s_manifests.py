import os
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Constants from spec
DEPLOYMENT_NAME = "telemetry-docs"
SERVICE_NAME = "telemetry-docs"
NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
EXPECTED_REPLICAS = 2
HTTP_PORT = 8080
SERVICE_PORT = 80
HEALTHZ_PATH = "/healthz"
EXPECTED_RESOURCES = {
    "requests": {"cpu": "100m", "memory": "128Mi"},
    "limits": {"cpu": "200m", "memory": "256Mi"}
}
EXPECTED_PROBE_CONFIG = {
    "liveness": {"initial_delay_seconds": 5, "period_seconds": 10, "path": HEALTHZ_PATH},
    "readiness": {"initial_delay_seconds": 2, "period_seconds": 5, "path": HEALTHZ_PATH}
}
EXPECTED_SECURITY_CONTEXT = {
    "run_as_non_root": True,
    "run_as_user": 101,
    "read_only_root_filesystem": True,
    "capabilities": {"drop": ["ALL"], "add": ["NET_BIND_SERVICE"]}
}
EXPECTED_ANNOTATIONS = {
    "opentelemetry.io/inject-nginx": "true",
    "opentelemetry.io/nginx-service-name": "telemetry-docs"
}

@pytest.fixture(scope="module")
def k8s_client():
    try:
        config.load_incluster_config()
    except:
        config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    return apps_v1, core_v1

def test_ac1_deployment_has_min_2_replicas_pods_running_ready(k8s_client):
    """AC-1: When Deployment applied, at least 2 pods are running and Ready within 60 seconds"""
    apps_v1, core_v1 = k8s_client
    try:
        deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    except ApiException as e:
        pytest.fail(f"Deployment {DEPLOYMENT_NAME} not found: {e}")
    
    assert deploy.spec.replicas >= EXPECTED_REPLICAS, f"Deployment has {deploy.spec.replicas} replicas, expected minimum {EXPECTED_REPLICAS}"
    
    selector = deploy.spec.selector.match_labels
    selector_str = ",".join([f"{k}={v}" for k, v in selector.items()])
    
    start_time = time.time()
    timeout = 60
    ready_pods = 0
    while time.time() - start_time < timeout:
        pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=selector_str)
        running_ready = 0
        for pod in pods.items:
            if pod.status.phase == "Running":
                if all([cs.ready for cs in pod.status.container_statuses]):
                    running_ready += 1
        if running_ready >= EXPECTED_REPLICAS:
            return
        time.sleep(2)
    pytest.fail(f"Only {running_ready} pods ready after {timeout}s, expected at least {EXPECTED_REPLICAS}")

def test_ac2_security_context_configured_correctly(k8s_client):
    """AC-2: All telemetry-docs pods run as non-root user (UID 101), read-only root fs, capabilities only NET_BIND_SERVICE"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    pod_spec = deploy.spec.template.spec
    container = pod_spec.containers[0]
    
    sec_ctx = container.security_context or pod_spec.security_context
    assert sec_ctx is not None, "No security context defined"
    
    assert sec_ctx.run_as_non_root == EXPECTED_SECURITY_CONTEXT["run_as_non_root"], "runAsNonRoot not set to true"
    assert sec_ctx.run_as_user == EXPECTED_SECURITY_CONTEXT["run_as_user"], f"runAsUser not set to {EXPECTED_SECURITY_CONTEXT['run_as_user']}"
    assert sec_ctx.read_only_root_filesystem == EXPECTED_SECURITY_CONTEXT["read_only_root_filesystem"], "readOnlyRootFilesystem not set to true"
    assert sec_ctx.capabilities is not None, "No capabilities defined"
    assert sorted(sec_ctx.capabilities.drop) == sorted(EXPECTED_SECURITY_CONTEXT["capabilities"]["drop"]), "Not all capabilities dropped"
    assert sorted(sec_ctx.capabilities.add) == sorted(EXPECTED_SECURITY_CONTEXT["capabilities"]["add"]), "Expected NET_BIND_SERVICE capability"

def test_ac3_liveness_probe_triggers_restart_on_healthz_failure(k8s_client):
    """AC-3: Liveness probe fails (triggers restart) when Nginx not responding on /healthz endpoint"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    
    assert container.liveness_probe is not None, "No liveness probe defined"
    assert container.liveness_probe.http_get is not None, "Liveness probe not HTTP GET"
    assert container.liveness_probe.http_get.path == EXPECTED_PROBE_CONFIG["liveness"]["path"], f"Liveness probe path wrong, expected {EXPECTED_PROBE_CONFIG['liveness']['path']}"
    assert container.liveness_probe.http_get.port == HTTP_PORT, f"Liveness probe port wrong, expected {HTTP_PORT}"
    assert container.liveness_probe.initial_delay_seconds == EXPECTED_PROBE_CONFIG["liveness"]["initial_delay_seconds"], "Liveness probe initial delay wrong"
    assert container.liveness_probe.period_seconds == EXPECTED_PROBE_CONFIG["liveness"]["period_seconds"], "Liveness probe period wrong"

def test_ac4_readiness_probe_removes_pod_on_healthz_failure(k8s_client):
    """AC-4: Readiness probe fails (removes pod from service endpoints) when /healthz returns non-200 status"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    
    assert container.readiness_probe is not None, "No readiness probe defined"
    assert container.readiness_probe.http_get is not None, "Readiness probe not HTTP GET"
    assert container.readiness_probe.http_get.path == EXPECTED_PROBE_CONFIG["readiness"]["path"], f"Readiness probe path wrong, expected {EXPECTED_PROBE_CONFIG['readiness']['path']}"
    assert container.readiness_probe.http_get.port == HTTP_PORT, f"Readiness probe port wrong, expected {HTTP_PORT}"
    assert container.readiness_probe.initial_delay_seconds == EXPECTED_PROBE_CONFIG["readiness"]["initial_delay_seconds"], "Readiness probe initial delay wrong"
    assert container.readiness_probe.period_seconds == EXPECTED_PROBE_CONFIG["readiness"]["period_seconds"], "Readiness probe period wrong"

def test_ac5_service_configured_correctly(k8s_client):
    """AC-5: Service telemetry-docs is created, type ClusterIP, forwards traffic from port 80 to pod port 8080"""
    _, core_v1 = k8s_client
    try:
        svc = core_v1.read_namespaced_service(name=SERVICE_NAME, namespace=NAMESPACE)
    except ApiException as e:
        pytest.fail(f"Service {SERVICE_NAME} not found: {e}")
    
    assert svc.spec.type == "ClusterIP", f"Service type {svc.spec.type} is not ClusterIP"
    port_found = False
    for port in svc.spec.ports:
        if port.port == SERVICE_PORT and port.target_port == HTTP_PORT:
            port_found = True
            break
    assert port_found, f"Service does not expose port {SERVICE_PORT} targeting container port {HTTP_PORT}"
    
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    deploy_labels = deploy.spec.template.metadata.labels
    selector = svc.spec.selector
    assert selector.get("app") == "telemetry-docs", "Service selector does not match app: telemetry-docs"
    for k, v in selector.items():
        assert k in deploy_labels, f"Selector key {k} not present in deployment pod labels"
        assert deploy_labels[k] == v, f"Selector value for {k} does not match deployment label"

def test_ac6_otel_annotations_present(k8s_client):
    """AC-6: OpenTelemetry annotations are present on Deployment pod template enabling Nginx tracing injection"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    pod_annotations = deploy.spec.template.metadata.annotations
    
    assert pod_annotations is not None, "No pod annotations defined"
    for k, v in EXPECTED_ANNOTATIONS.items():
        assert k in pod_annotations, f"Missing expected annotation {k}"
        assert pod_annotations[k] == v, f"Annotation {k} has wrong value, expected {v}"

def test_ac7_resource_limits_enforced(k8s_client):
    """AC-7: Resource requests and limits are enforced: pods cannot consume more than 200m CPU and 256Mi memory"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    
    assert container.resources is not None, "No resources defined for container"
    
    requests = container.resources.requests
    assert requests is not None, "No resource requests defined"
    assert "cpu" in requests, "No CPU request defined"
    assert "memory" in requests, "No memory request defined"
    cpu_req = float(requests["cpu"].replace("m", "")) if "m" in requests["cpu"] else float(requests["cpu"]) * 1000
    mem_req = int(requests["memory"].replace("Mi", "")) if "Mi" in requests["memory"] else int(requests["memory"].replace("Gi", "")) * 1024
    assert cpu_req == 100, f"CPU request {cpu_req}m, expected 100m"
    assert mem_req == 128, f"Memory request {mem_req}Mi, expected 128Mi"
    
    limits = container.resources.limits
    assert limits is not None, "No resource limits defined"
    assert "cpu" in limits, "No CPU limit defined"
    assert "memory" in limits, "No memory limit defined"
    cpu_limit = float(limits["cpu"].replace("m", "")) if "m" in limits["cpu"] else float(limits["cpu"]) * 1000
    mem_limit = int(limits["memory"].replace("Mi", "")) if "Mi" in limits["memory"] else int(limits["memory"].replace("Gi", "")) * 1024
    assert cpu_limit == 200, f"CPU limit {cpu_limit}m, expected 200m"
    assert mem_limit == 256, f"Memory limit {mem_limit}Mi, expected 256Mi"
