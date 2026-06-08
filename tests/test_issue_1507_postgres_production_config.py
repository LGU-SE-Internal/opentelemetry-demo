#!/usr/bin/env python3
import os
import pytest
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
from kubernetes.stream import stream

# Load manifest for static checks
MANIFEST_PATH = "./k8s/postgresql-deployment.yaml"
with open(MANIFEST_PATH, "r") as f:
    postgres_deployment_manifest = yaml.safe_load(f)

# Load kube config for cluster integration tests
config.load_kube_config()
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()
NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
DEPLOYMENT_NAME = "postgresql"

# Helper functions
def get_postgres_pod():
    """Get active postgres pod"""
    pods = core_v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector="app=postgresql,app.kubernetes.io/name=postgresql"
    )
    for pod in pods.items:
        if pod.status.phase in ["Running", "Pending"]:
            return pod
    return None

def cleanup_deployment():
    """Clean up test deployment"""
    try:
        apps_v1.delete_namespaced_deployment(
            name=DEPLOYMENT_NAME,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(grace_period_seconds=0)
        )
        # Wait for deletion
        for _ in range(30):
            try:
                apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
                time.sleep(1)
            except ApiException as e:
                if e.status == 404:
                    break
    except ApiException:
        pass

@pytest.fixture(autouse=True)
def auto_cleanup():
    """Auto clean up after each test"""
    cleanup_deployment()
    yield
    cleanup_deployment()

@pytest.mark.static
def test_ac1_default_liveness_probe_values():
    """AC-1: Default liveness probe config matches spec (command and default values)"""
    container_spec = postgres_deployment_manifest["spec"]["template"]["spec"]["containers"][0]
    liveness_probe = container_spec.get("livenessProbe", {})
    
    # Verify probe type and command
    assert "exec" in liveness_probe, "Liveness probe must be exec type"
    assert liveness_probe["exec"]["command"] == ["pg_isready", "-U", "postgres", "-h", "localhost"], "Liveness probe command incorrect"
    
    # Verify default values
    assert liveness_probe.get("initialDelaySeconds") == 30, f"Default liveness initialDelaySeconds should be 30, got {liveness_probe.get('initialDelaySeconds')}"
    assert liveness_probe.get("periodSeconds") == 10, f"Default liveness periodSeconds should be 10, got {liveness_probe.get('periodSeconds')}"
    assert liveness_probe.get("timeoutSeconds") == 5, f"Default liveness timeoutSeconds should be 5, got {liveness_probe.get('timeoutSeconds')}"
    assert liveness_probe.get("failureThreshold") == 3, f"Default liveness failureThreshold should be 3, got {liveness_probe.get('failureThreshold')}"

@pytest.mark.static
def test_ac2_default_readiness_probe_values():
    """AC-2: Default readiness probe config matches spec (command and default values)"""
    container_spec = postgres_deployment_manifest["spec"]["template"]["spec"]["containers"][0]
    readiness_probe = container_spec.get("readinessProbe", {})
    
    # Verify probe type and command
    assert "exec" in readiness_probe, "Readiness probe must be exec type"
    assert readiness_probe["exec"]["command"] == ["pg_isready", "-U", "postgres", "-h", "localhost"], "Readiness probe command incorrect"
    
    # Verify default values
    assert readiness_probe.get("initialDelaySeconds") == 5, f"Default readiness initialDelaySeconds should be 5, got {readiness_probe.get('initialDelaySeconds')}"
    assert readiness_probe.get("periodSeconds") == 5, f"Default readiness periodSeconds should be 5, got {readiness_probe.get('periodSeconds')}"
    assert readiness_probe.get("timeoutSeconds") == 3, f"Default readiness timeoutSeconds should be 3, got {readiness_probe.get('timeoutSeconds')}"
    assert readiness_probe.get("failureThreshold") == 3, f"Default readiness failureThreshold should be 3, got {readiness_probe.get('failureThreshold')}"

@pytest.mark.envoverride
def test_ac3_liveness_initial_delay_env_override():
    """AC-3: Liveness probe initialDelaySeconds uses value from POSTGRES_LIVENESS_PROBE_INITIAL_DELAY_SECONDS env var"""
    # Create deployment with env override
    modified_deploy = postgres_deployment_manifest.copy()
    container = modified_deploy["spec"]["template"]["spec"]["containers"][0]
    container["env"] = container.get("env", []) + [
        {"name": "POSTGRES_LIVENESS_PROBE_INITIAL_DELAY_SECONDS", "value": "60"}
    ]
    
    apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=modified_deploy)
    
    # Wait for deployment to be created
    time.sleep(5)
    deployed = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    deployed_liveness = deployed.spec.template.spec.containers[0].liveness_probe
    
    assert deployed_liveness.initial_delay_seconds == 60, f"Expected initialDelaySeconds 60, got {deployed_liveness.initial_delay_seconds}"

@pytest.mark.envoverride
def test_ac4_resource_limits_env_override():
    """AC-4: CPU request and memory limit use values from POSTGRES_RESOURCES_REQUESTS_CPU and POSTGRES_RESOURCES_LIMITS_MEMORY env vars"""
    modified_deploy = postgres_deployment_manifest.copy()
    container = modified_deploy["spec"]["template"]["spec"]["containers"][0]
    container["env"] = container.get("env", []) + [
        {"name": "POSTGRES_RESOURCES_REQUESTS_CPU", "value": "200m"},
        {"name": "POSTGRES_RESOURCES_LIMITS_MEMORY", "value": "2Gi"}
    ]
    
    apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=modified_deploy)
    
    time.sleep(5)
    deployed = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    resources = deployed.spec.template.spec.containers[0].resources
    
    assert resources.requests["cpu"] == "200m", f"Expected CPU request 200m, got {resources.requests.get('cpu')}"
    assert resources.limits["memory"] == "2Gi", f"Expected memory limit 2Gi, got {resources.limits.get('memory')}"

@pytest.mark.static
def test_ac5_default_security_context():
    """AC-5: Default security context has runAsNonRoot=true, allowPrivilegeEscalation=false, runAsUser=999"""
    container_spec = postgres_deployment_manifest["spec"]["template"]["spec"]["containers"][0]
    security_context = container_spec.get("securityContext", {})
    
    assert security_context.get("runAsNonRoot") == True, "runAsNonRoot must be true"
    assert security_context.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation must be false"
    assert security_context.get("runAsUser") == 999, f"Default runAsUser should be 999, got {security_context.get('runAsUser')}"
    assert security_context.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem must be true"

@pytest.mark.integration
def test_ac6_probes_success_when_db_up():
    """AC-6: Liveness and readiness probes succeed when database is responsive"""
    apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=postgres_deployment_manifest)
    
    # Wait for pod to be ready
    pod_ready = False
    for _ in range(120):
        pod = get_postgres_pod()
        if pod and pod.status.phase == "Running":
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    pod_ready = True
                    break
        if pod_ready:
            break
        time.sleep(1)
    
    assert pod_ready == True, "Pod never became ready, probes are failing"
    
    # Check no probe failure events
    events = core_v1.list_namespaced_event(namespace=NAMESPACE, field_selector=f"involvedObject.name={pod.metadata.name}")
    probe_failures = [e for e in events.items if "probe" in e.reason.lower() and "fail" in e.message.lower()]
    assert len(probe_failures) == 0, f"Found probe failures: {[e.message for e in probe_failures]}"

@pytest.mark.integration
def test_ac7_container_restarts_after_liveness_failures():
    """AC-7: Container restarts after 3 consecutive liveness probe failures"""
    # Modify liveness probe to always fail
    modified_deploy = postgres_deployment_manifest.copy()
    container = modified_deploy["spec"]["template"]["spec"]["containers"][0]
    container["livenessProbe"]["exec"]["command"] = ["false"]
    container["livenessProbe"]["initialDelaySeconds"] = 0
    container["livenessProbe"]["periodSeconds"] = 2
    
    apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=modified_deploy)
    
    # Wait for restart
    restart_count = 0
    for _ in range(60):
        pod = get_postgres_pod()
        if pod and pod.status.container_statuses:
            restart_count = pod.status.container_statuses[0].restart_count
            if restart_count >= 1:
                break
        time.sleep(1)
    
    assert restart_count >= 1, f"Expected at least 1 restart after liveness failures, got {restart_count}"

@pytest.mark.integration
def test_ac8_pod_not_ready_until_first_readiness_success():
    """AC-8: Pod remains NotReady until first successful readiness probe"""
    modified_deploy = postgres_deployment_manifest.copy()
    container = modified_deploy["spec"]["template"]["spec"]["containers"][0]
    container["readinessProbe"]["initialDelaySeconds"] = 15
    
    apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=modified_deploy)
    
    # Check pod is not ready for first 10 seconds
    for _ in range(10):
        pod = get_postgres_pod()
        if pod and pod.status.phase == "Running":
            ready_cond = next(c for c in pod.status.conditions if c.type == "Ready")
            assert ready_cond.status == "False", "Pod became ready before readiness probe initial delay"
        time.sleep(1)
    
    # Wait for pod to become ready
    pod_ready = False
    for _ in range(30):
        pod = get_postgres_pod()
        if pod:
            ready_cond = next(c for c in pod.status.conditions if c.type == "Ready")
            if ready_cond.status == "True":
                pod_ready = True
                break
        time.sleep(1)
    
    assert pod_ready == True, "Pod never became ready after initial delay"

@pytest.mark.performance
def test_ac9_default_resources_sufficient_for_demo():
    """AC-9: Default resources run demo workload without throttling or OOM"""
    apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=postgres_deployment_manifest)
    
    # Wait for pod ready
    for _ in range(120):
        pod = get_postgres_pod()
        if pod and pod.status.phase == "Running":
            ready_cond = next(c for c in pod.status.conditions if c.type == "Ready")
            if ready_cond.status == "True":
                break
        time.sleep(1)
    
    # Run demo workload (1k sample queries)
    exec_cmd = [
        "sh", "-c",
        "for i in $(seq 1 1000); do psql -U postgres -h localhost -c 'SELECT 1; SELECT * FROM information_schema.tables LIMIT 10'; done"
    ]
    
    # Execute command in pod
    resp = stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=exec_cmd,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    
    # Check no restarts (OOM would cause restart)
    pod = get_postgres_pod()
    assert pod.status.container_statuses[0].restart_count == 0, "Pod restarted during workload, likely OOM"
    
    # Check no throttling events
    events = core_v1.list_namespaced_event(namespace=NAMESPACE, field_selector=f"involvedObject.name={pod.metadata.name}")
    throttle_events = [e for e in events.items if "throttled" in e.message.lower()]
    assert len(throttle_events) == 0, f"Found CPU throttling events: {[e.message for e in throttle_events]}"
