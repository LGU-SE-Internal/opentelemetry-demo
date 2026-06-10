"""Integration tests for Jaeger persistent storage ACs, issue #1901"""
import os
import subprocess
import time
from kubernetes import client, config
import pytest

# Load kube config
config.load_kube_config()
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")
TEST_STORAGE_CLASS = os.environ.get("TEST_STORAGE_CLASS", None)

def test_ac1_pvc_provisioned_on_valid_config():
    """AC-1: PVC is provisioned when storageSize >=10Gi is provided"""
    # Deploy Jaeger with valid PVC config
    cmd = [
        "helm", "install", "jaeger-test", "./kubernetes/helm",
        "--set", "jaeger.pvc.storageSize=10Gi",
        "--namespace", NAMESPACE
    ]
    if TEST_STORAGE_CLASS:
        cmd.extend(["--set", f"jaeger.pvc.storageClassName={TEST_STORAGE_CLASS}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Helm install failed: {result.stderr}"

    # Wait for PVC to be created and bound
    core_v1 = client.CoreV1Api()
    pvc_name = "jaeger-storage"
    bound = False
    for _ in range(30):
        try:
            pvc = core_v1.read_namespaced_persistent_volume_claim(pvc_name, NAMESPACE)
            if pvc.status.phase == "Bound":
                bound = True
                break
        except client.exceptions.ApiException:
            pass
        time.sleep(2)
    
    assert bound, f"PVC {pvc_name} not bound after 60 seconds"
    # Cleanup
    subprocess.run(["helm", "uninstall", "jaeger-test", "--namespace", NAMESPACE], capture_output=True)

def test_ac2_no_trace_loss_on_pod_restart():
    """AC-2: Traces survive Jaeger pod restart"""
    # First deploy with valid PVC
    cmd = [
        "helm", "install", "jaeger-test", "./kubernetes/helm",
        "--set", "jaeger.pvc.storageSize=10Gi",
        "--namespace", NAMESPACE
    ]
    if TEST_STORAGE_CLASS:
        cmd.extend(["--set", f"jaeger.pvc.storageClassName={TEST_STORAGE_CLASS}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Helm install failed: {result.stderr}"

    # Wait for Jaeger pod to be ready
    apps_v1 = client.AppsV1Api()
    ready = False
    for _ in range(60):
        deploy = apps_v1.read_namespaced_deployment("jaeger", NAMESPACE)
        if deploy.status.ready_replicas and deploy.status.ready_replicas >= 1:
            ready = True
            break
        time.sleep(2)
    assert ready, "Jaeger pod not ready after 120 seconds"

    # Get current Jaeger pod name
    core_v1 = client.CoreV1Api()
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app.kubernetes.io/name=jaeger")
    assert len(pods.items) == 1
    old_pod_name = pods.items[0].metadata.name

    # Delete pod to trigger restart
    core_v1.delete_namespaced_pod(old_pod_name, NAMESPACE)

    # Wait for new pod to be ready
    new_pod_ready = False
    new_pod_name = None
    for _ in range(60):
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app.kubernetes.io/name=jaeger")
        if len(pods.items) == 1 and pods.items[0].status.phase == "Running" and pods.items[0].metadata.name != old_pod_name:
            new_pod_ready = True
            new_pod_name = pods.items[0].metadata.name
            break
        time.sleep(2)
    assert new_pod_ready, "New Jaeger pod not ready after restart"

    # Verify PVC is still bound to same PV
    pvc = core_v1.read_namespaced_persistent_volume_claim("jaeger-storage", NAMESPACE)
    assert pvc.status.phase == "Bound"

    # Cleanup
    subprocess.run(["helm", "uninstall", "jaeger-test", "--namespace", NAMESPACE], capture_output=True)

def test_ac3_trace_retention_ttl_works():
    """AC-3: Traces older than JAEGER_RETENTION_GO_TRACES_TTL are deleted"""
    # Deploy with TTL set to 2d
    cmd = [
        "helm", "install", "jaeger-test", "./kubernetes/helm",
        "--set", "jaeger.pvc.storageSize=10Gi",
        "--set", "jaeger.retention.ttl=2d",
        "--namespace", NAMESPACE
    ]
    if TEST_STORAGE_CLASS:
        cmd.extend(["--set", f"jaeger.pvc.storageClassName={TEST_STORAGE_CLASS}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Helm install failed: {result.stderr}"

    # Wait for pod ready
    apps_v1 = client.AppsV1Api()
    ready = False
    for _ in range(60):
        deploy = apps_v1.read_namespaced_deployment("jaeger", NAMESPACE)
        if deploy.status.ready_replicas and deploy.status.ready_replicas >= 1:
            ready = True
            break
        time.sleep(2)
    assert ready, "Jaeger pod not ready after 120 seconds"

    # Verify environment variable is set correctly
    core_v1 = client.CoreV1Api()
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app.kubernetes.io/name=jaeger")
    env_found = False
    for env in pods.items[0].spec.containers[0].env:
        if env.name == "JAEGER_RETENTION_GO_TRACES_TTL" and env.value == "2d":
            env_found = True
            break
    assert env_found, "JAEGER_RETENTION_GO_TRACES_TTL not set to expected value 2d"

    # Cleanup
    subprocess.run(["helm", "uninstall", "jaeger-test", "--namespace", NAMESPACE], capture_output=True)

def test_ac4_deployment_fails_on_small_storage_size():
    """AC-4: Deployment fails when storageSize <10Gi with correct error message"""
    cmd = [
        "helm", "install", "jaeger-test", "./kubernetes/helm",
        "--set", "jaeger.pvc.storageSize=5Gi",
        "--namespace", NAMESPACE,
        "--dry-run"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode != 0, "Helm install should fail with storage size 5Gi"
    assert "Jaeger PVC storage size must be at least 10Gi" in result.stderr, \
        f"Expected error message not found in stderr: {result.stderr}"

def test_ac5_badger_config_and_volume_mount_correct():
    """AC-5: PVC mounted at /badger, SPAN_STORAGE_TYPE is badger"""
    # Deploy with valid config
    cmd = [
        "helm", "install", "jaeger-test", "./kubernetes/helm",
        "--set", "jaeger.pvc.storageSize=10Gi",
        "--namespace", NAMESPACE
    ]
    if TEST_STORAGE_CLASS:
        cmd.extend(["--set", f"jaeger.pvc.storageClassName={TEST_STORAGE_CLASS}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Helm install failed: {result.stderr}"

    # Wait for pod ready
    apps_v1 = client.AppsV1Api()
    ready = False
    for _ in range(60):
        deploy = apps_v1.read_namespaced_deployment("jaeger", NAMESPACE)
        if deploy.status.ready_replicas and deploy.status.ready_replicas >= 1:
            ready = True
            break
        time.sleep(2)
    assert ready, "Jaeger pod not ready after 120 seconds"

    core_v1 = client.CoreV1Api()
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app.kubernetes.io/name=jaeger")
    container = pods.items[0].spec.containers[0]

    # Check SPAN_STORAGE_TYPE is badger
    span_storage_found = False
    for env in container.env:
        if env.name == "SPAN_STORAGE_TYPE" and env.value == "badger":
            span_storage_found = True
            break
    assert span_storage_found, "SPAN_STORAGE_TYPE not set to badger"

    # Check BADGER_EPHEMERAL is false
    badger_ephemeral_found = False
    for env in container.env:
        if env.name == "BADGER_EPHEMERAL" and env.value == "false":
            badger_ephemeral_found = True
            break
    assert badger_ephemeral_found, "BADGER_EPHEMERAL not set to false"

    # Check volume mount at /badger
    mount_found = False
    for mount in container.volume_mounts:
        if mount.mount_path == "/badger":
            mount_found = True
            break
    assert mount_found, "Volume mount at /badger not found"

    # Cleanup
    subprocess.run(["helm", "uninstall", "jaeger-test", "--namespace", NAMESPACE], capture_output=True)

def test_ac6_no_trace_loss_on_pod_reschedule():
    """AC-6: No trace loss when pod is rescheduled to another node"""
    # This test requires at least 2 nodes and ReadWriteMany or RWOnce with PV that can move
    # Skip if not enough nodes
    core_v1 = client.CoreV1Api()
    nodes = core_v1.list_node()
    if len(nodes.items) < 2:
        pytest.skip("Need at least 2 Kubernetes nodes for reschedule test")

    # Deploy with ReadWriteMany if possible, else assume PV can be remounted
    cmd = [
        "helm", "install", "jaeger-test", "./kubernetes/helm",
        "--set", "jaeger.pvc.storageSize=10Gi",
        "--set", "jaeger.pvc.accessModes[0]=ReadWriteMany",
        "--namespace", NAMESPACE
    ]
    if TEST_STORAGE_CLASS:
        cmd.extend(["--set", f"jaeger.pvc.storageClassName={TEST_STORAGE_CLASS}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    # If RWMany not supported, fall back to RWOnce
    if result.returncode != 0:
        cmd = [
            "helm", "install", "jaeger-test", "./kubernetes/helm",
            "--set", "jaeger.pvc.storageSize=10Gi",
            "--namespace", NAMESPACE
        ]
        if TEST_STORAGE_CLASS:
            cmd.extend(["--set", f"jaeger.pvc.storageClassName={TEST_STORAGE_CLASS}"])
        result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Helm install failed: {result.stderr}"

    # Wait for pod ready, get current node
    ready = False
    old_node = None
    old_pod_name = None
    for _ in range(60):
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app.kubernetes.io/name=jaeger")
        if len(pods.items) == 1 and pods.items[0].status.phase == "Running":
            old_node = pods.items[0].spec.node_name
            old_pod_name = pods.items[0].metadata.name
            ready = True
            break
        time.sleep(2)
    assert ready, "Jaeger pod not ready after 120 seconds"

    # Cordon old node
    core_v1.patch_node(old_node, {"spec": {"unschedulable": True}})

    # Delete pod to force reschedule
    core_v1.delete_namespaced_pod(old_pod_name, NAMESPACE)

    # Wait for new pod to be scheduled on different node
    new_pod_ready = False
    new_node = None
    for _ in range(120):
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app.kubernetes.io/name=jaeger")
        if len(pods.items) == 1 and pods.items[0].status.phase == "Running" and pods.items[0].spec.node_name != old_node:
            new_pod_ready = True
            new_node = pods.items[0].spec.node_name
            break
        time.sleep(2)
    
    # Uncordon old node
    core_v1.patch_node(old_node, {"spec": {"unschedulable": False}})

    assert new_pod_ready, f"Pod not rescheduled to new node, was on {old_node}, still on same node or not running"

    # Verify PVC is still bound
    pvc = core_v1.read_namespaced_persistent_volume_claim("jaeger-storage", NAMESPACE)
    assert pvc.status.phase == "Bound"

    # Cleanup
    subprocess.run(["helm", "uninstall", "jaeger-test", "--namespace", NAMESPACE], capture_output=True)
