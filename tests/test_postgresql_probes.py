#!/usr/bin/env python3
import pytest
import subprocess
import time
from kubernetes import client, config
from kubernetes.stream import stream

# Load kubernetes config
config.load_kube_config()
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

POSTGRES_DEPLOYMENT_NAME = "postgresql"
POSTGRES_LABEL_SELECTOR = "app.kubernetes.io/name=postgresql"
POSTGRES_SECRET_NAME = "otel-demo-postgresql"
NAMESPACE = "default"

def get_postgres_deployment():
    """Helper to get postgres deployment object"""
    return apps_v1.read_namespaced_deployment(name=POSTGRES_DEPLOYMENT_NAME, namespace=NAMESPACE)

def get_postgres_pod():
    """Helper to get running postgres pod"""
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=POSTGRES_LABEL_SELECTOR)
    for pod in pods.items:
        if pod.status.phase == "Running":
            return pod
    return None

@pytest.mark.ac1
def test_ac1_liveness_probe_exec_pg_isready_no_tcp():
    """AC-1: Liveness probe is exec running pg_isready with secret auth, TCP probe removed"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    # Check liveness probe exists and is exec type, not tcp
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.exec is not None, "Liveness probe is not exec type"
    assert container.liveness_probe.tcp_socket is None, "TCP socket liveness probe not removed"
    
    # Check command matches spec
    expected_cmd = ["pg_isready", "-U", "$(POSTGRES_USER)", "-d", "postgres"]
    assert container.liveness_probe.exec.command == expected_cmd, f"Liveness probe command mismatch, expected {expected_cmd}"

@pytest.mark.ac2
def test_ac2_readiness_probe_exec_select_query_no_tcp():
    """AC-2: Readiness probe is exec running SELECT 1 query with secret auth, TCP probe removed"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    # Check readiness probe exists and is exec type, not tcp
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.exec is not None, "Readiness probe is not exec type"
    assert container.readiness_probe.tcp_socket is None, "TCP socket readiness probe not removed"
    
    # Check command matches spec
    expected_cmd = ["psql", "-U", "$(POSTGRES_USER)", "-d", "$(POSTGRES_DB)", "-c", "SELECT 1;"]
    assert container.readiness_probe.exec.command == expected_cmd, f"Readiness probe command mismatch, expected {expected_cmd}"

@pytest.mark.ac3
def test_ac3_probes_use_existing_secret_no_hardcoded_creds():
    """AC-3: Probes use existing otel-demo-postgresql secret, no hardcoded credentials"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    # Check environment variables are sourced from correct secret
    env_vars = {env.name: env for env in container.env}
    for env_name in ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]:
        assert env_name in env_vars, f"{env_name} not found in container environment variables"
        assert env_vars[env_name].value_from is not None, f"{env_name} is hardcoded instead of sourced from secret"
        assert env_vars[env_name].value_from.secret_key_ref is not None, f"{env_name} not sourced from secret"
        assert env_vars[env_name].value_from.secret_key_ref.name == POSTGRES_SECRET_NAME, f"{env_name} not sourced from {POSTGRES_SECRET_NAME} secret"
    
    # Verify no hardcoded credentials in probe commands
    liveness_cmd = " ".join(container.liveness_probe.exec.command)
    readiness_cmd = " ".join(container.readiness_probe.exec.command)
    assert "postgres:" not in liveness_cmd and "postgres:" not in readiness_cmd, "Hardcoded credentials found in probe commands"
    assert "password=" not in liveness_cmd and "password=" not in readiness_cmd, "Hardcoded credentials found in probe commands"

@pytest.mark.ac4
def test_ac4_probe_timing_parameters_meet_thresholds():
    """AC-4: Probe timing parameters meet minimum thresholds"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    for probe in [container.liveness_probe, container.readiness_probe]:
        assert probe.initial_delay_seconds >= 5, f"initialDelaySeconds {probe.initial_delay_seconds} is less than minimum 5"
        assert probe.timeout_seconds >= 2, f"timeoutSeconds {probe.timeout_seconds} is less than minimum 2"
        assert probe.failure_threshold >= 3, f"failureThreshold {probe.failure_threshold} is less than minimum 3"
        assert probe.period_seconds == 10, f"periodSeconds should be 10, got {probe.period_seconds}"
        assert probe.success_threshold == 1, f"successThreshold should be 1, got {probe.success_threshold}"

@pytest.mark.ac5
def test_ac5_pod_transitions_to_ready_within_60s():
    """AC-5: Postgres pod transitions to Running/Ready state within 60 seconds of deployment"""
    # Delete existing pod to trigger recreation
    existing_pod = get_postgres_pod()
    if existing_pod:
        v1.delete_namespaced_pod(name=existing_pod.metadata.name, namespace=NAMESPACE, body=client.V1DeleteOptions())
    
    # Wait for new pod to be created and become ready
    start_time = time.time()
    ready = False
    for _ in range(12): # 12 * 5s = 60s total
        time.sleep(5)
        pod = get_postgres_pod()
        if not pod:
            continue
        for condition in pod.status.conditions:
            if condition.type == "Ready" and condition.status == "True":
                ready = True
                break
        if ready:
            break
    
    assert ready, f"Postgres pod did not become ready within 60 seconds"
    elapsed = time.time() - start_time
    assert elapsed <= 60, f"Postgres pod took {elapsed}s to become ready, exceeds 60s limit"

@pytest.mark.ac6
def test_ac6_readiness_probe_fails_when_listening_not_accepting_queries():
    """AC-6: Readiness probe fails when postgres is listening on 5432 but not accepting queries"""
    deploy = get_postgres_deployment()
    # Temporarily set wrong password in env to simulate auth failure (port still open but queries fail)
    original_env = deploy.spec.template.spec.containers[0].env.copy()
    wrong_env = [env for env in original_env if env.name != "POSTGRES_PASSWORD"]
    wrong_env.append(client.V1EnvVar(name="POSTGRES_PASSWORD", value="wrongpassword"))
    deploy.spec.template.spec.containers[0].env = wrong_env
    
    # Apply broken deployment
    apps_v1.replace_namespaced_deployment(name=POSTGRES_DEPLOYMENT_NAME, namespace=NAMESPACE, body=deploy)
    
    try:
        # Wait for new pod to start
        time.sleep(30)
        pod = get_postgres_pod()
        assert pod is not None
        
        # Check that port 5432 is open (listening)
        port_check = subprocess.run(
            ["kubectl", "exec", "-n", NAMESPACE, pod.metadata.name, "--", "nc", "-z", "localhost", "5432"],
            capture_output=True
        )
        assert port_check.returncode == 0, "Port 5432 is not open, test setup failed"
        
        # Check readiness probe fails
        for condition in pod.status.conditions:
            if condition.type == "Ready":
                assert condition.status == "False", "Readiness probe passed even though postgres not accepting queries"
                break
    finally:
        # Restore original deployment
        deploy.spec.template.spec.containers[0].env = original_env
        apps_v1.replace_namespaced_deployment(name=POSTGRES_DEPLOYMENT_NAME, namespace=NAMESPACE, body=deploy)
        # Wait for recovery
        time.sleep(30)

@pytest.mark.ac7
def test_ac7_both_probes_pass_when_operational():
    """AC-7: Both liveness and readiness probes return success when postgres is fully operational"""
    pod = get_postgres_pod()
    assert pod is not None
    
    # Check pod is ready
    ready = False
    for condition in pod.status.conditions:
        if condition.type == "Ready" and condition.status == "True":
            ready = True
            break
    assert ready, "Postgres pod is not ready, probes are failing"
    
    # Manually run probe commands to verify success
    liveness_cmd = ["pg_isready", "-U", "postgres", "-d", "postgres"]
    liveness_result = stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=liveness_cmd,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert liveness_result.returncode == 0, f"Liveness probe command failed: {liveness_result.stderr}"
    
    readiness_cmd = ["psql", "-U", "postgres", "-d", "postgres", "-c", "SELECT 1;"]
    readiness_result = stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=readiness_cmd,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert readiness_result.returncode == 0, f"Readiness probe command failed: {readiness_result.stderr}"

@pytest.mark.ac8
def test_ac8_no_unrelated_changes_to_deployment():
    """AC-8: No changes to postgres service, env vars, volumes, image beyond probe modifications"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    # Verify image is still postgres:15-alpine
    assert container.image.startswith("postgres:"), "Postgres image changed unexpectedly"
    assert "15-alpine" in container.image, "Postgres image version changed unexpectedly"
    
    # Verify volume mounts are unchanged (check at least 1 exists for data)
    assert len(container.volume_mounts) >= 1, "Volume mounts missing from postgres container"
    assert any("postgres-data" in mount.name for mount in container.volume_mounts), "Postgres data volume mount missing"
    
    # Verify service exists and still exposes port 5432
    service = v1.read_namespaced_service(name="postgresql", namespace=NAMESPACE)
    assert service.spec.ports[0].port == 5432, "Postgres service port changed unexpectedly"
    assert service.spec.ports[0].target_port == 5432, "Postgres service target port changed unexpectedly"
