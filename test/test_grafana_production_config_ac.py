#!/usr/bin/env python3
"""
Integration tests for Grafana production configuration ACs (issue #1226)
Tests are designed to fail before implementation is applied.
"""
import time
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import pytest

# Constants from spec
NAMESPACE = "opentelemetry-demo"
DEPLOYMENT_NAME = "grafana"
PVC_NAME = "grafana-storage"
CONTAINER_NAME = "grafana"
GRAFANA_PORT = 3000
HEALTH_ENDPOINT = "/api/health"
EXPECTED_UID = 472
EXPECTED_GID = 472
EXPECTED_CPU_REQUEST = "100m"
EXPECTED_CPU_LIMIT = "500m"
EXPECTED_MEM_REQUEST = "256Mi"
EXPECTED_MEM_LIMIT = "512Mi"
PVC_STORAGE_REQUEST = "10Gi"
PVC_ACCESS_MODES = ["ReadWriteOnce"]

@pytest.fixture(scope="module")
def k8s_client():
    """Initialize Kubernetes client"""
    try:
        config.load_incluster_config()
    except:
        config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    return apps_v1, core_v1

def get_running_grafana_pod(core_v1):
    """Get a running Grafana pod instance"""
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app.kubernetes.io/name={DEPLOYMENT_NAME}")
    running_pods = [p for p in pods.items if p.status.phase == "Running"]
    assert len(running_pods) > 0, "No running Grafana pods found"
    return running_pods[0]

def test_ac1_liveness_readiness_probes_configured(k8s_client):
    """AC-1: Liveness and readiness probes are correctly configured on deployment"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == CONTAINER_NAME)
    
    # Verify liveness probe exists and is configured correctly
    assert container.liveness_probe is not None, "Liveness probe missing"
    assert container.liveness_probe.http_get.path == HEALTH_ENDPOINT
    assert container.liveness_probe.http_get.port == GRAFANA_PORT
    assert container.liveness_probe.period_seconds == 10, f"Expected liveness interval 10s, got {container.liveness_probe.period_seconds}s"
    assert container.liveness_probe.timeout_seconds == 5, f"Expected liveness timeout 5s, got {container.liveness_probe.timeout_seconds}s"
    assert container.liveness_probe.failure_threshold == 3, f"Expected liveness failure threshold 3, got {container.liveness_probe.failure_threshold}"

    # Verify readiness probe exists and is configured correctly
    assert container.readiness_probe is not None, "Readiness probe missing"
    assert container.readiness_probe.http_get.path == HEALTH_ENDPOINT
    assert container.readiness_probe.http_get.port == GRAFANA_PORT

def test_ac1_health_endpoint_returns_200(k8s_client):
    """AC-1: /api/health endpoint returns 200 OK on running pod"""
    _, core_v1 = k8s_client
    pod = get_running_grafana_pod(core_v1)
    pod_ip = pod.status.pod_ip
    
    resp = requests.get(f"http://{pod_ip}:{GRAFANA_PORT}{HEALTH_ENDPOINT}", timeout=10)
    assert resp.status_code == 200, f"Expected 200 OK from health endpoint, got {resp.status_code}"

def test_ac2_pvc_created(k8s_client):
    """AC-2: PersistentVolumeClaim grafana-storage is created with correct spec"""
    _, core_v1 = k8s_client
    try:
        pvc = core_v1.read_namespaced_persistent_volume_claim(PVC_NAME, NAMESPACE)
    except ApiException as e:
        assert False, f"PVC {PVC_NAME} not found: {e}"
    
    assert PVC_ACCESS_MODES == pvc.spec.access_modes, f"Expected access modes {PVC_ACCESS_MODES}, got {pvc.spec.access_modes}"
    assert pvc.spec.resources.requests["storage"] == PVC_STORAGE_REQUEST, f"Expected storage request {PVC_STORAGE_REQUEST}, got {pvc.spec.resources.requests['storage']}"

def test_ac2_volume_mount_configured(k8s_client):
    """AC-2: Grafana deployment has volume mount for /var/lib/grafana"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == CONTAINER_NAME)
    
    volume_mount = next((vm for vm in container.volume_mounts if vm.mount_path == "/var/lib/grafana"), None)
    assert volume_mount is not None, "Volume mount for /var/lib/grafana missing"
    assert volume_mount.name == "grafana-storage"

    # Verify volume entry exists referencing PVC
    volume = next((v for v in deploy.spec.template.spec.volumes if v.name == "grafana-storage"), None)
    assert volume is not None, "Volume entry for grafana-storage missing"
    assert volume.persistent_volume_claim.claim_name == PVC_NAME, f"Volume references wrong PVC, expected {PVC_NAME}"

def test_ac2_data_persists_after_pod_restart(k8s_client):
    """AC-2: Custom dashboard saved before restart is present after pod deletion/recreation"""
    # This test will fail until persistence is implemented
    _, core_v1 = k8s_client
    # Get initial pod
    initial_pod = get_running_grafana_pod(core_v1)
    pod_ip = initial_pod.status.pod_ip
    
    # 1. Create test dashboard (simplified - assumes API access, adjust auth if needed)
    dashboard_payload = {
        "dashboard": {
            "id": None,
            "title": "Test Persistence Dashboard",
            "panels": [],
            "version": 1
        },
        "overwrite": False
    }
    
    # Create dashboard (default creds are admin/admin for demo)
    create_resp = requests.post(
        f"http://{pod_ip}:{GRAFANA_PORT}/api/dashboards/db",
        json=dashboard_payload,
        auth=("admin", "admin"),
        timeout=30
    )
    assert create_resp.status_code == 200, f"Failed to create test dashboard: {create_resp.text}"
    dashboard_uid = create_resp.json()["uid"]

    # 2. Delete the pod to trigger restart
    core_v1.delete_namespaced_pod(initial_pod.metadata.name, NAMESPACE)
    
    # 3. Wait for new pod to be running
    time.sleep(30)
    retries = 10
    new_pod = None
    for _ in range(retries):
        try:
            new_pod = get_running_grafana_pod(core_v1)
            if new_pod.metadata.name != initial_pod.metadata.name:
                break
        except AssertionError:
            pass
        time.sleep(10)
    assert new_pod is not None, "New Grafana pod did not start after deletion"
    assert new_pod.metadata.name != initial_pod.metadata.name, "Pod was not recreated"

    # 4. Verify test dashboard exists in new pod
    get_resp = requests.get(
        f"http://{new_pod.status.pod_ip}:{GRAFANA_PORT}/api/dashboards/uid/{dashboard_uid}",
        auth=("admin", "admin"),
        timeout=30
    )
    assert get_resp.status_code == 200, f"Test dashboard not found after restart, got status {get_resp.status_code}"
    assert get_resp.json()["dashboard"]["title"] == "Test Persistence Dashboard"

def test_ac3_resource_limits_configured(k8s_client):
    """AC-3: Resource requests and limits are correctly configured"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == CONTAINER_NAME)
    
    assert container.resources is not None, "Resources field missing"
    
    # Check requests
    assert container.resources.requests["cpu"] == EXPECTED_CPU_REQUEST, f"Expected CPU request {EXPECTED_CPU_REQUEST}, got {container.resources.requests.get('cpu')}"
    assert container.resources.requests["memory"] == EXPECTED_MEM_REQUEST, f"Expected memory request {EXPECTED_MEM_REQUEST}, got {container.resources.requests.get('memory')}"
    
    # Check limits
    assert container.resources.limits["cpu"] == EXPECTED_CPU_LIMIT, f"Expected CPU limit {EXPECTED_CPU_LIMIT}, got {container.resources.limits.get('cpu')}"
    assert container.resources.limits["memory"] == EXPECTED_MEM_LIMIT, f"Expected memory limit {EXPECTED_MEM_LIMIT}, got {container.resources.limits.get('memory')}"

def test_ac4_security_context_non_root(k8s_client):
    """AC-4: Grafana runs as non-root user 472 with no privilege escalation"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == CONTAINER_NAME)
    
    sc = container.security_context
    assert sc is not None, "Security context missing"
    
    assert sc.run_as_user == EXPECTED_UID, f"Expected runAsUser {EXPECTED_UID}, got {sc.run_as_user}"
    assert sc.run_as_group == EXPECTED_GID, f"Expected runAsGroup {EXPECTED_GID}, got {sc.run_as_group}"
    assert sc.run_as_non_root == True, "runAsNonRoot must be true"
    assert sc.allow_privilege_escalation == False, "allowPrivilegeEscalation must be false"
    assert sc.read_only_root_filesystem == True, "readOnlyRootFilesystem must be true"

def test_ac4_running_user_is_non_root(k8s_client):
    """AC-4: Running container executes as user 472 (non-root)"""
    _, core_v1 = k8s_client
    pod = get_running_grafana_pod(core_v1)
    
    # Execute id command in container
    resp = core_v1.connect_get_namespaced_pod_exec(
        pod.metadata.name,
        NAMESPACE,
        command=["id", "-u"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    uid = int(resp.strip())
    assert uid == EXPECTED_UID, f"Expected running UID {EXPECTED_UID}, got {uid}"

def test_ac5_preprovisioned_assets_load(k8s_client):
    """AC-5: Pre-provisioned dashboards and datasources load without errors"""
    _, core_v1 = k8s_client
    pod = get_running_grafana_pod(core_v1)
    
    # Check logs for provisioning errors
    logs = core_v1.read_namespaced_pod_log(pod.metadata.name, NAMESPACE, container=CONTAINER_NAME)
    
    # Check for no provisioning errors
    assert "provisioning failed" not in logs.lower(), "Provisioning errors found in Grafana logs"
    assert "error loading dashboard" not in logs.lower(), "Dashboard loading errors found in Grafana logs"
    assert "datasource provisioning error" not in logs.lower(), "Datasource provisioning errors found in Grafana logs"

def test_ac5_preprovisioned_dashboards_accessible(k8s_client):
    """AC-5: Pre-provisioned dashboards are accessible in UI"""
    _, core_v1 = k8s_client
    pod = get_running_grafana_pod(core_v1)
    pod_ip = pod.status.pod_ip
    
    # Get list of dashboards, verify at least one pre-provisioned dashboard exists (e.g. OpenTelemetry Demo dashboard)
    resp = requests.get(
        f"http://{pod_ip}:{GRAFANA_PORT}/api/search?query=OpenTelemetry Demo",
        auth=("admin", "admin"),
        timeout=30
    )
    assert resp.status_code == 200, f"Failed to list dashboards: {resp.text}"
    dashboards = resp.json()
    assert len(dashboards) >= 1, "No pre-provisioned OpenTelemetry Demo dashboards found"
