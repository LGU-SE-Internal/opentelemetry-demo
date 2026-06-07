import time
import os
import pytest
import subprocess
import json
from kubernetes import client, config
from datetime import datetime, timedelta

# Test constants from spec
ENV_RETENTION_PERIOD = "JAEGER_BADGER_RETENTION_PERIOD"
ENV_DISK_THRESHOLD = "JAEGER_BADGER_RETENTION_DISK_THRESHOLD"
DEFAULT_RETENTION_PERIOD = "72h"
DEFAULT_DISK_THRESHOLD = 0.9
JAEGER_DEPLOYMENT_NAME = "jaeger"
NAMESPACE = "default"

@pytest.fixture(scope="module")
def k8s_client():
    config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api()

def get_jaeger_pod(k8s_core):
    pods = k8s_core.list_namespaced_pod(NAMESPACE, label_selector="app.kubernetes.io/name=jaeger")
    assert len(pods.items) > 0, "No Jaeger pods found"
    return pods.items[0]

def get_jaeger_container_args(k8s_apps):
    deploy = k8s_apps.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
    return container.args

def get_pod_logs(k8s_core, pod_name, tail_lines=100):
    return k8s_core.read_namespaced_pod_log(pod_name, NAMESPACE, tail_lines=tail_lines)

def test_ac1_time_based_retention_deletes_old_traces(k8s_client):
    """AC-1: Traces older than configured retention period are automatically deleted within 1 hour"""
    core_v1, apps_v1 = k8s_client
    test_period = "24h"
    
    # Set retention period to 24h
    deploy = apps_v1.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
    env_vars = container.env or []
    env_vars = [e for e in env_vars if e.name != ENV_RETENTION_PERIOD]
    env_vars.append(client.V1EnvVar(name=ENV_RETENTION_PERIOD, value=test_period))
    container.env = env_vars
    apps_v1.patch_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE, deploy)
    
    # Wait for deployment to rollout
    subprocess.run(["kubectl", "rollout", "status", f"deployment/{JAEGER_DEPLOYMENT_NAME}", "-n", NAMESPACE], check=True)
    
    # Get current pod
    pod = get_jaeger_pod(core_v1)
    
    # Verify retention flag is present with correct value
    args = get_jaeger_container_args(apps_v1)
    assert any(f"--badger.retention-time={test_period}" in a for a in args), "Retention time flag not set correctly"
    
    # TODO: Generate test traces, store timestamps, wait 25h, verify old traces are gone
    # Invariant: No traces older than retention period exist after pruning run
    pytest.fail("Implementation not complete: Time based retention not implemented")

def test_ac2_default_retention_period_applied(k8s_client):
    """AC-2: Default 72h retention is applied when no explicit value is set"""
    core_v1, apps_v1 = k8s_client
    
    # Remove any explicit retention period env var
    deploy = apps_v1.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
    env_vars = container.env or []
    env_vars = [e for e in env_vars if e.name != ENV_RETENTION_PERIOD]
    container.env = env_vars
    apps_v1.patch_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE, deploy)
    
    # Wait for rollout
    subprocess.run(["kubectl", "rollout", "status", f"deployment/{JAEGER_DEPLOYMENT_NAME}", "-n", NAMESPACE], check=True)
    
    # Verify retention flag uses default value
    args = get_jaeger_container_args(apps_v1)
    assert any(f"--badger.retention-time={DEFAULT_RETENTION_PERIOD}" in a for a in args), "Default retention time not applied"
    
    pytest.fail("Implementation not complete: Default retention not set")

def test_ac3_disk_threshold_pruning(k8s_client):
    """AC-3: Old traces are pruned when disk usage exceeds threshold until below threshold"""
    core_v1, apps_v1 = k8s_client
    test_threshold = 0.8
    
    # Set disk threshold to 0.8
    deploy = apps_v1.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
    env_vars = container.env or []
    env_vars = [e for e in env_vars if e.name != ENV_DISK_THRESHOLD]
    env_vars.append(client.V1EnvVar(name=ENV_DISK_THRESHOLD, value=str(test_threshold)))
    container.env = env_vars
    apps_v1.patch_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE, deploy)
    
    # Wait for rollout
    subprocess.run(["kubectl", "rollout", "status", f"deployment/{JAEGER_DEPLOYMENT_NAME}", "-n", NAMESPACE], check=True)
    
    # Verify disk threshold flag is set correctly
    args = get_jaeger_container_args(apps_v1)
    assert any(f"--badger.retention-disk-space-usage-threshold={test_threshold}" in a for a in args), "Disk threshold flag not set correctly"
    
    # TODO: Fill disk to >80%, verify pruning runs, usage drops below 80%
    # Invariant: Disk usage never stays above threshold for more than 1 hour
    pytest.fail("Implementation not complete: Disk threshold pruning not implemented")

def test_ac4_existing_data_no_unintended_deletion(k8s_client):
    """AC-4: No data younger than retention period is deleted when policy is enabled on existing deployment"""
    core_v1, apps_v1 = k8s_client
    test_period = "168h" # 7 days
    
    # Generate test traces of various ages (1 day old, 3 days old, 8 days old) before enabling policy
    # TODO: Pre-populate storage with test traces
    
    # Enable retention policy
    deploy = apps_v1.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
    env_vars = container.env or []
    env_vars = [e for e in env_vars if e.name != ENV_RETENTION_PERIOD]
    env_vars.append(client.V1EnvVar(name=ENV_RETENTION_PERIOD, value=test_period))
    container.env = env_vars
    apps_v1.patch_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE, deploy)
    
    # Wait for rollout and pruning run
    time.sleep(3600) # Wait 1 hour for pruning to run
    
    # Verify traces <7 days old are present, traces >7 days old are deleted
    # Invariant: All traces with age < retention period exist after policy application
    pytest.fail("Implementation not complete: Existing data protection not verified")

def test_ac5_invalid_retention_period_causes_crashloop(k8s_client):
    """AC-5: Invalid retention period value causes CrashLoopBackOff with clear error"""
    core_v1, apps_v1 = k8s_client
    invalid_values = ["72days", "abc", "12", "24mxyz"]
    
    for invalid_val in invalid_values:
        # Set invalid retention period
        deploy = apps_v1.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
        container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
        env_vars = container.env or []
        env_vars = [e for e in env_vars if e.name != ENV_RETENTION_PERIOD]
        env_vars.append(client.V1EnvVar(name=ENV_RETENTION_PERIOD, value=invalid_val))
        container.env = env_vars
        apps_v1.patch_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE, deploy)
        
        # Wait for pod to crash
        time.sleep(60)
        pod = get_jaeger_pod(core_v1)
        
        # Verify pod is in CrashLoopBackOff
        assert pod.status.phase == "Running" or pod.status.phase == "Pending"
        container_status = next(s for s in pod.status.container_statuses if s.name == JAEGER_DEPLOYMENT_NAME)
        assert container_status.state.waiting is not None
        assert container_status.state.waiting.reason == "CrashLoopBackOff", f"Pod not in CrashLoopBackOff for invalid value {invalid_val}"
        
        # Verify error message in logs
        logs = get_pod_logs(core_v1, pod.metadata.name)
        assert "invalid duration" in logs.lower(), f"No invalid duration error in logs for value {invalid_val}"
    
    pytest.fail("Implementation not complete: Invalid retention period validation not working")

def test_ac6_invalid_disk_threshold_causes_crashloop(k8s_client):
    """AC-6: Invalid disk threshold value causes CrashLoopBackOff with clear error"""
    core_v1, apps_v1 = k8s_client
    invalid_values = ["1.5", "-0.1", "abc", "100"]
    
    for invalid_val in invalid_values:
        # Set invalid disk threshold
        deploy = apps_v1.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
        container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
        env_vars = container.env or []
        env_vars = [e for e in env_vars if e.name != ENV_DISK_THRESHOLD]
        env_vars.append(client.V1EnvVar(name=ENV_DISK_THRESHOLD, value=invalid_val))
        container.env = env_vars
        apps_v1.patch_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE, deploy)
        
        # Wait for pod to crash
        time.sleep(60)
        pod = get_jaeger_pod(core_v1)
        
        # Verify pod is in CrashLoopBackOff
        container_status = next(s for s in pod.status.container_statuses if s.name == JAEGER_DEPLOYMENT_NAME)
        assert container_status.state.waiting is not None
        assert container_status.state.waiting.reason == "CrashLoopBackOff", f"Pod not in CrashLoopBackOff for invalid value {invalid_val}"
        
        # Verify error message in logs
        logs = get_pod_logs(core_v1, pod.metadata.name)
        assert "invalid threshold" in logs.lower() or "out of range" in logs.lower(), f"No invalid threshold error in logs for value {invalid_val}"
    
    pytest.fail("Implementation not complete: Invalid disk threshold validation not working")

def test_ac7_zero_threshold_disables_disk_pruning(k8s_client):
    """AC-7: Disk threshold 0 disables disk-based pruning, only time-based retention active"""
    core_v1, apps_v1 = k8s_client
    
    # Set disk threshold to 0
    deploy = apps_v1.read_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE)
    container = next(c for c in deploy.spec.template.spec.containers if c.name == JAEGER_DEPLOYMENT_NAME)
    env_vars = container.env or []
    env_vars = [e for e in env_vars if e.name != ENV_DISK_THRESHOLD]
    env_vars.append(client.V1EnvVar(name=ENV_DISK_THRESHOLD, value="0"))
    container.env = env_vars
    apps_v1.patch_namespaced_deployment(JAEGER_DEPLOYMENT_NAME, NAMESPACE, deploy)
    
    # Wait for rollout
    subprocess.run(["kubectl", "rollout", "status", f"deployment/{JAEGER_DEPLOYMENT_NAME}", "-n", NAMESPACE], check=True)
    
    # Verify disk threshold flag is set to 0, time-based retention is still present
    args = get_jaeger_container_args(apps_v1)
    assert any(f"--badger.retention-disk-space-usage-threshold=0" in a for a in args), "Disk threshold 0 not set correctly"
    assert any("--badger.retention-time=" in a for a in args), "Time-based retention missing when disk threshold is 0"
    
    # TODO: Fill disk above default 90% threshold, verify no pruning occurs
    # Invariant: No pruning triggered by disk usage when threshold is 0
    pytest.fail("Implementation not complete: Disk pruning disable not working")
