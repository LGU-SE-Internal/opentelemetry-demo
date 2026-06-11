import os
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Constants from spec
DEPLOYMENT_NAME = "ad-service"
SERVICE_NAME = "ad-service"
NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
GRPC_PORT = 9555
EXPECTED_RESOURCES = {
    "requests": {"cpu": "0.1", "memory": "256Mi"},
    "limits": {"cpu": "1", "memory": "1Gi"}
}
EXPECTED_PROBE_CONFIG = {
    "startup": {"initial_delay_seconds": 10, "failure_threshold": 10},
    "liveness": {"period_seconds": 5},
    "readiness": {"period_seconds": 5}
}
EXPECTED_SECURITY_CONTEXT = {
    "run_as_non_root": True,
    "run_as_user": 1000,
    "read_only_root_filesystem": True,
    "capabilities": {"drop": ["ALL"]}
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

def test_ac1_deployment_exists_with_valid_resources(k8s_client):
    """AC-1: Deployment exists with valid CPU/memory requests and limits"""
    apps_v1, _ = k8s_client
    try:
        deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    except ApiException as e:
        pytest.fail(f"Deployment {DEPLOYMENT_NAME} not found: {e}")
    
    container = deploy.spec.template.spec.containers[0]
    assert container.resources is not None, "No resources defined for container"
    
    # Check requests
    requests = container.resources.requests
    assert requests is not None, "No resource requests defined"
    assert "cpu" in requests, "No CPU request defined"
    assert "memory" in requests, "No memory request defined"
    # Convert to comparable values (simplified for test)
    cpu_req = float(requests["cpu"].replace("m", "")) if "m" in requests["cpu"] else float(requests["cpu"]) * 1000
    mem_req = int(requests["memory"].replace("Mi", "")) if "Mi" in requests["memory"] else int(requests["memory"].replace("Gi", "")) * 1024
    assert cpu_req >= 100, f"CPU request {cpu_req}m < minimum 100m"
    assert mem_req >= 256, f"Memory request {mem_req}Mi < minimum 256Mi"
    
    # Check limits
    limits = container.resources.limits
    assert limits is not None, "No resource limits defined"
    assert "cpu" in limits, "No CPU limit defined"
    assert "memory" in limits, "No memory limit defined"
    cpu_limit = float(limits["cpu"].replace("m", "")) if "m" in limits["cpu"] else float(limits["cpu"]) * 1000
    mem_limit = int(limits["memory"].replace("Mi", "")) if "Mi" in limits["memory"] else int(limits["memory"].replace("Gi", "")) * 1024
    assert cpu_limit <= 1000, f"CPU limit {cpu_limit}m > maximum 1000m"
    assert mem_limit <= 1024, f"Memory limit {mem_limit}Mi > maximum 1024Mi"

def test_ac2_deployment_has_correct_grpc_probes(k8s_client):
    """AC-2: Deployment has liveness, readiness, startup probes using gRPC on port 9555"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    
    # Check all probes exist
    assert container.liveness_probe is not None, "No liveness probe defined"
    assert container.readiness_probe is not None, "No readiness probe defined"
    assert container.startup_probe is not None, "No startup probe defined"
    
    # Check all probes use gRPC on correct port
    for probe_name, probe in [("liveness", container.liveness_probe), 
                             ("readiness", container.readiness_probe),
                             ("startup", container.startup_probe)]:
        assert probe.grpc is not None, f"{probe_name} probe does not use gRPC"
        assert probe.grpc.port == GRPC_PORT, f"{probe_name} probe uses wrong port {probe.grpc.port}, expected {GRPC_PORT}"
    
    # Check probe config
    assert container.startup_probe.initial_delay_seconds == EXPECTED_PROBE_CONFIG["startup"]["initial_delay_seconds"], \
        f"Startup probe initial delay wrong, expected {EXPECTED_PROBE_CONFIG['startup']['initial_delay_seconds']}"
    assert container.startup_probe.failure_threshold == EXPECTED_PROBE_CONFIG["startup"]["failure_threshold"], \
        f"Startup probe failure threshold wrong, expected {EXPECTED_PROBE_CONFIG['startup']['failure_threshold']}"
    assert container.liveness_probe.period_seconds == EXPECTED_PROBE_CONFIG["liveness"]["period_seconds"], \
        f"Liveness probe period wrong, expected {EXPECTED_PROBE_CONFIG['liveness']['period_seconds']}"
    assert container.readiness_probe.period_seconds == EXPECTED_PROBE_CONFIG["readiness"]["period_seconds"], \
        f"Readiness probe period wrong, expected {EXPECTED_PROBE_CONFIG['readiness']['period_seconds']}"

def test_ac3_deployment_has_secure_security_context(k8s_client):
    """AC-3: Pod security context enforces non-root, read-only root fs, dropped capabilities"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    pod_spec = deploy.spec.template.spec
    container = pod_spec.containers[0]
    
    # Check pod-level or container-level security context
    sec_ctx = container.security_context or pod_spec.security_context
    assert sec_ctx is not None, "No security context defined"
    
    assert sec_ctx.run_as_non_root == EXPECTED_SECURITY_CONTEXT["run_as_non_root"], "runAsNonRoot not set to true"
    assert sec_ctx.run_as_user == EXPECTED_SECURITY_CONTEXT["run_as_user"], f"runAsUser not set to {EXPECTED_SECURITY_CONTEXT['run_as_user']}"
    assert sec_ctx.read_only_root_filesystem == EXPECTED_SECURITY_CONTEXT["read_only_root_filesystem"], "readOnlyRootFilesystem not set to true"
    assert sec_ctx.capabilities is not None, "No capabilities defined in security context"
    assert sorted(sec_ctx.capabilities.drop) == sorted(EXPECTED_SECURITY_CONTEXT["capabilities"]["drop"]), "Capabilities not dropped as expected"

def test_ac4_deployment_exposes_all_config_as_env_vars(k8s_client):
    """AC-4: All runtime settings exposed as environment variables"""
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    
    assert container.env is not None, "No environment variables defined for container"
    # At minimum expect OTEL_* env vars common to all demo services, plus service-specific config
    otel_vars = [e for e in container.env if e.name.startswith("OTEL_")]
    assert len(otel_vars) >= 3, "Missing standard OTEL environment variables"

def test_ac5_service_exists_with_correct_config(k8s_client):
    """AC-5: Matching Service exists exposing gRPC port 9555 as ClusterIP"""
    _, core_v1 = k8s_client
    try:
        svc = core_v1.read_namespaced_service(name=SERVICE_NAME, namespace=NAMESPACE)
    except ApiException as e:
        pytest.fail(f"Service {SERVICE_NAME} not found: {e}")
    
    assert svc.spec.type == "ClusterIP", f"Service type {svc.spec.type} is not ClusterIP"
    port_found = False
    for port in svc.spec.ports:
        if port.port == GRPC_PORT and port.target_port == GRPC_PORT:
            port_found = True
            break
    assert port_found, f"Service does not expose port {GRPC_PORT} targeting container port {GRPC_PORT}"
    
    # Check selector matches deployment pod labels
    apps_v1, _ = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    deploy_labels = deploy.spec.template.metadata.labels
    selector = svc.spec.selector
    for k, v in selector.items():
        assert k in deploy_labels, f"Selector key {k} not present in deployment pod labels"
        assert deploy_labels[k] == v, f"Selector value for {k} does not match deployment label"

def test_ac6_pod_starts_successfully_with_passing_probes(k8s_client):
    """AC-6: Pod enters Running state with all probes passing within 60 seconds"""
    apps_v1, core_v1 = k8s_client
    deploy = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    selector = deploy.spec.selector.match_labels
    selector_str = ",".join([f"{k}={v}" for k, v in selector.items()])
    
    start_time = time.time()
    timeout = 60
    while time.time() - start_time < timeout:
        pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=selector_str)
        if len(pods.items) > 0:
            pod = pods.items[0]
            if pod.status.phase == "Running":
                # Check all containers are ready
                ready = all([cs.ready for cs in pod.status.container_statuses])
                if ready:
                    return
        time.sleep(2)
    pytest.fail(f"Pod did not become ready within {timeout} seconds")

def test_ac7_pod_runs_securely(k8s_client):
    """AC-7: Pod does not run as root, root filesystem is read-only"""
    _, core_v1 = k8s_client
    # Get running pod
    pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No ad-service pods found"
    pod = pods.items[0]
    
    # Exec into pod to check user
    exec_command = ["/bin/sh", "-c", "id -u"]
    resp = core_v1.connect_get_namespaced_pod_exec(
        pod.metadata.name,
        NAMESPACE,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    user_id = int(resp.strip())
    assert user_id != 0, f"Pod runs as root user (uid {user_id})"
    
    # Check root filesystem is read-only
    exec_command = ["/bin/sh", "-c", "touch /testfile 2>&1 || echo 'read-only'"]
    resp = core_v1.connect_get_namespaced_pod_exec(
        pod.metadata.name,
        NAMESPACE,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert "read-only" in resp.lower() or "permission denied" in resp.lower(), "Root filesystem is writable"
