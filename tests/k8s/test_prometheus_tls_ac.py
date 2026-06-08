#!/usr/bin/env python3
import pytest
import subprocess
import os
import time
import requests
from kubernetes import client, config
from kubernetes.stream import stream

# Load kubernetes config
try:
    config.load_kube_config()
except:
    config.load_incluster_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

PROMETHEUS_DEPLOYMENT_NAME = "prometheus"
PROMETHEUS_LABEL_SELECTOR = "app.kubernetes.io/name=prometheus"
NAMESPACE = "monitoring"
EXPECTED_PORT = 9090
TLS_MOUNT_PATH = "/etc/prometheus/tls"
WEB_CONFIG_PATH = "/etc/prometheus/web-config.yaml"


def get_prometheus_deployment():
    """Helper to get prometheus deployment object"""
    return apps_v1.read_namespaced_deployment(name=PROMETHEUS_DEPLOYMENT_NAME, namespace=NAMESPACE)


def get_prometheus_pod():
    """Helper to get running prometheus pod"""
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=PROMETHEUS_LABEL_SELECTOR)
    for pod in pods.items:
        if pod.status.phase == "Running":
            return pod
    return None


def wait_for_pod_status(desired_phase="Running", timeout=120):
    """Wait for pod to reach desired phase or timeout"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        pod = get_prometheus_pod()
        if pod and pod.status.phase == desired_phase:
            return pod
        time.sleep(5)
    return None


def exec_in_pod(pod, cmd):
    """Execute command in prometheus pod"""
    return stream(
        v1.connect_get_namespaced_pod_exec,
        pod.metadata.name,
        NAMESPACE,
        command=cmd,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )


def test_ac1_tls_disabled_uses_http():
    """AC-1: When PROMETHEUS_TLS_ENABLED is unset or false, Prometheus serves endpoints over unencrypted HTTP on port 9090"""
    deployment = get_prometheus_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    # Check TLS env var defaults to false if not set
    tls_enabled = None
    for env in container.env:
        if env.name == "PROMETHEUS_TLS_ENABLED":
            tls_enabled = env.value.lower() == "true"
            break
    assert tls_enabled is False or tls_enabled is None, "PROMETHEUS_TLS_ENABLED should default to false"
    
    # Check web.config.file flag is not present when TLS disabled
    assert "--web.config.file" not in container.args, "web.config.file should not be set when TLS is disabled"
    
    # Verify HTTP access works
    pod = wait_for_pod_status()
    assert pod is not None, "Prometheus pod failed to start with TLS disabled"
    
    # Port forward and test HTTP request
    pf = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod.metadata.name}", f"9090:{EXPECTED_PORT}", "-n", NAMESPACE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    
    try:
        resp = requests.get("http://localhost:9090/-/healthy", verify=False, timeout=5)
        assert resp.status_code == 200, "HTTP request should succeed when TLS is disabled"
    finally:
        pf.terminate()
        pf.wait()


def test_ac2_tls_enabled_uses_https_only():
    """AC-2: When PROMETHEUS_TLS_ENABLED=true and valid cert/key present, all endpoints are only accessible via HTTPS"""
    # This test requires TLS enabled deployment configuration
    deployment = get_prometheus_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    # Check TLS enabled env var
    tls_enabled = False
    for env in container.env:
        if env.name == "PROMETHEUS_TLS_ENABLED":
            tls_enabled = env.value.lower() == "true"
            break
    assert tls_enabled is True, "PROMETHEUS_TLS_ENABLED should be true for this test"
    
    # Check web.config.file flag is present
    assert "--web.config.file" in str(container.args), "web.config.file should be set when TLS is enabled"
    
    pod = wait_for_pod_status()
    assert pod is not None, "Prometheus pod failed to start with TLS enabled"
    
    # Port forward and test HTTP request is rejected
    pf = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod.metadata.name}", f"9090:{EXPECTED_PORT}", "-n", NAMESPACE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    
    try:
        # HTTP request should fail
        with pytest.raises((requests.exceptions.ConnectionError, requests.exceptions.SSLError)):
            requests.get("http://localhost:9090/-/healthy", timeout=5)
        
        # HTTPS request should succeed (ignore self-signed cert for test)
        resp = requests.get("https://localhost:9090/-/healthy", verify=False, timeout=5)
        assert resp.status_code == 200, "HTTPS request should succeed when TLS is enabled"
    finally:
        pf.terminate()
        pf.wait()


def test_ac3_tls_enabled_missing_cert_fails_start():
    """AC-3: When PROMETHEUS_TLS_ENABLED=true but cert/key files missing, Prometheus fails to start with explicit error"""
    pod = get_prometheus_pod()
    assert pod is None or pod.status.phase != "Running", "Prometheus should not start when cert/key are missing"
    
    # Check pod events for error message
    events = v1.list_namespaced_event(NAMESPACE, field_selector=f"involvedObject.name={pod.metadata.name}" if pod else "")
    error_found = False
    for event in events.items:
        if "TLS certificate or key not found at configured path" in event.message:
            error_found = True
            break
    assert error_found is True, "Missing TLS cert error message not found in pod events"


def test_ac4_tls_secret_mounted_correctly():
    """AC-4: When prometheus.tls.existingSecret is set in Helm values, secret contents are mounted at /etc/prometheus/tls"""
    deployment = get_prometheus_deployment()
    volumes = deployment.spec.template.spec.volumes
    volume_mounts = deployment.spec.template.spec.containers[0].volume_mounts
    
    # Check TLS secret volume exists
    tls_volume = None
    for vol in volumes:
        if vol.secret and vol.secret.secret_name:
            tls_volume = vol
            break
    assert tls_volume is not None, "TLS secret volume not found in deployment volumes"
    
    # Check volume mount exists at correct path
    tls_mount = None
    for mount in volume_mounts:
        if mount.mount_path == TLS_MOUNT_PATH:
            tls_mount = mount
            break
    assert tls_mount is not None, f"TLS secret not mounted at {TLS_MOUNT_PATH}"
    assert tls_mount.read_only is True, "TLS mount must be read-only"
    
    # Verify files exist in mount path
    pod = wait_for_pod_status()
    assert pod is not None, "Prometheus pod failed to start with TLS secret mounted"
    
    cert_exists = exec_in_pod(pod, ["test", "-f", "/etc/prometheus/tls/tls.crt"])
    key_exists = exec_in_pod(pod, ["test", "-f", "/etc/prometheus/tls/tls.key"])
    assert cert_exists == "", "tls.crt not found in mount path"
    assert key_exists == "", "tls.key not found in mount path"


def test_ac5_mtls_enabled_requires_client_cert():
    """AC-5: When PROMETHEUS_MTLS_ENABLED=true and valid CA present, requests without valid client cert return 403"""
    deployment = get_prometheus_deployment()
    container = deployment.spec.template.spec.containers[0]
    
    # Check mTLS enabled env var
    mtls_enabled = False
    for env in container.env:
        if env.name == "PROMETHEUS_MTLS_ENABLED":
            mtls_enabled = env.value.lower() == "true"
            break
    assert mtls_enabled is True, "PROMETHEUS_MTLS_ENABLED should be true for this test"
    
    pod = wait_for_pod_status()
    assert pod is not None, "Prometheus pod failed to start with mTLS enabled"
    
    # Port forward and test requests
    pf = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod.metadata.name}", f"9090:{EXPECTED_PORT}", "-n", NAMESPACE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    
    try:
        # Request without client cert should return 403
        with pytest.raises(requests.exceptions.SSLError):
            requests.get("https://localhost:9090/-/healthy", verify=False, timeout=5)
        
        # Request with valid client cert should succeed
        # (Test assumes client cert/key present in test environment)
        resp = requests.get(
            "https://localhost:9090/-/healthy",
            verify="/tmp/ca.crt",
            cert=("/tmp/client.crt", "/tmp/client.key"),
            timeout=5
        )
        assert resp.status_code == 200, "Request with valid client cert should succeed"
        
        # Request with invalid client cert should return 403
        resp = requests.get(
            "https://localhost:9090/-/healthy",
            verify="/tmp/ca.crt",
            cert=("/tmp/invalid-client.crt", "/tmp/invalid-client.key"),
            timeout=5
        )
        assert resp.status_code == 403, "Request with invalid client cert should return 403"
    finally:
        pf.terminate()
        pf.wait()


def test_ac6_mtls_enabled_missing_ca_fails_start():
    """AC-6: When PROMETHEUS_MTLS_ENABLED=true but CA certificate missing, Prometheus fails to start with explicit error"""
    pod = get_prometheus_pod()
    assert pod is None or pod.status.phase != "Running", "Prometheus should not start when CA cert is missing for mTLS"
    
    # Check pod events for error message
    events = v1.list_namespaced_event(NAMESPACE, field_selector=f"involvedObject.name={pod.metadata.name}" if pod else "")
    error_found = False
    for event in events.items:
        if "CA certificate required for mTLS not found at configured path" in event.message:
            error_found = True
            break
    assert error_found is True, "Missing CA cert error message not found in pod events"
