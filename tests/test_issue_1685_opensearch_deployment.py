#!/usr/bin/env python3
import os
import pytest
import yaml
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
from kubernetes.stream import stream

# Manifest path (will exist once implementation is done)
MANIFEST_DIR = "./kubernetes/opensearch/"
MANIFEST_FILES = [
    os.path.join(MANIFEST_DIR, "pvc.yaml"),
    os.path.join(MANIFEST_DIR, "deployment.yaml"),
    os.path.join(MANIFEST_DIR, "service.yaml")
]

# Load kube config for cluster integration tests
config.load_kube_config()
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()
NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
DEPLOYMENT_NAME = "opensearch"
SERVICE_NAME = "opensearch"
PVC_NAME = "opensearch-data"

def load_manifest(filepath):
    """Load YAML manifest from file"""
    with open(filepath, "r") as f:
        return yaml.safe_load(f)

def get_opensearch_pod():
    """Get active opensearch pod"""
    pods = core_v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector="app.kubernetes.io/name=opensearch"
    )
    for pod in pods.items:
        if pod.status.phase in ["Running", "Pending"]:
            return pod
    return None

def cleanup_resources():
    """Clean up test resources"""
    # Delete deployment
    try:
        apps_v1.delete_namespaced_deployment(
            name=DEPLOYMENT_NAME,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(grace_period_seconds=0)
        )
        for _ in range(30):
            try:
                apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
                time.sleep(1)
            except ApiException as e:
                if e.status == 404:
                    break
    except ApiException:
        pass
    
    # Delete service
    try:
        core_v1.delete_namespaced_service(
            name=SERVICE_NAME,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(grace_period_seconds=0)
        )
    except ApiException:
        pass
    
    # Delete PVC
    try:
        core_v1.delete_namespaced_persistent_volume_claim(
            name=PVC_NAME,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(grace_period_seconds=0)
        )
    except ApiException:
        pass

@pytest.fixture(autouse=True)
def auto_cleanup():
    """Auto clean up after each test"""
    cleanup_resources()
    yield
    cleanup_resources()

@pytest.mark.static
def test_ac1_deployment_resource_limits_requests():
    """AC-1: Deployment has correct resource requests (1 CPU / 2Gi memory) and limits (2 CPU / 4Gi memory)"""
    deployment = load_manifest(os.path.join(MANIFEST_DIR, "deployment.yaml"))
    container_spec = deployment["spec"]["template"]["spec"]["containers"][0]
    resources = container_spec.get("resources", {})
    
    assert "requests" in resources, "Resource requests must be defined"
    assert "limits" in resources, "Resource limits must be defined"
    
    assert resources["requests"]["cpu"] == "1", f"Expected CPU request 1, got {resources['requests'].get('cpu')}"
    assert resources["requests"]["memory"] == "2Gi", f"Expected memory request 2Gi, got {resources['requests'].get('memory')}"
    
    assert resources["limits"]["cpu"] == "2", f"Expected CPU limit 2, got {resources['limits'].get('cpu')}"
    assert resources["limits"]["memory"] == "4Gi", f"Expected memory limit 4Gi, got {resources['limits'].get('memory')}"

@pytest.mark.static
def test_ac2_deployment_probe_configuration():
    """AC-2: Deployment has correct liveness and readiness probe configuration"""
    deployment = load_manifest(os.path.join(MANIFEST_DIR, "deployment.yaml"))
    container_spec = deployment["spec"]["template"]["spec"]["containers"][0]
    
    liveness_probe = container_spec.get("livenessProbe", {})
    readiness_probe = container_spec.get("readinessProbe", {})
    
    # Check liveness probe
    assert "httpGet" in liveness_probe, "Liveness probe must be httpGet type"
    assert liveness_probe["httpGet"]["port"] == 9200, "Liveness probe port must be 9200"
    assert liveness_probe["httpGet"]["path"] == "/_cluster/health", "Liveness probe path must be /_cluster/health"
    assert liveness_probe.get("initialDelaySeconds") == 60, f"Expected liveness initialDelaySeconds 60, got {liveness_probe.get('initialDelaySeconds')}"
    assert liveness_probe.get("periodSeconds") == 10, f"Expected liveness periodSeconds 10, got {liveness_probe.get('periodSeconds')}"
    
    # Check readiness probe
    assert "httpGet" in readiness_probe, "Readiness probe must be httpGet type"
    assert readiness_probe["httpGet"]["port"] == 9200, "Readiness probe port must be 9200"
    assert readiness_probe["httpGet"]["path"] == "/_cluster/health?local=true", "Readiness probe path must be /_cluster/health?local=true"
    assert readiness_probe.get("initialDelaySeconds") == 30, f"Expected readiness initialDelaySeconds 30, got {readiness_probe.get('initialDelaySeconds')}"
    assert readiness_probe.get("periodSeconds") == 5, f"Expected readiness periodSeconds 5, got {readiness_probe.get('periodSeconds')}"

@pytest.mark.static
def test_ac3_deployment_security_context():
    """AC-3: Deployment security context is configured for non-root execution without privilege escalation"""
    deployment = load_manifest(os.path.join(MANIFEST_DIR, "deployment.yaml"))
    container_spec = deployment["spec"]["template"]["spec"]["containers"][0]
    security_context = container_spec.get("securityContext", {})
    
    assert security_context.get("runAsNonRoot") == True, "runAsNonRoot must be true"
    assert security_context.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation must be false"
    assert security_context.get("runAsUser") == 1000, f"Expected runAsUser 1000, got {security_context.get('runAsUser')}"
    assert security_context.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem must be true"

@pytest.mark.static
def test_ac4_pvc_configuration_and_mount():
    """AC-4: PersistentVolumeClaim is correctly configured and mounted to opensearch data path"""
    # Check PVC manifest
    pvc = load_manifest(os.path.join(MANIFEST_DIR, "pvc.yaml"))
    assert pvc["metadata"]["name"] == PVC_NAME, f"PVC name must be {PVC_NAME}"
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"], "PVC access mode must be ReadWriteOnce"
    assert pvc["spec"]["resources"]["requests"]["storage"] in ["10Gi", "100Gi"], "PVC storage request must be at least 10Gi"
    
    # Check volume mount in deployment
    deployment = load_manifest(os.path.join(MANIFEST_DIR, "deployment.yaml"))
    volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
    volume_mounts = deployment["spec"]["template"]["spec"]["containers"][0].get("volumeMounts", [])
    
    pvc_volume = next((v for v in volumes if v["persistentVolumeClaim"]["claimName"] == PVC_NAME), None)
    assert pvc_volume is not None, f"PVC {PVC_NAME} must be referenced in deployment volumes"
    
    data_mount = next((m for m in volume_mounts if m["mountPath"] == "/usr/share/opensearch/data"), None)
    assert data_mount is not None, "Data volume must be mounted to /usr/share/opensearch/data"
    assert data_mount["name"] == pvc_volume["name"], "Data mount must reference the PVC volume"

@pytest.mark.static
def test_ac5_service_configuration():
    """AC-5: ClusterIP service correctly exposes ports 9200 and 9300 and selects opensearch pods"""
    service = load_manifest(os.path.join(MANIFEST_DIR, "service.yaml"))
    assert service["metadata"]["name"] == SERVICE_NAME, f"Service name must be {SERVICE_NAME}"
    assert service["spec"]["type"] == "ClusterIP", "Service type must be ClusterIP"
    
    ports = service["spec"]["ports"]
    http_port = next((p for p in ports if p["name"] == "http"), None)
    transport_port = next((p for p in ports if p["name"] == "transport"), None)
    
    assert http_port is not None, "Service must have http port named 'http'"
    assert http_port["port"] == 9200, "HTTP service port must be 9200"
    assert http_port["targetPort"] == 9200, "HTTP target port must be 9200"
    
    assert transport_port is not None, "Service must have transport port named 'transport'"
    assert transport_port["port"] == 9300, "Transport service port must be 9300"
    assert transport_port["targetPort"] == 9300, "Transport target port must be 9300"
    
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "opensearch", "Service selector must match opensearch pods"

@pytest.mark.static
def test_ac6_hardening_patterns_match_postgresql():
    """AC-6: Manifest follows same hardening patterns as PostgreSQL manifest"""
    deployment = load_manifest(os.path.join(MANIFEST_DIR, "deployment.yaml"))
    container_spec = deployment["spec"]["template"]["spec"]["containers"][0]
    security_context = container_spec.get("securityContext", {})
    
    # Check no privileged mode
    assert security_context.get("privileged") in [None, False], "Privileged mode must not be enabled"
    # Check standard labels
    template_labels = deployment["spec"]["template"]["metadata"].get("labels", {})
    assert "app.kubernetes.io/name" in template_labels, "Standard app.kubernetes.io/name label must be present"
    assert "app.kubernetes.io/instance" in template_labels, "Standard app.kubernetes.io/instance label must be present"
    assert "app.kubernetes.io/version" in template_labels, "Standard app.kubernetes.io/version label must be present"

@pytest.mark.integration
def test_ac7_service_reachability_and_response():
    """AC-7: Opensearch pod is reachable via service on port 9200 and returns valid 200 OK response"""
    # Apply all manifests
    for f in MANIFEST_FILES:
        manifest = load_manifest(f)
        if manifest["kind"] == "PersistentVolumeClaim":
            core_v1.create_namespaced_persistent_volume_claim(namespace=NAMESPACE, body=manifest)
        elif manifest["kind"] == "Deployment":
            apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=manifest)
        elif manifest["kind"] == "Service":
            core_v1.create_namespaced_service(namespace=NAMESPACE, body=manifest)
    
    # Wait for pod to be ready
    pod_ready = False
    for _ in range(180):
        pod = get_opensearch_pod()
        if pod and pod.status.phase == "Running":
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    pod_ready = True
                    break
        if pod_ready:
            break
        time.sleep(1)
    
    assert pod_ready == True, "Pod never became ready"
    
    # Test connectivity via service (run a curl from a test pod)
    exec_pod = core_v1.create_namespaced_pod(
        namespace=NAMESPACE,
        body={
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "test-connectivity"},
            "spec": {
                "containers": [
                    {
                        "name": "curl",
                        "image": "curlimages/curl:latest",
                        "command": ["sleep", "3600"]
                    }
                ],
                "restartPolicy": "Never"
            }
        }
    )
    
    # Wait for test pod to be ready
    time.sleep(10)
    
    # Execute curl command to opensearch service
    try:
        resp = stream(
            core_v1.connect_get_namespaced_pod_exec,
            "test-connectivity",
            NAMESPACE,
            command=["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://{SERVICE_NAME}:9200/"],
            stderr=True, stdin=False, stdout=True, tty=False
        )
        status_code = resp.strip()
        assert status_code == "200", f"Expected 200 OK response, got {status_code}"
    finally:
        # Clean up test pod
        core_v1.delete_namespaced_pod(name="test-connectivity", namespace=NAMESPACE, body=client.V1DeleteOptions(grace_period_seconds=0))

@pytest.mark.integration
def test_ac8_data_persistence_across_pod_restart():
    """AC-8: Data is preserved across pod deletion and recreation"""
    # Apply all manifests
    for f in MANIFEST_FILES:
        manifest = load_manifest(f)
        if manifest["kind"] == "PersistentVolumeClaim":
            core_v1.create_namespaced_persistent_volume_claim(namespace=NAMESPACE, body=manifest)
        elif manifest["kind"] == "Deployment":
            apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=manifest)
        elif manifest["kind"] == "Service":
            core_v1.create_namespaced_service(namespace=NAMESPACE, body=manifest)
    
    # Wait for pod to be ready
    for _ in range(180):
        pod = get_opensearch_pod()
        if pod and pod.status.phase == "Running":
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    break
        time.sleep(1)
    
    # Insert test data
    exec_cmd = [
        "curl", "-X", "PUT", "http://localhost:9200/test-index/_doc/1",
        "-H", "Content-Type: application/json",
        "-d", '{"test_key": "test_value"}'
    ]
    
    pod = get_opensearch_pod()
    stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=exec_cmd,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    time.sleep(2)
    
    # Delete the pod to trigger recreation
    core_v1.delete_namespaced_pod(name=pod.metadata.name, namespace=NAMESPACE)
    
    # Wait for new pod to be ready
    new_pod_ready = False
    new_pod = None
    for _ in range(180):
        new_pod = get_opensearch_pod()
        if new_pod and new_pod.metadata.uid != pod.metadata.uid and new_pod.status.phase == "Running":
            for cond in new_pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    new_pod_ready = True
                    break
        if new_pod_ready:
            break
        time.sleep(1)
    
    assert new_pod_ready == True, "New pod never became ready after deletion"
    
    # Verify test data still exists
    get_cmd = ["curl", "-s", "http://localhost:9200/test-index/_doc/1"]
    resp = stream(
        core_v1.connect_get_namespaced_pod_exec,
        new_pod.metadata.name,
        NAMESPACE,
        command=get_cmd,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    
    assert '"test_key":"test_value"' in resp, "Test data not found after pod restart, persistence failed"
