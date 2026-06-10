import pytest
from kubernetes import client, config
import subprocess
import time
import requests

# Configs
QUOTE_SERVICE_DEPLOYMENT_NAME = "quote-service"
QUOTE_SERVICE_SERVICE_ACCOUNT_NAME = "quote-service"
QUOTE_SERVICE_SERVICE_NAME = "quote-service"
QUOTE_SERVICE_NAMESPACE = "opentelemetry-demo"
QUOTE_SERVICE_PORT = 8080
EXPECTED_UID = 10001
EXPECTED_GID = 10001

@pytest.fixture(scope="module")
def k8s_client():
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    return apps_v1, core_v1

@pytest.fixture(scope="module")
def quote_service_resources(k8s_client):
    apps_v1, core_v1 = k8s_client
    resources = {}
    # AC-1: Check all resources exist
    try:
        resources["serviceaccount"] = core_v1.read_namespaced_service_account(QUOTE_SERVICE_SERVICE_ACCOUNT_NAME, QUOTE_SERVICE_NAMESPACE)
        resources["service"] = core_v1.read_namespaced_service(QUOTE_SERVICE_SERVICE_NAME, QUOTE_SERVICE_NAMESPACE)
        resources["deployment"] = apps_v1.read_namespaced_deployment(QUOTE_SERVICE_DEPLOYMENT_NAME, QUOTE_SERVICE_NAMESPACE)
        return resources
    except client.exceptions.ApiException as e:
        pytest.fail(f"Required quote service resource not found: {e}")

@pytest.fixture(scope="module")
def running_quote_service_pod(k8s_client):
    _, core_v1 = k8s_client
    pods = core_v1.list_namespaced_pod(QUOTE_SERVICE_NAMESPACE, label_selector="app.kubernetes.io/name=quote-service,app.kubernetes.io/component=service")
    for pod in pods.items:
        if pod.status.phase == "Running" and all(c.ready for c in pod.status.container_statuses):
            return pod
    pytest.fail("No running quote service pod found")

def test_ac1_all_resources_created_successfully(quote_service_resources):
    """AC-1: All three resources (ServiceAccount, Service, Deployment) are created successfully without validation errors"""
    assert "serviceaccount" in quote_service_resources
    assert "service" in quote_service_resources
    assert "deployment" in quote_service_resources
    
    sa = quote_service_resources["serviceaccount"]
    assert sa.metadata.name == QUOTE_SERVICE_SERVICE_ACCOUNT_NAME
    assert sa.metadata.namespace == QUOTE_SERVICE_NAMESPACE
    
    svc = quote_service_resources["service"]
    assert svc.metadata.name == QUOTE_SERVICE_SERVICE_NAME
    assert svc.metadata.namespace == QUOTE_SERVICE_NAMESPACE
    
    deploy = quote_service_resources["deployment"]
    assert deploy.metadata.name == QUOTE_SERVICE_DEPLOYMENT_NAME
    assert deploy.metadata.namespace == QUOTE_SERVICE_NAMESPACE

def test_ac2_resource_requests_limits_configured(quote_service_resources):
    """AC-2: Deployment defines CPU requests=100m, CPU limits=200m, memory requests=128Mi, memory limits=256Mi"""
    deploy = quote_service_resources["deployment"]
    container = deploy.spec.template.spec.containers[0]
    assert hasattr(container, 'resources'), "resources section not defined"
    
    resources = container.resources
    assert hasattr(resources, 'requests'), "resource requests not defined"
    assert resources.requests.get('cpu') == "100m", f"CPU request should be 100m, got {resources.requests.get('cpu')}"
    assert resources.requests.get('memory') == "128Mi", f"Memory request should be 128Mi, got {resources.requests.get('memory')}"
    
    assert hasattr(resources, 'limits'), "resource limits not defined"
    assert resources.limits.get('cpu') == "200m", f"CPU limit should be 200m, got {resources.limits.get('cpu')}"
    assert resources.limits.get('memory') == "256Mi", f"Memory limit should be 256Mi, got {resources.limits.get('memory')}"

def test_ac3_liveness_probe_configured(quote_service_resources):
    """AC-3: Liveness probe configured to GET /health/liveness on 8080, initialDelaySeconds=5, periodSeconds=10, timeoutSeconds=1, failureThreshold=3"""
    deploy = quote_service_resources["deployment"]
    container = deploy.spec.template.spec.containers[0]
    assert hasattr(container, 'liveness_probe'), "livenessProbe not defined"
    
    liveness = container.liveness_probe
    assert hasattr(liveness, 'http_get'), "livenessProbe should use HTTP GET"
    assert liveness.http_get.path == "/health/liveness", f"Liveness path should be /health/liveness, got {liveness.http_get.path}"
    assert liveness.http_get.port == QUOTE_SERVICE_PORT, f"Liveness port should be {QUOTE_SERVICE_PORT}, got {liveness.http_get.port}"
    
    assert liveness.initial_delay_seconds == 5, f"initialDelaySeconds should be 5, got {liveness.initial_delay_seconds}"
    assert liveness.period_seconds == 10, f"periodSeconds should be 10, got {liveness.period_seconds}"
    assert liveness.timeout_seconds == 1, f"timeoutSeconds should be 1, got {liveness.timeout_seconds}"
    assert liveness.failure_threshold == 3, f"failureThreshold should be 3, got {liveness.failure_threshold}"

def test_ac4_readiness_probe_configured(quote_service_resources):
    """AC-4: Readiness probe configured to GET /health/readiness on 8080, initialDelaySeconds=2, periodSeconds=5, timeoutSeconds=1, failureThreshold=3"""
    deploy = quote_service_resources["deployment"]
    container = deploy.spec.template.spec.containers[0]
    assert hasattr(container, 'readiness_probe'), "readinessProbe not defined"
    
    readiness = container.readiness_probe
    assert hasattr(readiness, 'http_get'), "readinessProbe should use HTTP GET"
    assert readiness.http_get.path == "/health/readiness", f"Readiness path should be /health/readiness, got {readiness.http_get.path}"
    assert readiness.http_get.port == QUOTE_SERVICE_PORT, f"Readiness port should be {QUOTE_SERVICE_PORT}, got {readiness.http_get.port}"
    
    assert readiness.initial_delay_seconds == 2, f"initialDelaySeconds should be 2, got {readiness.initial_delay_seconds}"
    assert readiness.period_seconds == 5, f"periodSeconds should be 5, got {readiness.period_seconds}"
    assert readiness.timeout_seconds == 1, f"timeoutSeconds should be 1, got {readiness.timeout_seconds}"
    assert readiness.failure_threshold == 3, f"failureThreshold should be 3, got {readiness.failure_threshold}"

def test_ac5_security_context_configured(quote_service_resources):
    """AC-5: Pod security context has runAsNonRoot=true, runAsUser=10001, runAsGroup=10001, readOnlyRootFilesystem=true, capabilities drop ALL"""
    deploy = quote_service_resources["deployment"]
    pod_spec = deploy.spec.template.spec
    assert hasattr(pod_spec, 'security_context'), "Pod security context not defined"
    
    pod_sc = pod_spec.security_context
    assert pod_sc.run_as_non_root == True, "runAsNonRoot should be true"
    assert pod_sc.run_as_user == EXPECTED_UID, f"runAsUser should be {EXPECTED_UID}, got {pod_sc.run_as_user}"
    assert pod_sc.run_as_group == EXPECTED_GID, f"runAsGroup should be {EXPECTED_GID}, got {pod_sc.run_as_group}"
    
    container = deploy.spec.template.spec.containers[0]
    assert hasattr(container, 'security_context'), "Container security context not defined"
    container_sc = container.security_context
    assert container_sc.read_only_root_filesystem == True, "readOnlyRootFilesystem should be true"
    assert hasattr(container_sc.capabilities, 'drop'), "Capabilities drop list not defined"
    assert "ALL" in container_sc.capabilities.drop, "Should drop ALL capabilities"

def test_ac6_otel_environment_variables_configured(quote_service_resources):
    """AC-6: Container has required OTel environment variables set exactly as spec"""
    deploy = quote_service_resources["deployment"]
    container = deploy.spec.template.spec.containers[0]
    assert hasattr(container, 'env'), "Environment variables not defined on container"
    
    env_vars = {e.name: e.value for e in container.env}
    assert env_vars.get("OTEL_SERVICE_NAME") == "quote-service", f"OTEL_SERVICE_NAME should be quote-service, got {env_vars.get('OTEL_SERVICE_NAME')}"
    assert env_vars.get("OTEL_EXPORTER_OTLP_ENDPOINT") == "http://otelcol.opentelemetry-demo.svc.cluster.local:4318", f"OTEL_EXPORTER_OTLP_ENDPOINT incorrect"
    assert env_vars.get("OTEL_EXPORTER_OTLP_PROTOCOL") == "http/protobuf", f"OTEL_EXPORTER_OTLP_PROTOCOL should be http/protobuf"
    assert env_vars.get("OTEL_RESOURCE_ATTRIBUTES") == "service.namespace=opentelemetry-demo", f"OTEL_RESOURCE_ATTRIBUTES incorrect"

def test_ac7_service_routes_traffic_correctly(running_quote_service_pod, k8s_client):
    """AC-7: Service correctly routes traffic to pods, returns 200 OK for /health/readiness endpoint"""
    _, core_v1 = k8s_client
    svc = core_v1.read_namespaced_service(QUOTE_SERVICE_SERVICE_NAME, QUOTE_SERVICE_NAMESPACE)
    
    # Test service access via kube proxy or direct service IP
    svc_ip = svc.spec.cluster_ip
    url = f"http://{svc_ip}:{QUOTE_SERVICE_PORT}/health/readiness"
    
    try:
        response = requests.get(url, timeout=5)
        assert response.status_code == 200, f"Service returned status {response.status_code} instead of 200"
    except Exception as e:
        pytest.fail(f"Failed to access service endpoint: {e}")

def test_ac8_pods_stay_ready_for_5_minutes(running_quote_service_pod, k8s_client):
    """AC-8: Pods start successfully and remain Ready for at least 5 minutes with no restarts due to probe/resource issues"""
    _, core_v1 = k8s_client
    pod_name = running_quote_service_pod.metadata.name
    initial_restart_count = running_quote_service_pod.status.container_statuses[0].restart_count
    
    # Wait 5 minutes
    time.sleep(300)
    
    pod = core_v1.read_namespaced_pod(pod_name, QUOTE_SERVICE_NAMESPACE)
    final_restart_count = pod.status.container_statuses[0].restart_count
    
    assert final_restart_count == initial_restart_count, f"Pod restarted {final_restart_count - initial_restart_count} times during 5 minute window"
    assert all(c.ready for c in pod.status.container_statuses), "Pod is no longer in Ready state after 5 minutes"
