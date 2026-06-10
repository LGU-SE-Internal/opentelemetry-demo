#!/usr/bin/env python3
"""
Integration tests for flagd persistent custom config feature (issue #1903)
Tests cover all acceptance criteria from the approved spec.
"""
import os
import time
import kubernetes
from kubernetes.client import CoreV1Api, AppsV1Api
from kubernetes.stream import stream
import pytest

# Constants from spec
PVC_NAME = "flagd-custom-config"
FLAGD_NAMESPACE = os.environ.get("FLAGD_NAMESPACE", "default")
DEFAULT_MOUNT_PATH = "/var/lib/flagd/config"
DEFAULT_CUSTOM_CONFIG_PATH = f"{DEFAULT_MOUNT_PATH}/flags.json"
DEFAULT_BAKED_CONFIG_PATH = "/etc/flagd/demo_flags.json"
FLAGD_UID = 1001
FLAGD_GID = 1001
DEFAULT_TEST_FLAG_CONTENT = """{
  "flags": {
    "test-persistent-flag": {
      "state": "ENABLED",
      "variants": {
        "on": true,
        "off": false
      },
      "defaultVariant": "on"
    }
  }
}"""

@pytest.fixture(scope="module")
def k8s_clients():
    kubernetes.config.load_kube_config()
    core_api = CoreV1Api()
    apps_api = AppsV1Api()
    return core_api, apps_api

@pytest.fixture(scope="module")
def flagd_pod_name(k8s_clients):
    core_api, _ = k8s_clients
    pods = core_api.list_namespaced_pod(
        namespace=FLAGD_NAMESPACE,
        label_selector="app.kubernetes.io/name=flagd"
    )
    assert len(pods.items) > 0, "No flagd pods found"
    return pods.items[0].metadata.name

def exec_in_pod(core_api, pod_name, cmd, namespace=FLAGD_NAMESPACE, user=None):
    exec_command = ["/bin/sh", "-c", cmd]
    kwargs = {"command": exec_command, "stdin": True, "stdout": True, "stderr": True, "tty": False}
    if user:
        kwargs["preload_content"] = False
        kwargs["query_params"] = {"user": str(user)}
    
    resp = stream(core_api.connect_get_namespaced_pod_exec, pod_name, namespace, **kwargs)
    if user:
        resp.run_forever(timeout=10)
        stdout = resp.read_stdout()
        stderr = resp.read_stderr()
        return resp.returncode, stdout, stderr
    return resp

def test_ac1_pvc_exists(k8s_clients):
    """AC-1: Verify PVC named flagd-custom-config exists in flagd namespace"""
    core_api, _ = k8s_clients
    pvc = core_api.read_namespaced_persistent_volume_claim(
        name=PVC_NAME,
        namespace=FLAGD_NAMESPACE
    )
    assert pvc is not None
    assert pvc.metadata.name == PVC_NAME
    assert pvc.spec.access_modes == ["ReadWriteOnce"]
    assert pvc.spec.resources.requests["storage"] == "1Gi"

def test_ac2_custom_flags_survive_pod_restart(k8s_clients, flagd_pod_name):
    """AC-2: Verify custom flags persist after pod deletion and recreation"""
    core_api, apps_api = k8s_clients

    # Step 1: Write custom flags to persistent path
    exec_command = f"echo '{DEFAULT_TEST_FLAG_CONTENT}' > {DEFAULT_CUSTOM_CONFIG_PATH}"
    exec_in_pod(core_api, flagd_pod_name, exec_command, user=0)

    # Verify file was written
    _, stdout, _ = exec_in_pod(core_api, flagd_pod_name, f"cat {DEFAULT_CUSTOM_CONFIG_PATH}", user=FLAGD_UID)
    assert "test-persistent-flag" in stdout

    # Step 2: Delete the pod
    core_api.delete_namespaced_pod(name=flagd_pod_name, namespace=FLAGD_NAMESPACE)

    # Step 3: Wait for new pod to start
    time.sleep(30)
    new_pods = core_api.list_namespaced_pod(
        namespace=FLAGD_NAMESPACE,
        label_selector="app.kubernetes.io/name=flagd",
        field_selector="status.phase=Running"
    )
    while len(new_pods.items) == 0:
        time.sleep(10)
        new_pods = core_api.list_namespaced_pod(
            namespace=FLAGD_NAMESPACE,
            label_selector="app.kubernetes.io/name=flagd",
            field_selector="status.phase=Running"
        )
    new_pod_name = new_pods.items[0].metadata.name

    # Step 4: Verify custom flag file exists in new pod and is loaded
    _, stdout, _ = exec_in_pod(core_api, new_pod_name, f"cat {DEFAULT_CUSTOM_CONFIG_PATH}", user=FLAGD_UID)
    assert "test-persistent-flag" in stdout

    # Verify flagd loaded the custom config
    _, stdout, _ = exec_in_pod(core_api, new_pod_name, "ps aux | grep flagd", user=FLAGD_UID)
    assert DEFAULT_CUSTOM_CONFIG_PATH in stdout

def test_ac3_custom_env_var_path_used(k8s_clients, flagd_pod_name):
    """AC-3: Verify FLAGD_CUSTOM_CONFIG_PATH env var specifies custom flag path"""
    core_api, apps_api = k8s_clients
    custom_path = f"{DEFAULT_MOUNT_PATH}/my_custom_flags.json"

    # Write test file to custom path
    exec_in_pod(core_api, flagd_pod_name, f"echo '{DEFAULT_TEST_FLAG_CONTENT}' > {custom_path}", user=0)
    exec_in_pod(core_api, flagd_pod_name, f"chown {FLAGD_UID}:{FLAGD_GID} {custom_path}", user=0)

    # Update deployment with custom env var
    deployment = apps_api.read_namespaced_deployment(name="flagd", namespace=FLAGD_NAMESPACE)
    original_env = deployment.spec.template.spec.containers[0].env.copy()
    env_found = False
    for env in deployment.spec.template.spec.containers[0].env:
        if env.name == "FLAGD_CUSTOM_CONFIG_PATH":
            env.value = custom_path
            env_found = True
    if not env_found:
        deployment.spec.template.spec.containers[0].env.append(
            kubernetes.client.V1EnvVar(name="FLAGD_CUSTOM_CONFIG_PATH", value=custom_path)
        )
    apps_api.patch_namespaced_deployment(name="flagd", namespace=FLAGD_NAMESPACE, body=deployment)

    # Wait for rollout
    time.sleep(40)
    new_pods = core_api.list_namespaced_pod(
        namespace=FLAGD_NAMESPACE,
        label_selector="app.kubernetes.io/name=flagd",
        field_selector="status.phase=Running"
    )
    while len(new_pods.items) == 0:
        time.sleep(10)
        new_pods = core_api.list_namespaced_pod(
            namespace=FLAGD_NAMESPACE,
            label_selector="app.kubernetes.io/name=flagd",
            field_selector="status.phase=Running"
        )
    new_pod_name = new_pods.items[0].metadata.name

    # Verify flagd uses custom path
    _, stdout, _ = exec_in_pod(core_api, new_pod_name, "ps aux | grep flagd", user=FLAGD_UID)
    assert custom_path in stdout

    # Revert env var change
    deployment = apps_api.read_namespaced_deployment(name="flagd", namespace=FLAGD_NAMESPACE)
    deployment.spec.template.spec.containers[0].env = original_env
    apps_api.patch_namespaced_deployment(name="flagd", namespace=FLAGD_NAMESPACE, body=deployment)

def test_ac4_non_root_user_has_permissions(k8s_clients, flagd_pod_name):
    """AC-4: Verify non-root flagd user (UID 1001) has rw permissions on /var/lib/flagd/config"""
    core_api, _ = k8s_clients

    # Test write permission
    test_file = f"{DEFAULT_MOUNT_PATH}/test_permission.txt"
    returncode, _, stderr = exec_in_pod(
        core_api, flagd_pod_name, f"touch {test_file} && echo 'test' > {test_file}",
        user=FLAGD_UID
    )
    assert returncode == 0, f"Write failed: {stderr}"

    # Test read permission
    returncode, stdout, stderr = exec_in_pod(
        core_api, flagd_pod_name, f"cat {test_file}", user=FLAGD_UID
    )
    assert returncode == 0, f"Read failed: {stderr}"
    assert "test" in stdout

    # Verify ownership of directory
    returncode, stdout, stderr = exec_in_pod(
        core_api, flagd_pod_name, f"ls -ld {DEFAULT_MOUNT_PATH}", user=0
    )
    assert f"{FLAGD_UID} {FLAGD_GID}" in stdout

def test_ac5_fallback_to_default_config_when_no_custom_file(k8s_clients, flagd_pod_name):
    """AC-5: Verify default baked config loads when no custom config exists"""
    core_api, _ = k8s_clients

    # Remove any existing custom config
    exec_in_pod(core_api, flagd_pod_name, f"rm -f {DEFAULT_CUSTOM_CONFIG_PATH}", user=0)

    # Restart flagd process
    exec_in_pod(core_api, flagd_pod_name, "pkill flagd", user=0)
    time.sleep(10)

    # Verify flagd uses default config path
    _, stdout, _ = exec_in_pod(core_api, flagd_pod_name, "ps aux | grep flagd", user=FLAGD_UID)
    assert DEFAULT_BAKED_CONFIG_PATH in stdout
    assert DEFAULT_CUSTOM_CONFIG_PATH not in stdout

def test_ac6_fallback_to_default_when_custom_file_unreadable(k8s_clients, flagd_pod_name):
    """AC-6: Verify fallback to default when custom config has bad permissions, no crash"""
    core_api, _ = k8s_clients

    # Create custom config with root-only permissions
    exec_in_pod(core_api, flagd_pod_name, f"echo '{DEFAULT_TEST_FLAG_CONTENT}' > {DEFAULT_CUSTOM_CONFIG_PATH}", user=0)
    exec_in_pod(core_api, flagd_pod_name, f"chmod 0600 {DEFAULT_CUSTOM_CONFIG_PATH}", user=0)

    # Restart flagd
    exec_in_pod(core_api, flagd_pod_name, "pkill flagd", user=0)
    time.sleep(10)

    # Verify pod is still running
    pod = core_api.read_namespaced_pod(name=flagd_pod_name, namespace=FLAGD_NAMESPACE)
    assert pod.status.phase == "Running"

    # Verify flagd uses default config
    _, stdout, _ = exec_in_pod(core_api, flagd_pod_name, "ps aux | grep flagd", user=FLAGD_UID)
    assert DEFAULT_BAKED_CONFIG_PATH in stdout

    # Verify warning log exists
    _, stdout, _ = exec_in_pod(core_api, flagd_pod_name, "cat /var/log/flagd/entrypoint.log 2>/dev/null || journalctl -u flagd 2>/dev/null || dmesg | grep flagd", user=0)
    assert "warning" in stdout.lower() or "permission denied" in stdout.lower()

    # Clean up
    exec_in_pod(core_api, flagd_pod_name, f"rm -f {DEFAULT_CUSTOM_CONFIG_PATH}", user=0)
