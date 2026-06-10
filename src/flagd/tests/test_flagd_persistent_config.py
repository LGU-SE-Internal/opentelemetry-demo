import os
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

@pytest.fixture(scope="module")
def k8s_client():
    config.load_kube_config()
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    return v1, apps_v1

@pytest.fixture(scope="module")
def flagd_namespace():
    return os.getenv("FLAGD_NAMESPACE", "default")

def test_ac1_pvc_exists(k8s_client, flagd_namespace):
    v1, _ = k8s_client
    try:
        pvc = v1.read_namespaced_persistent_volume_claim(
            name="flagd-custom-config",
            namespace=flagd_namespace
        )
        assert pvc is not None
        assert pvc.spec.access_modes == ["ReadWriteOnce"]
        assert pvc.spec.resources.requests["storage"] == "1Gi"
    except ApiException as e:
        pytest.fail(f"PVC flagd-custom-config not found: {e}")

def test_ac4_non_root_user_has_permissions(k8s_client, flagd_namespace):
    v1, _ = k8s_client
    pods = v1.list_namespaced_pod(
        namespace=flagd_namespace,
        label_selector="app=flagd"
    )
    assert len(pods.items) > 0, "No flagd pods found"
    pod_name = pods.items[0].metadata.name
    
    exec_command = [
        "/bin/sh",
        "-c",
        "su -s /bin/sh 1001 -c 'touch /var/lib/flagd/config/test_perm && rm /var/lib/flagd/config/test_perm && echo OK'"
    ]
    
    from kubernetes.stream import stream
    resp = stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        flagd_namespace,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert "OK" in resp, "Non-root user 1001 does not have write permissions on /var/lib/flagd/config"

def test_ac3_custom_env_var_path_used(k8s_client, flagd_namespace):
    _, apps_v1 = k8s_client
    deploy = apps_v1.read_namespaced_deployment(
        name="flagd",
        namespace=flagd_namespace
    )
    
    env_vars = {e.name: e.value for e in deploy.spec.template.spec.containers[0].env}
    assert "FLAGD_CUSTOM_CONFIG_PATH" in env_vars
    assert env_vars["FLAGD_CUSTOM_CONFIG_PATH"] == "/var/lib/flagd/config/flags.json"

def test_ac5_fallback_to_default_config_when_no_custom_file(k8s_client, flagd_namespace):
    v1, _ = k8s_client
    pods = v1.list_namespaced_pod(
        namespace=flagd_namespace,
        label_selector="app=flagd"
    )
    pod_name = pods.items[0].metadata.name
    
    # Get logs to check fallback message
    logs = v1.read_namespaced_pod_log(pod_name, flagd_namespace)
    assert "No custom flag configuration found" in logs or "Using default config" in logs
    
    # Check flagd is running
    assert v1.read_namespaced_pod_status(pod_name, flagd_namespace).status.phase == "Running"

def test_ac6_fallback_to_default_when_custom_file_unreadable(k8s_client, flagd_namespace):
    v1, _ = k8s_client
    pods = v1.list_namespaced_pod(
        namespace=flagd_namespace,
        label_selector="app=flagd"
    )
    pod_name = pods.items[0].metadata.name
    
    # Create unreadable custom file
    exec_commands = [
        ["touch", "/var/lib/flagd/config/flags.json"],
        ["chmod", "000", "/var/lib/flagd/config/flags.json"]
    ]
    
    from kubernetes.stream import stream
    for cmd in exec_commands:
        stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            flagd_namespace,
            command=cmd,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
    
    # Delete pod to restart it
    v1.delete_namespaced_pod(pod_name, flagd_namespace)
    
    # Wait for new pod to start
    time.sleep(30)
    new_pods = v1.list_namespaced_pod(
        namespace=flagd_namespace,
        label_selector="app=flagd"
    )
    new_pod_name = new_pods.items[0].metadata.name
    
    # Check logs for warning and fallback
    logs = v1.read_namespaced_pod_log(new_pod_name, flagd_namespace)
    assert "WARNING: Custom flag configuration file" in logs
    assert "exists but is not readable. Falling back to default config." in logs
    assert v1.read_namespaced_pod_status(new_pod_name, flagd_namespace).status.phase == "Running"
    
    # Clean up
    cleanup_cmd = ["/bin/sh", "-c", "rm -f /var/lib/flagd/config/flags.json"]
    stream(
        v1.connect_get_namespaced_pod_exec,
        new_pod_name,
        flagd_namespace,
        command=cleanup_cmd,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )

def test_ac2_custom_flags_survive_pod_restart(k8s_client, flagd_namespace):
    v1, _ = k8s_client
    pods = v1.list_namespaced_pod(
        namespace=flagd_namespace,
        label_selector="app=flagd"
    )
    pod_name = pods.items[0].metadata.name
    
    # Write custom flag file
    custom_config = '{"flags": {"testPersistentFlag": {"state": "ENABLED", "defaultVariant": "on", "variants": {"on": true, "off": false}}}}'
    exec_command = [
        "/bin/sh",
        "-c",
        f"echo '{custom_config}' > /var/lib/flagd/config/flags.json && chown 1001:1001 /var/lib/flagd/config/flags.json"
    ]
    
    from kubernetes.stream import stream
    stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        flagd_namespace,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    # Delete original pod
    v1.delete_namespaced_pod(pod_name, flagd_namespace)
    
    # Wait for new pod to start
    time.sleep(30)
    new_pods = v1.list_namespaced_pod(
        namespace=flagd_namespace,
        label_selector="app=flagd"
    )
    new_pod_name = new_pods.items[0].metadata.name
    
    # Check logs confirm custom config is loaded
    logs = v1.read_namespaced_pod_log(new_pod_name, flagd_namespace)
    assert "Using custom flag configuration from /var/lib/flagd/config/flags.json" in logs
    
    # Verify custom flag exists
    verify_cmd = [
        "/bin/sh",
        "-c",
        "wget -qO- http://localhost:8014/flags | grep -q 'testPersistentFlag' && echo OK"
    ]
    resp = stream(
        v1.connect_get_namespaced_pod_exec,
        new_pod_name,
        flagd_namespace,
        command=verify_cmd,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert "OK" in resp, "Custom flag not found after pod restart"
    
    # Clean up
    cleanup_cmd = ["/bin/sh", "-c", "rm -f /var/lib/flagd/config/flags.json"]
    stream(
        v1.connect_get_namespaced_pod_exec,
        new_pod_name,
        flagd_namespace,
        command=cleanup_cmd,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
