#!/usr/bin/env python3
import os
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Load kubernetes config
if os.getenv("KUBERNETES_SERVICE_HOST"):
    config.load_incluster_config()
else:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
FLAGD_DEPLOYMENT_NAME = "flagd"
FLAGD_CONTAINER_NAME = "flagd"
FLAGD_PORT = 8013
HEALTH_ENDPOINT = "/healthz"

@pytest.fixture(scope="module")
def flagd_deployment():
    """Fixture to get the flagd deployment object"""
    try:
        deploy = apps_v1.read_namespaced_deployment(
            name=FLAGD_DEPLOYMENT_NAME,
            namespace=NAMESPACE
        )
        return deploy
    except ApiException as e:
        pytest.fail(f"Failed to get flagd deployment: {e}")

@pytest.fixture(scope="module")
def flagd_pod():
    """Fixture to get a running flagd pod"""
    try:
        pods = v1.list_namespaced_pod(
            namespace=NAMESPACE,
            label_selector=f"app={FLAGD_DEPLOYMENT_NAME}"
        )
        assert len(pods.items) > 0, "No flagd pods found"
        for pod in pods.items:
            if pod.status.phase == "Running":
                return pod
        pytest.fail("No running flagd pods found")
    except ApiException as e:
        pytest.fail(f"Failed to list flagd pods: {e}")

def test_ac1_probes_configured(flagd_deployment):
    """AC-1: Liveness and readiness probes are configured on the flagd container pointing to the /healthz endpoint on port 8013"""
    container = None
    for c in flagd_deployment.spec.template.spec.containers:
        if c.name == FLAGD_CONTAINER_NAME:
            container = c
            break
    assert container is not None, "flagd container not found in deployment"
    
    # Check liveness probe
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.http_get is not None, "Liveness probe is not HTTP GET"
    assert container.liveness_probe.http_get.path == HEALTH_ENDPOINT, f"Liveness probe path is not {HEALTH_ENDPOINT}"
    assert container.liveness_probe.http_get.port == FLAGD_PORT, f"Liveness probe port is not {FLAGD_PORT}"
    
    # Check readiness probe
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.http_get is not None, "Readiness probe is not HTTP GET"
    assert container.readiness_probe.http_get.path == HEALTH_ENDPOINT, f"Readiness probe path is not {HEALTH_ENDPOINT}"
    assert container.readiness_probe.http_get.port == FLAGD_PORT, f"Readiness probe port is not {FLAGD_PORT}"

def test_ac2_probe_scheme_matches_tls(flagd_deployment):
    """AC-2: When TLS is enabled for flagd service, probes automatically use https scheme; when TLS disabled, use http scheme"""
    container = None
    for c in flagd_deployment.spec.template.spec.containers:
        if c.name == FLAGD_CONTAINER_NAME:
            container = c
            break
    assert container is not None, "flagd container not found in deployment"
    
    # Check if TLS is enabled via env var
    tls_enabled = False
    for env in container.env:
        if env.name == "FLAGD_TLS_ENABLED" and env.value.lower() == "true":
            tls_enabled = True
            break
    
    expected_scheme = "HTTPS" if tls_enabled else "HTTP"
    assert container.liveness_probe.http_get.scheme == expected_scheme, f"Liveness probe scheme {container.liveness_probe.http_get.scheme} does not match expected {expected_scheme} (TLS enabled: {tls_enabled})"
    assert container.readiness_probe.http_get.scheme == expected_scheme, f"Readiness probe scheme {container.readiness_probe.http_get.scheme} does not match expected {expected_scheme} (TLS enabled: {tls_enabled})"

def test_ac3_resource_configuration(flagd_deployment):
    """AC-3: Flagd container resources are set to: CPU request = 100m, CPU limit = 500m, memory request = 128Mi, memory limit = 256Mi"""
    container = None
    for c in flagd_deployment.spec.template.spec.containers:
        if c.name == FLAGD_CONTAINER_NAME:
            container = c
            break
    assert container is not None, "flagd container not found in deployment"
    
    assert container.resources is not None, "Resources not configured for flagd container"
    assert container.resources.requests is not None, "Resource requests not configured"
    assert container.resources.limits is not None, "Resource limits not configured"
    
    assert container.resources.requests.get("cpu") == "100m", f"CPU request is {container.resources.requests.get('cpu')}, expected 100m"
    assert container.resources.limits.get("cpu") == "500m", f"CPU limit is {container.resources.limits.get('cpu')}, expected 500m"
    assert container.resources.requests.get("memory") == "128Mi", f"Memory request is {container.resources.requests.get('memory')}, expected 128Mi"
    assert container.resources.limits.get("memory") == "256Mi", f"Memory limit is {container.resources.limits.get('memory')}, expected 256Mi"

def test_ac4_pod_security_context(flagd_deployment):
    """AC-4: Pod security context is configured with runAsNonRoot: true, runAsUser: 1000, fsGroup: 1000"""
    pod_sc = flagd_deployment.spec.template.spec.security_context
    assert pod_sc is not None, "Pod security context not configured"
    assert pod_sc.run_as_non_root is True, "runAsNonRoot is not true"
    assert pod_sc.run_as_user == 1000, f"runAsUser is {pod_sc.run_as_user}, expected 1000"
    assert pod_sc.fs_group == 1000, f"fsGroup is {pod_sc.fs_group}, expected 1000"

def test_ac5_container_security_context(flagd_deployment):
    """AC-5: Flagd container security context is configured with readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, privileged: false, and all capabilities dropped"""
    container = None
    for c in flagd_deployment.spec.template.spec.containers:
        if c.name == FLAGD_CONTAINER_NAME:
            container = c
            break
    assert container is not None, "flagd container not found in deployment"
    
    container_sc = container.security_context
    assert container_sc is not None, "Container security context not configured"
    assert container_sc.read_only_root_filesystem is True, "readOnlyRootFilesystem is not true"
    assert container_sc.allow_privilege_escalation is False, "allowPrivilegeEscalation is not false"
    assert container_sc.privileged is False, "privileged is not false"
    assert container_sc.capabilities is not None, "Capabilities not configured"
    assert container_sc.capabilities.drop == ["ALL"], f"Capabilities drop is {container_sc.capabilities.drop}, expected ['ALL']"

def test_ac6_probes_return_200_after_startup(flagd_pod):
    """AC-6: When deployed to Kubernetes, liveness and readiness probes return 200 OK status within 30 seconds of pod startup"""
    start_time = time.time()
    success = False
    
    while time.time() - start_time < 30:
        try:
            # Execute probe check via pod exec
            # First check if TLS is enabled
            tls_enabled = False
            exec_cmd = ["printenv", "FLAGD_TLS_ENABLED"]
            resp = v1.connect_get_namespaced_pod_exec(
                flagd_pod.metadata.name,
                NAMESPACE,
                command=exec_cmd,
                container=FLAGD_CONTAINER_NAME,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False
            )
            if resp.strip().lower() == "true":
                tls_enabled = True
            
            scheme = "https" if tls_enabled else "http"
            curl_cmd = ["curl", "-k", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{scheme}://localhost:{FLAGD_PORT}{HEALTH_ENDPOINT}"]
            resp = v1.connect_get_namespaced_pod_exec(
                flagd_pod.metadata.name,
                NAMESPACE,
                command=curl_cmd,
                container=FLAGD_CONTAINER_NAME,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False
            )
            if resp.strip() == "200":
                success = True
                break
        except ApiException:
            pass
        time.sleep(2)
    
    assert success, "Probes did not return 200 OK within 30 seconds of startup"

def test_ac7_non_root_execution_and_readonly_fs(flagd_pod):
    """AC-7: Running flagd process executes as non-root user (UID 1000) and cannot write to the root filesystem"""
    # Check UID of running process
    exec_cmd = ["id", "-u"]
    resp = v1.connect_get_namespaced_pod_exec(
        flagd_pod.metadata.name,
        NAMESPACE,
        command=exec_cmd,
        container=FLAGD_CONTAINER_NAME,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    assert resp.strip() == "1000", f"Running as UID {resp.strip()}, expected 1000"
    
    # Check if can write to root filesystem
    exec_cmd = ["touch", "/test_write.txt"]
    try:
        v1.connect_get_namespaced_pod_exec(
            flagd_pod.metadata.name,
            NAMESPACE,
            command=exec_cmd,
            container=FLAGD_CONTAINER_NAME,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        write_success = True
    except ApiException:
        write_success = False
    
    assert write_success is False, "Was able to write to root filesystem, expected read-only"

def test_ac8_resource_limit_enforcement():
    """AC-8: Kubernetes enforces the configured CPU and memory limits, terminating the pod if resource consumption exceeds the defined thresholds"""
    # This test creates a temporary flagd pod with resource limits and triggers memory consumption over limit
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "flagd-resource-test",
            "labels": {
                "app": "flagd-test"
            }
        },
        "spec": {
            "containers": [{
                "name": "flagd",
                "image": "ghcr.io/open-feature/flagd:v0.6.0",
                "resources": {
                    "requests": {
                        "cpu": "100m",
                        "memory": "128Mi"
                    },
                    "limits": {
                        "cpu": "500m",
                        "memory": "256Mi"
                    }
                },
                "command": ["sh", "-c", "sleep 10 && dd if=/dev/zero of=/dev/null bs=300M count=1"]
            }],
            "restartPolicy": "Never"
        }
    }
    
    try:
        # Create test pod
        v1.create_namespaced_pod(NAMESPACE, pod_manifest)
        
        # Wait up to 2 minutes for pod to be terminated
        start_time = time.time()
        terminated = False
        termination_reason = ""
        
        while time.time() - start_time < 120:
            pod = v1.read_namespaced_pod("flagd-resource-test", NAMESPACE)
            if pod.status.phase == "Failed":
                terminated = True
                termination_reason = pod.status.container_statuses[0].state.terminated.reason
                break
            time.sleep(5)
        
        assert terminated is True, "Test pod was not terminated after exceeding memory limit"
        assert termination_reason == "OOMKilled", f"Pod terminated for reason {termination_reason}, expected OOMKilled"
    finally:
        # Clean up test pod
        try:
            v1.delete_namespaced_pod("flagd-resource-test", NAMESPACE)
        except ApiException:
            pass
