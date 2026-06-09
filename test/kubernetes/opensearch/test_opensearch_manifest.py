import pytest
from kubernetes import client, config
import time
import requests

# Load kube config
config.load_kube_config()
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()

NAMESPACE = "default"
DEPLOYMENT_NAME = "opensearch"
PVC_NAME = "opensearch-data"
SERVICE_NAME = "opensearch"

@pytest.mark.ac1
def test_ac1_deployment_resource_limits_requests():
    """AC-1: Deployment has correct resource requests and limits"""
    deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    
    # Check requests
    assert container.resources.requests["cpu"] == "1"
    assert container.resources.requests["memory"] == "2Gi"
    
    # Check limits
    assert container.resources.limits["cpu"] == "2"
    assert container.resources.limits["memory"] == "4Gi"

@pytest.mark.ac2
def test_ac2_deployment_health_probes():
    """AC-2: Deployment has correct liveness and readiness probes"""
    deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deployment.spec.template.spec.containers[0]
    
    # Check liveness probe
    liveness = container.liveness_probe
    assert liveness.http_get.port == 9200
    assert liveness.http_get.path == "/_cluster/health"
    assert liveness.initial_delay_seconds == 60
    assert liveness.period_seconds == 10
    
    # Check readiness probe
    readiness = container.readiness_probe
    assert readiness.http_get.port == 9200
    assert readiness.http_get.path == "/_cluster/health?local=true"
    assert readiness.initial_delay_seconds == 30
    assert readiness.period_seconds == 5

@pytest.mark.ac3
def test_ac3_deployment_security_context():
    """AC-3: Deployment has correct non-root security context"""
    deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    sc = deployment.spec.template.spec.security_context
    container_sc = deployment.spec.template.spec.containers[0].security_context
    
    assert sc.run_as_non_root == True
    assert sc.run_as_user == 1000
    assert container_sc.allow_privilege_escalation == False
    assert container_sc.read_only_root_filesystem == True

@pytest.mark.ac4
def test_ac4_pvc_exists_and_mounted():
    """AC-4: PVC exists and is mounted to correct path"""
    # Check PVC exists
    pvc = core_v1.read_namespaced_persistent_volume_claim(PVC_NAME, NAMESPACE)
    assert pvc.spec.access_modes == ["ReadWriteOnce"]
    assert pvc.spec.resources.requests["storage"] >= "10Gi"
    
    # Check PVC is mounted in deployment
    deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    volumes = deployment.spec.template.spec.volumes
    pvc_volume = next(v for v in volumes if v.persistent_volume_claim and v.persistent_volume_claim.claim_name == PVC_NAME)
    assert pvc_volume is not None
    
    # Check mount path
    volume_mounts = deployment.spec.template.spec.containers[0].volume_mounts
    data_mount = next(vm for vm in volume_mounts if vm.name == pvc_volume.name)
    assert data_mount.mount_path == "/usr/share/opensearch/data"

@pytest.mark.ac5
def test_ac5_service_configuration():
    """AC-5: Service exists with correct ports and selector"""
    service = core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    assert service.spec.type == "ClusterIP"
    assert service.spec.selector["app.kubernetes.io/name"] == "opensearch"
    
    # Check ports
    ports = {p.name: p for p in service.spec.ports}
    assert "http" in ports
    assert ports["http"].port == 9200
    assert ports["http"].target_port == 9200
    
    assert "transport" in ports
    assert ports["transport"].port == 9300
    assert ports["transport"].target_port == 9300

@pytest.mark.ac6
def test_ac6_follows_postgresql_hardening_patterns():
    """AC-6: Follows same hardening patterns as PostgreSQL manifest"""
    postgres_deployment = apps_v1.read_namespaced_deployment("postgresql", NAMESPACE)
    opensearch_deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    
    # Check no privileged mode
    assert opensearch_deployment.spec.template.spec.containers[0].security_context.privileged is None or opensearch_deployment.spec.template.spec.containers[0].security_context.privileged == False
    
    # Check non-root user same pattern as postgres
    assert opensearch_deployment.spec.template.spec.security_context.run_as_non_root == postgres_deployment.spec.template.spec.security_context.run_as_non_root
    
    # Check persistent storage exists for state data
    opensearch_volumes = opensearch_deployment.spec.template.spec.volumes
    assert any(v.persistent_volume_claim is not None for v in opensearch_volumes)
    
    # Check health checks exist (same as postgres has probes)
    assert opensearch_deployment.spec.template.spec.containers[0].liveness_probe is not None
    assert opensearch_deployment.spec.template.spec.containers[0].readiness_probe is not None
    
    # Check standard labels
    assert "app.kubernetes.io/name" in opensearch_deployment.metadata.labels
    assert "app.kubernetes.io/name" in opensearch_deployment.spec.template.metadata.labels

@pytest.mark.ac7
def test_ac7_service_reachable():
    """AC-7: Opensearch is reachable via service on port 9200"""
    # Wait for pod to be ready
    time.sleep(120)  # Give enough time for opensearch to start
    service = core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    cluster_ip = service.spec.cluster_ip
    resp = requests.get(f"http://{cluster_ip}:9200", timeout=10)
    assert resp.status_code == 200
    assert "version" in resp.json()

@pytest.mark.ac8
def test_ac8_data_persists_across_restart():
    """AC-8: Data persists across pod restart"""
    # First write test data
    service = core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    cluster_ip = service.spec.cluster_ip
    
    # Create test index
    resp = requests.put(f"http://{cluster_ip}:9200/test_index", timeout=10)
    assert resp.status_code in [200, 201]
    
    # Insert test document
    doc = {"test_key": "test_value"}
    resp = requests.post(f"http://{cluster_ip}:9200/test_index/_doc/1", json=doc, timeout=10)
    assert resp.status_code in [200, 201]
    time.sleep(5)
    
    # Delete pod to trigger restart
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app.kubernetes.io/name={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0
    pod_name = pods.items[0].metadata.name
    core_v1.delete_namespaced_pod(pod_name, NAMESPACE)
    
    # Wait for new pod to be ready
    time.sleep(120)
    
    # Verify test data still exists
    resp = requests.get(f"http://{cluster_ip}:9200/test_index/_doc/1", timeout=10)
    assert resp.status_code == 200
    assert resp.json()["_source"]["test_key"] == "test_value"
