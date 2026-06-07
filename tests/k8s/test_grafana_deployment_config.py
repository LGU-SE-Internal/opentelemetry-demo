import pytest
from kubernetes import client, config
import requests
import time
import os

# Load kube config
config.load_kube_config()
k8s_apps_v1 = client.AppsV1Api()
k8s_core_v1 = client.CoreV1Api()

NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
DEPLOYMENT_NAME = "opentelemetry-demo-grafana"
GRAFANA_SERVICE_NAME = "opentelemetry-demo-grafana"
GRAFANA_PORT = 3000

def get_grafana_deployment():
    return k8s_apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)

def get_running_grafana_pods():
    pods = k8s_core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app.kubernetes.io/name={DEPLOYMENT_NAME},app.kubernetes.io/component=grafana")
    return [pod for pod in pods.items if pod.status.phase == "Running"]

def test_ac1_liveness_probe_configured_correctly():
    """Verify liveness probe is configured per spec: /api/health endpoint, port 3000, initial delay 30s, period 10s, failure threshold 3"""
    deployment = get_grafana_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.http_get is not None, "Liveness probe not using HTTP GET"
    assert container.liveness_probe.http_get.path == "/api/health", f"Expected liveness probe path /api/health, got {container.liveness_probe.http_get.path}"
    assert container.liveness_probe.http_get.port == GRAFANA_PORT, f"Expected liveness probe port {GRAFANA_PORT}, got {container.liveness_probe.http_get.port}"
    assert container.liveness_probe.initial_delay_seconds == 30, f"Expected liveness initial delay 30s, got {container.liveness_probe.initial_delay_seconds}"
    assert container.liveness_probe.period_seconds == 10, f"Expected liveness period 10s, got {container.liveness_probe.period_seconds}"
    assert container.liveness_probe.failure_threshold == 3, f"Expected liveness failure threshold 3, got {container.liveness_probe.failure_threshold}"

def test_ac2_readiness_probe_configured_correctly():
    """Verify readiness probe is configured per spec: /api/health endpoint, port 3000, initial delay 5s, period 5s, failure threshold 3"""
    deployment = get_grafana_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.http_get is not None, "Readiness probe not using HTTP GET"
    assert container.readiness_probe.http_get.path == "/api/health", f"Expected readiness probe path /api/health, got {container.readiness_probe.http_get.path}"
    assert container.readiness_probe.http_get.port == GRAFANA_PORT, f"Expected readiness probe port {GRAFANA_PORT}, got {container.readiness_probe.http_get.port}"
    assert container.readiness_probe.initial_delay_seconds == 5, f"Expected readiness initial delay 5s, got {container.readiness_probe.initial_delay_seconds}"
    assert container.readiness_probe.period_seconds == 5, f"Expected readiness period 5s, got {container.readiness_probe.period_seconds}"
    assert container.readiness_probe.failure_threshold == 3, f"Expected readiness failure threshold 3, got {container.readiness_probe.failure_threshold}"

def test_ac3_resource_constraints_configured_correctly():
    """Verify resource requests and limits are set per spec: requests CPU 100m, memory 256Mi; limits CPU 500m, memory 512Mi"""
    deployment = get_grafana_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    assert container.resources is not None, "Resource constraints not configured"
    assert container.resources.requests is not None, "Resource requests not configured"
    assert container.resources.limits is not None, "Resource limits not configured"
    
    assert container.resources.requests.get("cpu") == "100m", f"Expected CPU request 100m, got {container.resources.requests.get('cpu')}"
    assert container.resources.requests.get("memory") == "256Mi", f"Expected memory request 256Mi, got {container.resources.requests.get('memory')}"
    assert container.resources.limits.get("cpu") == "500m", f"Expected CPU limit 500m, got {container.resources.limits.get('cpu')}"
    assert container.resources.limits.get("memory") == "512Mi", f"Expected memory limit 512Mi, got {container.resources.limits.get('memory')}"

def test_ac4_security_context_configured_correctly():
    """Verify security context is set to run as non-root unprivileged user with no privilege escalation"""
    deployment = get_grafana_deployment()
    pod_security_context = deployment.spec.template.spec.security_context
    container = deployment.spec.template.spec.containers[0]
    container_security_context = container.security_context
    
    # Check pod-level security context
    assert pod_security_context is not None, "Pod security context not configured"
    assert pod_security_context.run_as_non_root is True, "Pod security context not set to runAsNonRoot: true"
    assert pod_security_context.run_as_user == 472, f"Expected runAsUser 472, got {pod_security_context.run_as_user}"
    
    # Check container-level security context
    assert container_security_context is not None, "Container security context not configured"
    assert container_security_context.allow_privilege_escalation is False, "Container security context not set to allowPrivilegeEscalation: false"
    assert container_security_context.read_only_root_filesystem is True, "Container security context not set to readOnlyRootFilesystem: true"
    assert container_security_context.privileged is False, "Container security context not set to privileged: false"

def test_ac5_all_parameters_configurable_via_env_vars():
    """Verify all configurable parameters are exposed as environment variables with correct defaults"""
    deployment = get_grafana_deployment()
    container = deployment.spec.template.spec.containers[0]
    env_vars = {env.name: env.value for env in container.env}
    
    expected_env_vars = {
        "GRAFANA_LIVENESS_PROBE_INITIAL_DELAY_SECONDS": "30",
        "GRAFANA_LIVENESS_PROBE_PERIOD_SECONDS": "10",
        "GRAFANA_READINESS_PROBE_INITIAL_DELAY_SECONDS": "5",
        "GRAFANA_READINESS_PROBE_PERIOD_SECONDS": "5",
        "GRAFANA_RESOURCE_REQUEST_CPU": "100m",
        "GRAFANA_RESOURCE_REQUEST_MEMORY": "256Mi",
        "GRAFANA_RESOURCE_LIMIT_CPU": "500m",
        "GRAFANA_RESOURCE_LIMIT_MEMORY": "512Mi",
        "GRAFANA_RUN_USER_UID": "472"
    }
    
    for var_name, expected_default in expected_env_vars.items():
        assert var_name in env_vars, f"Environment variable {var_name} not found in container config"
        # Check variable is templated (uses {{ .Values... }} or equivalent in helm, not hardcoded)
        assert "{{" in env_vars[var_name] or "$(" in env_vars[var_name], f"Environment variable {var_name} is hardcoded, should be configurable via values/helm params"

def test_ac6_grafana_provisioning_unchanged():
    """Verify existing Grafana provisioning works correctly: dashboards present, data source connected"""
    pods = get_running_grafana_pods()
    assert len(pods) >= 1, "No running Grafana pods found"
    pod = pods[0]
    
    # Port forward to Grafana pod
    from kubernetes.stream import portforward
    pf = portforward.portforward(
        k8s_core_v1.api_client,
        f"/api/v1/namespaces/{NAMESPACE}/pods/{pod.metadata.name}/portforward",
        ports=[GRAFANA_PORT],
    )
    
    local_port = pf.forward(GRAFANA_PORT)
    base_url = f"http://localhost:{local_port}"
    
    # Test dashboards endpoint
    resp = requests.get(f"{base_url}/api/search", auth=("admin", "admin"))
    assert resp.status_code == 200, f"Failed to get dashboards: {resp.text}"
    dashboards = resp.json()
    opentelemetry_dashboards = [d for d in dashboards if "OpenTelemetry" in d.get("title", "")]
    assert len(opentelemetry_dashboards) > 0, "No OpenTelemetry demo dashboards found"
    
    # Test data source endpoint
    resp = requests.get(f"{base_url}/api/datasources", auth=("admin", "admin"))
    assert resp.status_code == 200, f"Failed to get datasources: {resp.text}"
    datasources = resp.json()
    otel_datasource = [ds for ds in datasources if "OpenTelemetry" in ds.get("name", "") or "Prometheus" in ds.get("name", "")]
    assert len(otel_datasource) > 0, "OpenTelemetry data source not found"
    
    # Test querying data source
    ds_id = otel_datasource[0]["id"]
    resp = requests.post(
        f"{base_url}/api/ds/query",
        auth=("admin", "admin"),
        json={"queries": [{"refId": "A", "datasourceId": ds_id, "expr": "up", "instant": True}]}
    )
    assert resp.status_code == 200, f"Failed to query data source: {resp.text}"
    assert "error" not in resp.json(), f"Data source query returned error: {resp.text}"

def test_ac7_pod_starts_and_probes_pass_consistently():
    """Verify Grafana pod starts successfully, probes pass for 5 consecutive minutes, no unexpected restarts"""
    deployment = get_grafana_deployment()
    # Wait for deployment to be ready
    time.sleep(60)
    deploy_ready = False
    for _ in range(10):
        deploy = get_grafana_deployment()
        if deploy.status.ready_replicas == deploy.spec.replicas:
            deploy_ready = True
            break
        time.sleep(30)
    assert deploy_ready, "Grafana deployment did not become ready within 5 minutes"
    
    pods = get_running_grafana_pods()
    assert len(pods) >= 1, "No running Grafana pods found"
    
    # Check restart count for 5 minutes
    start_time = time.time()
    while time.time() - start_time < 300:
        for pod in pods:
            updated_pod = k8s_core_v1.read_namespaced_pod(pod.metadata.name, NAMESPACE)
            restart_count = sum([cs.restart_count for cs in updated_pod.status.container_statuses])
            assert restart_count == 0, f"Grafana pod {pod.metadata.name} restarted unexpectedly (restart count: {restart_count})"
            # Check pod conditions are good
            ready_condition = [c for c in updated_pod.status.conditions if c.type == "Ready"][0]
            assert ready_condition.status == "True", f"Grafana pod {pod.metadata.name} is not ready"
        time.sleep(30)
