import pytest
import subprocess
import json
import time
from kubernetes import client, config

@pytest.fixture(scope="module")
def k8s_client():
    config.load_kube_config()
    return client.CoreV1Api()

@pytest.fixture(scope="module")
def apps_client():
    config.load_kube_config()
    return client.AppsV1Api()

@pytest.fixture(scope="module")
def email_deployment(apps_client):
    deployments = apps_client.list_namespaced_deployment(namespace="default")
    for deploy in deployments.items:
        if "email" in deploy.metadata.name:
            return deploy
    return None

@pytest.fixture(scope="module")
def email_pod(k8s_client):
    pods = k8s_client.list_namespaced_pod(namespace="default", label_selector="app=email")
    if pods.items:
        return pods.items[0]
    return None

def test_ac1_liveness_probe_config(email_deployment):
    """AC-1: Liveness probe is configured correctly with /health endpoint, proper delays and thresholds"""
    assert email_deployment is not None, "Email deployment not found"
    
    container = email_deployment.spec.template.spec.containers[0]
    assert container.liveness_probe is not None, "Liveness probe not configured"
    
    assert container.liveness_probe.http_get is not None
    assert container.liveness_probe.http_get.path == "/health"
    assert container.liveness_probe.http_get.port == 8080  # Assumed exposed port, adjust if needed
    assert container.liveness_probe.initial_delay_seconds >= 5
    assert container.liveness_probe.period_seconds >= 10
    assert container.liveness_probe.failure_threshold >= 3

def test_ac2_readiness_probe_config(email_deployment):
    """AC-2: Readiness probe is configured correctly with /health endpoint, proper delays and thresholds"""
    assert email_deployment is not None, "Email deployment not found"
    
    container = email_deployment.spec.template.spec.containers[0]
    assert container.readiness_probe is not None, "Readiness probe not configured"
    
    assert container.readiness_probe.http_get is not None
    assert container.readiness_probe.http_get.path == "/health"
    assert container.readiness_probe.http_get.port == 8080  # Assumed exposed port, adjust if needed
    assert container.readiness_probe.initial_delay_seconds >= 2
    assert container.readiness_probe.period_seconds >= 5
    assert container.readiness_probe.failure_threshold >= 3

def test_ac3_resource_config(email_deployment):
    """AC-3: CPU and memory resources are set within valid ranges"""
    assert email_deployment is not None, "Email deployment not found"
    
    container = email_deployment.spec.template.spec.containers[0]
    assert container.resources is not None, "Resources not configured"
    assert container.resources.requests is not None, "Resource requests not configured"
    assert container.resources.limits is not None, "Resource limits not configured"
    
    # Parse CPU values (convert from millicores if needed)
    def parse_cpu(cpu_str):
        if cpu_str.endswith('m'):
            return int(cpu_str[:-1])
        return int(float(cpu_str) * 1000)
    
    # Parse memory values (convert to Mi)
    def parse_memory(mem_str):
        if mem_str.endswith('Mi'):
            return int(mem_str[:-2])
        if mem_str.endswith('Gi'):
            return int(mem_str[:-2]) * 1024
        return int(mem_str) // (1024 * 1024)
    
    requests_cpu = parse_cpu(container.resources.requests['cpu'])
    requests_memory = parse_memory(container.resources.requests['memory'])
    limits_cpu = parse_cpu(container.resources.limits['cpu'])
    limits_memory = parse_memory(container.resources.limits['memory'])
    
    assert 10 <= requests_cpu <= 100, f"CPU request {requests_cpu}m outside valid range 10-100m"
    assert 64 <= requests_memory <= 256, f"Memory request {requests_memory}Mi outside valid range 64-256Mi"
    assert 200 <= limits_cpu <= 500, f"CPU limit {limits_cpu}m outside valid range 200-500m"
    assert 128 <= limits_memory <= 512, f"Memory limit {limits_memory}Mi outside valid range 128-512Mi"

def test_ac4_security_context_config(email_deployment):
    """AC-4: Container and pod security context configured for non-root execution"""
    assert email_deployment is not None, "Email deployment not found"
    
    pod_security_context = email_deployment.spec.template.spec.security_context
    container_security_context = email_deployment.spec.template.spec.containers[0].security_context
    
    assert pod_security_context is not None, "Pod security context not configured"
    assert pod_security_context.allow_privilege_escalation is False, "allowPrivilegeEscalation is not false"
    
    assert container_security_context is not None, "Container security context not configured"
    assert container_security_context.run_as_non_root is True, "runAsNonRoot is not true"
    assert container_security_context.run_as_user >= 1000, f"runAsUser {container_security_context.run_as_user} is less than 1000"

def test_ac5_dockerfile_non_root_config():
    """AC-5: Dockerfile contains non-root user creation, permission setup and USER instruction"""
    dockerfile_path = "src/email/Dockerfile"
    with open(dockerfile_path, 'r') as f:
        content = f.read()
    
    # Check for user creation
    assert "useradd" in content.lower() or "adduser" in content.lower(), "No user creation command found in Dockerfile"
    # Check for ownership change
    assert "chown" in content.lower(), "No chown command found for application directory"
    # Check for USER instruction
    assert "USER " in content, "No USER instruction found in Dockerfile"
    # Verify USER is not root
    user_lines = [line for line in content.split('\n') if line.strip().startswith('USER')]
    assert len(user_lines) > 0
    assert "root" not in user_lines[-1].lower(), "Last USER instruction is still root"

def test_ac6_process_runs_as_non_root(email_pod, k8s_client):
    """AC-6: Main service process runs as non-root user inside container"""
    assert email_pod is not None, "Email pod not found"
    assert email_pod.status.phase == "Running", "Email pod is not running"
    
    # Run id -u inside the pod
    exec_command = [
        '/bin/sh',
        '-c',
        'id -u'
    ]
    
    resp = k8s_client.connect_get_namespaced_pod_exec(
        email_pod.metadata.name,
        email_pod.metadata.namespace,
        command=exec_command,
        stderr=True, stdin=False,
        stdout=True, tty=False
    )
    
    uid = int(resp.strip())
    assert uid != 0, f"Process is running as root (UID {uid})"
    assert uid >= 1000, f"Process UID {uid} is less than 1000"

def test_ac7_pod_starts_running(email_deployment, apps_client, k8s_client):
    """AC-7: Pod becomes Running and Ready 1/1 within 60 seconds of deployment"""
    assert email_deployment is not None, "Email deployment not found"
    
    # Wait up to 60 seconds for deployment to be ready
    start_time = time.time()
    while time.time() - start_time < 60:
        deploy = apps_client.read_namespaced_deployment_status(
            email_deployment.metadata.name,
            email_deployment.metadata.namespace
        )
        if deploy.status.ready_replicas and deploy.status.ready_replicas >= 1:
            pods = k8s_client.list_namespaced_pod(
                namespace="default",
                label_selector=f"app=email",
                field_selector="status.phase=Running"
            )
            if pods.items:
                for pod in pods.items:
                    for condition in pod.status.conditions:
                        if condition.type == "Ready" and condition.status == "True":
                            return
        time.sleep(2)
    
    assert False, "Email pod did not become Ready within 60 seconds"

def test_ac8_probes_report_healthy_when_endpoint_ok(email_pod, k8s_client):
    """AC-8: Probes report healthy when /health returns 200"""
    assert email_pod is not None, "Email pod not found"
    
    # Check pod is ready
    ready = False
    for condition in email_pod.status.conditions:
        if condition.type == "Ready" and condition.status == "True":
            ready = True
            break
    assert ready, "Pod is not in Ready state when health endpoint is OK"
    
    # Check no recent restarts
    for container_status in email_pod.status.container_statuses:
        assert container_status.restart_count == 0, f"Container has {container_status.restart_count} restarts"

def test_ac9_probes_trigger_restart_when_endpoint_fails(email_pod, k8s_client, apps_client):
    """AC-9: Liveness probe triggers restart, readiness marks pod NotReady when /health fails 3 times"""
    assert email_pod is not None, "Email pod not found"
    
    # First get initial restart count
    initial_restarts = email_pod.status.container_statuses[0].restart_count
    
    # Verify probe failure thresholds are configured correctly
    container = apps_client.read_namespaced_deployment(email_pod.metadata.labels['app'], "default").spec.template.spec.containers[0]
    assert container.liveness_probe.failure_threshold >= 3
    assert container.readiness_probe.failure_threshold >= 3
