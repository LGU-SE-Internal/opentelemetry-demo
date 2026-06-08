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

LLM_DEPLOYMENT_NAME = "llm"
LLM_LABEL_SELECTOR = "app.kubernetes.io/name=llm"
NAMESPACE = "default"

def get_llm_deployment():
    """Helper to get llm deployment object"""
    return apps_v1.read_namespaced_deployment(name=LLM_DEPLOYMENT_NAME, namespace=NAMESPACE)

def get_llm_pod():
    """Helper to get running llm pod"""
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=LLM_LABEL_SELECTOR)
    for pod in pods.items:
        if pod.status.phase == "Running":
            return pod
    return None

@pytest.mark.ac1
def test_ac1_liveness_probe_config_defaults():
    """AC-1: Liveness probe uses /health endpoint with default parameters 10s initial delay, 5s period, 3 failure threshold"""
    deploy = get_llm_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.http_get is not None, "Liveness probe is not HTTP GET type"
    assert container.liveness_probe.http_get.path == "/health", "Liveness probe not targeting /health endpoint"
    
    # Check default parameters
    assert container.liveness_probe.initial_delay_seconds == 10, f"Expected initial delay 10s, got {container.liveness_probe.initial_delay_seconds}"
    assert container.liveness_probe.period_seconds == 5, f"Expected period 5s, got {container.liveness_probe.period_seconds}"
    assert container.liveness_probe.failure_threshold == 3, f"Expected failure threshold 3, got {container.liveness_probe.failure_threshold}"

@pytest.mark.ac2
def test_ac2_readiness_probe_config_defaults():
    """AC-2: Readiness probe uses /health endpoint with default parameters 5s initial delay, 3s period, 2 failure threshold"""
    deploy = get_llm_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.http_get is not None, "Readiness probe is not HTTP GET type"
    assert container.readiness_probe.http_get.path == "/health", "Readiness probe not targeting /health endpoint"
    
    # Check default parameters
    assert container.readiness_probe.initial_delay_seconds == 5, f"Expected initial delay 5s, got {container.readiness_probe.initial_delay_seconds}"
    assert container.readiness_probe.period_seconds == 3, f"Expected period 3s, got {container.readiness_probe.period_seconds}"
    assert container.readiness_probe.failure_threshold == 2, f"Expected failure threshold 2, got {container.readiness_probe.failure_threshold}"

@pytest.mark.ac3
def test_ac3_probe_parameters_configurable_via_env():
    """AC-3: All liveness and readiness probe parameters can be overridden via respective environment variables"""
    deploy = get_llm_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    # Verify probe fields reference environment variables
    probe_env_vars = [
        "LLM_LIVENESS_INITIAL_DELAY",
        "LLM_LIVENESS_PERIOD",
        "LLM_LIVENESS_FAILURE_THRESHOLD",
        "LLM_READINESS_INITIAL_DELAY",
        "LLM_READINESS_PERIOD",
        "LLM_READINESS_FAILURE_THRESHOLD"
    ]
    
    env_var_names = [env.name for env in container.env]
    for env_var in probe_env_vars:
        assert env_var in env_var_names, f"{env_var} not found in container environment variables"
        # Verify values are set from env var with correct defaults
        env = next(e for e in container.env if e.name == env_var)
        assert env.value is not None, f"{env_var} has no default value set"

@pytest.mark.ac4
def test_ac4_resource_defaults_configured():
    """AC-4: Default resource requests 0.5 CPU / 1Gi memory, limits 2 CPU / 4Gi memory"""
    deploy = get_llm_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.resources is not None, "Resources not configured for llm container"
    assert container.resources.requests is not None, "Resource requests not configured"
    assert container.resources.limits is not None, "Resource limits not configured"
    
    # Check defaults
    assert container.resources.requests["cpu"] == "0.5", f"Expected CPU request 0.5, got {container.resources.requests['cpu']}"
    assert container.resources.requests["memory"] == "1Gi", f"Expected memory request 1Gi, got {container.resources.requests['memory']}"
    assert container.resources.limits["cpu"] == "2", f"Expected CPU limit 2, got {container.resources.limits['cpu']}"
    assert container.resources.limits["memory"] == "4Gi", f"Expected memory limit 4Gi, got {container.resources.limits['memory']}"

@pytest.mark.ac5
def test_ac5_resource_parameters_configurable_via_env():
    """AC-5: All CPU and memory resource values can be overridden via respective environment variables"""
    deploy = get_llm_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    resource_env_vars = [
        "LLM_CPU_REQUEST",
        "LLM_CPU_LIMIT",
        "LLM_MEMORY_REQUEST",
        "LLM_MEMORY_LIMIT"
    ]
    
    env_var_names = [env.name for env in container.env]
    for env_var in resource_env_vars:
        assert env_var in env_var_names, f"{env_var} not found in container environment variables"
        env = next(e for e in container.env if e.name == env_var)
        assert env.value is not None, f"{env_var} has no default value set"

@pytest.mark.ac6
def test_ac6_security_context_defaults():
    """AC-6: Security context configured with non-root, no privilege escalation, all capabilities dropped, default UID 1000"""
    deploy = get_llm_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.security_context is not None, "Security context not configured"
    assert container.security_context.run_as_non_root == True, "runAsNonRoot not set to true"
    assert container.security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation not set to false"
    assert container.security_context.capabilities is not None, "Capabilities not configured"
    assert "ALL" in container.security_context.capabilities.drop, "ALL capabilities not dropped"
    assert container.security_context.run_as_user == 1000, f"Expected runAsUser 1000, got {container.security_context.run_as_user}"
    
    # Verify runAsUser is configurable via env
    env_var_names = [env.name for env in container.env]
    assert "LLM_RUN_AS_USER" in env_var_names, "LLM_RUN_AS_USER not found in container environment variables"

@pytest.mark.ac7
def test_ac7_health_endpoint_200_marks_pod_live_ready():
    """AC-7: When /health returns 200 OK, pod is marked as live and ready"""
    pod = get_llm_pod()
    assert pod is not None, "No running llm pod found"
    
    # Verify pod is ready
    ready = False
    for condition in pod.status.conditions:
        if condition.type == "Ready" and condition.status == "True":
            ready = True
            break
    assert ready, "Pod is not marked as ready when /health returns 200 OK"
    
    # Verify /health endpoint returns 200
    result = stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8080/health"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert result.strip() == "200", f"Expected /health to return 200, got {result.strip()}"

@pytest.mark.ac8
def test_ac8_liveness_failure_triggers_restart():
    """AC-8: Consecutive liveness failures equal to failure threshold trigger pod restart"""
    deploy = get_llm_deployment()
    # Temporarily modify liveness probe to hit non-existent endpoint
    original_liveness = deploy.spec.template.spec.containers[0].liveness_probe.http_get.path
    deploy.spec.template.spec.containers[0].liveness_probe.http_get.path = "/non-existent-health"
    
    # Apply broken deployment
    apps_v1.replace_namespaced_deployment(name=LLM_DEPLOYMENT_NAME, namespace=NAMESPACE, body=deploy)
    
    try:
        # Wait for pod to restart
        time.sleep(60) # Wait longer than default failure threshold time (3 * 5s = 15s plus initial delay)
        pod = get_llm_pod()
        assert pod is not None
        
        # Check restart count > 0
        assert pod.status.container_statuses[0].restart_count > 0, "Pod did not restart after liveness probe failures"
    finally:
        # Restore original deployment
        deploy = get_llm_deployment()
        deploy.spec.template.spec.containers[0].liveness_probe.http_get.path = original_liveness
        apps_v1.replace_namespaced_deployment(name=LLM_DEPLOYMENT_NAME, namespace=NAMESPACE, body=deploy)
        time.sleep(30)

@pytest.mark.ac9
def test_ac9_readiness_failure_removes_from_endpoints():
    """AC-9: Consecutive readiness failures equal to failure threshold remove pod from service endpoints"""
    pod = get_llm_pod()
    assert pod is not None
    
    # Temporarily break /health endpoint
    stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=["mv", "/app/health_endpoint", "/app/health_endpoint.bak"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    try:
        # Wait for readiness probe to fail
        time.sleep(20) # Wait longer than default failure threshold (2 * 3s = 6s plus initial delay)
        
        # Check pod is not ready
        pod = get_llm_pod()
        ready = False
        for condition in pod.status.conditions:
            if condition.type == "Ready" and condition.status == "False":
                ready = True
                break
        assert ready, "Pod still marked as ready after readiness probe failures"
        
        # Check pod is removed from endpoints
        endpoints = v1.read_namespaced_endpoints(name=LLM_DEPLOYMENT_NAME, namespace=NAMESPACE)
        pod_ip_found = any(subset.addresses is not None and any(addr.ip == pod.status.pod_ip for addr in subset.addresses) for subset in endpoints.subsets)
        assert not pod_ip_found, "Pod still present in service endpoints after readiness failures"
    finally:
        # Restore health endpoint
        stream(
            v1.connect_get_namespaced_pod_exec,
            pod.metadata.name,
            NAMESPACE,
            command=["mv", "/app/health_endpoint.bak", "/app/health_endpoint"],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        time.sleep(20)

@pytest.mark.ac10
def test_ac10_cpu_limit_enforced():
    """AC-10: CPU usage above limit is throttled by Kubernetes"""
    pod = get_llm_pod()
    assert pod is not None
    
    # Run CPU stress test
    stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=["python", "-c", "while True: pass"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False
    )
    
    # Wait for 10 seconds to let CPU usage spike
    time.sleep(10)
    
    # Check CPU throttling metrics
    metrics = subprocess.run(
        ["kubectl", "top", "pod", pod.metadata.name, "-n", NAMESPACE, "--containers"],
        capture_output=True,
        text=True
    )
    assert metrics.returncode == 0, "Failed to get pod metrics"
    
    cpu_usage = metrics.stdout.splitlines()[1].split()[1]
    # Check usage doesn't exceed limit (2 cores = 2000m)
    assert int(cpu_usage.replace("m", "")) <= 2000, f"CPU usage {cpu_usage} exceeds limit of 2000m, throttling not working"

@pytest.mark.ac11
def test_ac11_memory_limit_enforced():
    """AC-11: Memory usage above limit results in OOMKilled termination"""
    deploy = get_llm_deployment()
    # Temporarily lower memory limit to make test easier
    original_memory_limit = deploy.spec.template.spec.containers[0].resources.limits["memory"]
    deploy.spec.template.spec.containers[0].resources.limits["memory"] = "128Mi"
    
    # Apply modified deployment
    apps_v1.replace_namespaced_deployment(name=LLM_DEPLOYMENT_NAME, namespace=NAMESPACE, body=deploy)
    
    try:
        time.sleep(30)
        pod = get_llm_pod()
        assert pod is not None
        
        # Run memory stress test
        stream(
            v1.connect_get_namespaced_pod_exec,
            pod.metadata.name,
            NAMESPACE,
            command=["python", "-c", "a = []; while True: a.append('x' * 1024 * 1024)"],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False
        )
        
        # Wait for OOM kill
        time.sleep(20)
        
        # Check pod was OOM killed
        pod = get_llm_pod()
        assert pod.status.container_statuses[0].last_state.terminated is not None
        assert pod.status.container_statuses[0].last_state.terminated.reason == "OOMKilled", "Pod not terminated with OOMKilled status when exceeding memory limit"
    finally:
        # Restore original memory limit
        deploy = get_llm_deployment()
        deploy.spec.template.spec.containers[0].resources.limits["memory"] = original_memory_limit
        apps_v1.replace_namespaced_deployment(name=LLM_DEPLOYMENT_NAME, namespace=NAMESPACE, body=deploy)
        time.sleep(30)

@pytest.mark.ac12
def test_ac12_security_context_enforced():
    """AC-12: Container runs as non-root UID, no root privileges, cannot escalate privileges"""
    pod = get_llm_pod()
    assert pod is not None
    
    # Check running user
    whoami_result = stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=["id", "-u"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert whoami_result.strip() == "1000", f"Running as UID {whoami_result.strip()}, expected 1000"
    
    # Check cannot run as root
    su_result = stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=["su", "root", "-c", "echo test"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert "Permission denied" in su_result, "Able to switch to root user, privilege restrictions not working"
    
    # Check cannot perform privileged operation
    mount_result = stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=["mount", "-t", "tmpfs", "tmpfs", "/tmp/test"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert "Operation not permitted" in mount_result, "Able to perform privileged mount operation, capabilities not dropped properly"
