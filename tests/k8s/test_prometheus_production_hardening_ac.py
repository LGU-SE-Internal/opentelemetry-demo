#!/usr/bin/env python3
"""Integration tests for Prometheus Production Hardening ACs (issue #1599)"""
import os
import yaml
import time
from kubernetes import client, config

PROMETHEUS_DEPLOYMENT_PATH = "./k8s/prometheus-deployment.yaml"
EXPECTED_DEPLOYMENT_NAME = "prometheus"
EXPECTED_CONTAINER_NAME = "prometheus"
EXPECTED_PORT = 9090
EXPECTED_LIVENESS_PROBE_PATH = "/-/healthy"
EXPECTED_READINESS_PROBE_PATH = "/-/ready"

# Default values from spec
DEFAULT_CPU_REQUEST = "100m"
DEFAULT_CPU_LIMIT = "500m"
DEFAULT_MEMORY_REQUEST = "256Mi"
DEFAULT_MEMORY_LIMIT = "1Gi"
DEFAULT_RUN_AS_USER = 1000
DEFAULT_LIVENESS_INITIAL_DELAY = 30
DEFAULT_LIVENESS_PERIOD = 10
DEFAULT_LIVENESS_TIMEOUT = 5
DEFAULT_READINESS_INITIAL_DELAY = 10
DEFAULT_READINESS_PERIOD = 5
DEFAULT_READINESS_TIMEOUT = 3


def test_ac1_liveness_probe_configured():
    """AC-1: Liveness probe points to /-/healthy endpoint on port 9090 with default values"""
    assert os.path.exists(PROMETHEUS_DEPLOYMENT_PATH), f"Prometheus Deployment manifest not found at {PROMETHEUS_DEPLOYMENT_PATH}"
    
    with open(PROMETHEUS_DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert manifest.get("kind") == "Deployment", "Manifest is not a Deployment"
    assert "spec" in manifest, "Deployment has no spec section"
    assert "template" in manifest["spec"], "Deployment has no pod template"
    assert "spec" in manifest["spec"]["template"], "Pod template has no spec"
    assert "containers" in manifest["spec"]["template"]["spec"], "Pod template has no containers"
    
    prom_container = next((c for c in manifest["spec"]["template"]["spec"]["containers"] 
                          if c["name"] == EXPECTED_CONTAINER_NAME), None)
    assert prom_container is not None, f"Container {EXPECTED_CONTAINER_NAME} not found in deployment"
    
    assert "livenessProbe" in prom_container, "Prometheus container missing livenessProbe"
    liveness_probe = prom_container["livenessProbe"]
    
    assert "httpGet" in liveness_probe, "Liveness probe is not HTTP GET type"
    assert liveness_probe["httpGet"]["path"] == EXPECTED_LIVENESS_PROBE_PATH, f"Expected liveness probe path {EXPECTED_LIVENESS_PROBE_PATH}, got {liveness_probe['httpGet']['path']}"
    assert liveness_probe["httpGet"]["port"] == EXPECTED_PORT, f"Expected liveness probe port {EXPECTED_PORT}, got {liveness_probe['httpGet']['port']}"
    assert liveness_probe.get("initialDelaySeconds") == DEFAULT_LIVENESS_INITIAL_DELAY, f"Expected liveness initialDelaySeconds {DEFAULT_LIVENESS_INITIAL_DELAY}, got {liveness_probe.get('initialDelaySeconds')}"
    assert liveness_probe.get("periodSeconds") == DEFAULT_LIVENESS_PERIOD, f"Expected liveness periodSeconds {DEFAULT_LIVENESS_PERIOD}, got {liveness_probe.get('periodSeconds')}"
    assert liveness_probe.get("timeoutSeconds") == DEFAULT_LIVENESS_TIMEOUT, f"Expected liveness timeoutSeconds {DEFAULT_LIVENESS_TIMEOUT}, got {liveness_probe.get('timeoutSeconds')}"


def test_ac2_readiness_probe_configured():
    """AC-2: Readiness probe points to /-/ready endpoint on port 9090 with default values"""
    with open(PROMETHEUS_DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    prom_container = next((c for c in manifest["spec"]["template"]["spec"]["containers"] 
                          if c["name"] == EXPECTED_CONTAINER_NAME), None)
    assert prom_container is not None, f"Container {EXPECTED_CONTAINER_NAME} not found in deployment"
    
    assert "readinessProbe" in prom_container, "Prometheus container missing readinessProbe"
    readiness_probe = prom_container["readinessProbe"]
    
    assert "httpGet" in readiness_probe, "Readiness probe is not HTTP GET type"
    assert readiness_probe["httpGet"]["path"] == EXPECTED_READINESS_PROBE_PATH, f"Expected readiness probe path {EXPECTED_READINESS_PROBE_PATH}, got {readiness_probe['httpGet']['path']}"
    assert readiness_probe["httpGet"]["port"] == EXPECTED_PORT, f"Expected readiness probe port {EXPECTED_PORT}, got {readiness_probe['httpGet']['port']}"
    assert readiness_probe.get("initialDelaySeconds") == DEFAULT_READINESS_INITIAL_DELAY, f"Expected readiness initialDelaySeconds {DEFAULT_READINESS_INITIAL_DELAY}, got {readiness_probe.get('initialDelaySeconds')}"
    assert readiness_probe.get("periodSeconds") == DEFAULT_READINESS_PERIOD, f"Expected readiness periodSeconds {DEFAULT_READINESS_PERIOD}, got {readiness_probe.get('periodSeconds')}"
    assert readiness_probe.get("timeoutSeconds") == DEFAULT_READINESS_TIMEOUT, f"Expected readiness timeoutSeconds {DEFAULT_READINESS_TIMEOUT}, got {readiness_probe.get('timeoutSeconds')}"


def test_ac3_default_resource_requirements():
    """AC-3: Default resource requirements are set correctly when no custom env vars are provided"""
    with open(PROMETHEUS_DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    prom_container = next((c for c in manifest["spec"]["template"]["spec"]["containers"] 
                          if c["name"] == EXPECTED_CONTAINER_NAME), None)
    assert prom_container is not None, f"Container {EXPECTED_CONTAINER_NAME} not found in deployment"
    
    assert "resources" in prom_container, "Prometheus container missing resources section"
    resources = prom_container["resources"]
    
    assert "requests" in resources, "Resources missing requests section"
    assert "limits" in resources, "Resources missing limits section"
    
    assert resources["requests"].get("cpu") == DEFAULT_CPU_REQUEST, f"Expected default CPU request {DEFAULT_CPU_REQUEST}, got {resources['requests'].get('cpu')}"
    assert resources["limits"].get("cpu") == DEFAULT_CPU_LIMIT, f"Expected default CPU limit {DEFAULT_CPU_LIMIT}, got {resources['limits'].get('cpu')}"
    assert resources["requests"].get("memory") == DEFAULT_MEMORY_REQUEST, f"Expected default memory request {DEFAULT_MEMORY_REQUEST}, got {resources['requests'].get('memory')}"
    assert resources["limits"].get("memory") == DEFAULT_MEMORY_LIMIT, f"Expected default memory limit {DEFAULT_MEMORY_LIMIT}, got {resources['limits'].get('memory')}"


def test_ac4_custom_resource_values_from_env():
    """AC-4: Custom resource values from environment variables are applied correctly"""
    # Test with overridden env vars
    os.environ["PROMETHEUS_CPU_REQUEST"] = "200m"
    os.environ["PROMETHEUS_MEMORY_LIMIT"] = "2Gi"
    
    # Render/process the deployment manifest (assuming it uses envsubst or similar parameterization)
    with open(PROMETHEUS_DEPLOYMENT_PATH, "r") as f:
        manifest_content = f.read()
    
    # Replace env vars in content
    for key, value in os.environ.items():
        if key.startswith("PROMETHEUS_"):
            manifest_content = manifest_content.replace(f"${key}", value)
            manifest_content = manifest_content.replace(f"${{{key}}}", value)
    
    manifest = yaml.safe_load(manifest_content)
    
    prom_container = next((c for c in manifest["spec"]["template"]["spec"]["containers"] 
                          if c["name"] == EXPECTED_CONTAINER_NAME), None)
    assert prom_container is not None, f"Container {EXPECTED_CONTAINER_NAME} not found in deployment"
    
    resources = prom_container["resources"]
    assert resources["requests"]["cpu"] == "200m", f"Expected custom CPU request 200m, got {resources['requests']['cpu']}"
    assert resources["limits"]["memory"] == "2Gi", f"Expected custom memory limit 2Gi, got {resources['limits']['memory']}"
    
    # Cleanup env vars
    del os.environ["PROMETHEUS_CPU_REQUEST"]
    del os.environ["PROMETHEUS_MEMORY_LIMIT"]


def test_ac5_security_context_configured():
    """AC-5: Security context fields are set unconditionally: runAsNonRoot=true, readOnlyRootFilesystem=true, capabilities.drop=["ALL"]"""
    with open(PROMETHEUS_DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    prom_container = next((c for c in manifest["spec"]["template"]["spec"]["containers"] 
                          if c["name"] == EXPECTED_CONTAINER_NAME), None)
    assert prom_container is not None, f"Container {EXPECTED_CONTAINER_NAME} not found in deployment"
    
    assert "securityContext" in prom_container, "Prometheus container missing securityContext"
    security_context = prom_container["securityContext"]
    
    assert security_context.get("runAsNonRoot") == True, "Expected runAsNonRoot to be true"
    assert security_context.get("readOnlyRootFilesystem") == True, "Expected readOnlyRootFilesystem to be true"
    assert "capabilities" in security_context, "Security context missing capabilities section"
    assert "drop" in security_context["capabilities"], "Capabilities missing drop list"
    assert "ALL" in security_context["capabilities"]["drop"], "Expected ALL capabilities to be dropped"


def test_ac6_pod_starts_and_passes_probes():
    """AC-6: Prometheus pod starts, transitions to Running state, and passes probes within 60 seconds"""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    
    namespace = os.getenv("DEMO_NAMESPACE", "default")
    
    # Wait for deployment to be ready
    start_time = time.time()
    deployment_ready = False
    while time.time() - start_time < 60:
        try:
            deployment = apps_v1.read_namespaced_deployment(name=EXPECTED_DEPLOYMENT_NAME, namespace=namespace)
            if deployment.status.ready_replicas and deployment.status.ready_replicas >= 1:
                deployment_ready = True
                break
        except client.exceptions.ApiException:
            pass
        time.sleep(2)
    
    assert deployment_ready, "Prometheus deployment did not become ready within 60 seconds"
    
    # Get pod
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app.kubernetes.io/name={EXPECTED_DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No Prometheus pods found in cluster"
    pod = pods.items[0]
    
    assert pod.status.phase == "Running", f"Prometheus pod is not in Running state, current state: {pod.status.phase}"
    
    # Verify probes are passing
    for condition in pod.status.conditions:
        if condition.type == "Ready":
            assert condition.status == "True", "Prometheus pod is not passing readiness probe"
        if condition.type == "ContainersReady":
            assert condition.status == "True", "Prometheus containers are not ready"


def test_ac7_process_runs_as_non_root():
    """AC-7: Prometheus process runs as non-root user (default UID 1000 or custom UID from env var)"""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    
    v1 = client.CoreV1Api()
    namespace = os.getenv("DEMO_NAMESPACE", "default")
    
    # Get pod
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app.kubernetes.io/name={EXPECTED_DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No Prometheus pods found in cluster"
    pod_name = pods.items[0].metadata.name
    
    # Exec into pod to check process UID
    exec_command = [
        "/bin/sh",
        "-c",
        "ps aux | grep prometheus | grep -v grep | awk '{print $1}'"
    ]
    
    resp = client.stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    uid_output = resp.strip()
    assert uid_output != "root", "Prometheus process is running as root user!"
    
    # Check if it's default UID 1000 or custom
    custom_uid = os.getenv("PROMETHEUS_RUN_AS_USER")
    expected_uid = custom_uid if custom_uid else str(DEFAULT_RUN_AS_USER)
    
    # If output is username, get UID from /etc/passwd
    if not uid_output.isdigit():
        exec_command = [
            "/bin/sh",
            "-c",
            f"id -u {uid_output}"
        ]
        resp = client.stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=exec_command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        uid_output = resp.strip()
    
    assert uid_output == expected_uid, f"Prometheus process running as UID {uid_output}, expected {expected_uid}"


def test_ac8_root_filesystem_read_only():
    """AC-8: Root filesystem is mounted as read-only inside the container"""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    
    v1 = client.CoreV1Api()
    namespace = os.getenv("DEMO_NAMESPACE", "default")
    
    # Get pod
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app.kubernetes.io/name={EXPECTED_DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No Prometheus pods found in cluster"
    pod_name = pods.items[0].metadata.name
    
    # Check root filesystem mount flags
    exec_command = [
        "/bin/sh",
        "-c",
        "mount | grep ' / ' | awk '{print $6}'"
    ]
    
    resp = client.stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    mount_flags = resp.strip()
    assert "ro" in mount_flags.split(","), f"Root filesystem is not mounted read-only, mount flags: {mount_flags}"
