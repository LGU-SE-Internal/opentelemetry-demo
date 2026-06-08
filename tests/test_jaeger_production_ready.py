#!/usr/bin/env python3
import os
import time
import requests
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import pytest

# Configuration
JAEGER_DEPLOYMENT_PATH = os.path.join(os.path.dirname(__file__), "..", "k8s", "jaeger-deployment.yaml")
NAMESPACE = "default"
JAEGER_LABEL_SELECTOR = "app.kubernetes.io/name=jaeger"

# Load Kubernetes config
try:
    config.load_incluster_config()
except:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()


def test_ac1_liveness_probe_config():
    """Verify Jaeger deployment has all required liveness probe fields as defined in spec"""
    with open(JAEGER_DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    containers = dep["spec"]["template"]["spec"]["containers"]
    jaeger_container = next(c for c in containers if c["name"] == "jaeger")
    
    assert "livenessProbe" in jaeger_container, "Liveness probe missing from Jaeger container"
    probe = jaeger_container["livenessProbe"]
    
    # Verify probe type and HTTP config
    assert "httpGet" in probe, "Liveness probe should be HTTP GET type"
    assert probe["httpGet"]["path"] == "/health", f"Expected liveness probe path /health, got {probe['httpGet']['path']}"
    assert probe["httpGet"]["port"] == 14269, f"Expected liveness probe port 14269, got {probe['httpGet']['port']}"
    
    # Verify parameters
    assert probe["initialDelaySeconds"] == 30, f"Expected initialDelaySeconds 30, got {probe['initialDelaySeconds']}"
    assert probe["periodSeconds"] == 10, f"Expected periodSeconds 10, got {probe['periodSeconds']}"
    assert probe["failureThreshold"] == 3, f"Expected failureThreshold 3, got {probe['failureThreshold']}"


def test_ac2_readiness_probe_config():
    """Verify Jaeger deployment has all required readiness probe fields as defined in spec"""
    with open(JAEGER_DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    containers = dep["spec"]["template"]["spec"]["containers"]
    jaeger_container = next(c for c in containers if c["name"] == "jaeger")
    
    assert "readinessProbe" in jaeger_container, "Readiness probe missing from Jaeger container"
    probe = jaeger_container["readinessProbe"]
    
    # Verify probe type and HTTP config
    assert "httpGet" in probe, "Readiness probe should be HTTP GET type"
    assert probe["httpGet"]["path"] == "/health", f"Expected readiness probe path /health, got {probe['httpGet']['path']}"
    assert probe["httpGet"]["port"] == 14269, f"Expected readiness probe port 14269, got {probe['httpGet']['port']}"
    
    # Verify parameters
    assert probe["initialDelaySeconds"] == 5, f"Expected initialDelaySeconds 5, got {probe['initialDelaySeconds']}"
    assert probe["periodSeconds"] == 5, f"Expected periodSeconds 5, got {probe['periodSeconds']}"
    assert probe["failureThreshold"] == 3, f"Expected failureThreshold 3, got {probe['failureThreshold']}"


def test_ac3_resource_requirements_config():
    """Verify Jaeger deployment has all required CPU/memory resource requests/limits as defined in spec"""
    with open(JAEGER_DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    containers = dep["spec"]["template"]["spec"]["containers"]
    jaeger_container = next(c for c in containers if c["name"] == "jaeger")
    
    assert "resources" in jaeger_container, "Resource requirements missing from Jaeger container"
    resources = jaeger_container["resources"]
    
    # Verify requests
    assert "requests" in resources, "Resource requests missing"
    assert resources["requests"]["cpu"] == "100m", f"Expected CPU request 100m, got {resources['requests']['cpu']}"
    assert resources["requests"]["memory"] == "256Mi", f"Expected memory request 256Mi, got {resources['requests']['memory']}"
    
    # Verify limits
    assert "limits" in resources, "Resource limits missing"
    assert resources["limits"]["cpu"] == "500m", f"Expected CPU limit 500m, got {resources['limits']['cpu']}"
    assert resources["limits"]["memory"] == "512Mi", f"Expected memory limit 512Mi, got {resources['limits']['memory']}"


def test_ac4_security_context_config():
    """Verify Jaeger deployment has all required security context fields as defined in spec"""
    with open(JAEGER_DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_spec = dep["spec"]["template"]["spec"]
    containers = pod_spec["containers"]
    jaeger_container = next(c for c in containers if c["name"] == "jaeger")
    
    # Verify pod-level security context
    assert "securityContext" in pod_spec, "Pod-level security context missing"
    pod_sc = pod_spec["securityContext"]
    assert pod_sc["runAsNonRoot"] == True, "Pod should be configured to run as non-root"
    assert pod_sc["runAsUser"] == 10001, f"Expected runAsUser 10001, got {pod_sc['runAsUser']}"
    
    # Verify container-level security context
    assert "securityContext" in jaeger_container, "Container-level security context missing"
    container_sc = jaeger_container["securityContext"]
    assert container_sc["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation should be false"
    assert container_sc["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem should be true"
    assert "capabilities" in container_sc, "Capabilities section missing from security context"
    assert container_sc["capabilities"]["drop"] == ["ALL"], "Should drop ALL capabilities"


@pytest.mark.integration
def test_ac5_no_probe_failures_under_normal_operation():
    """Verify no liveness/readiness probe failures after 10+ minutes of normal operation"""
    # Get running Jaeger pod
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=JAEGER_LABEL_SELECTOR, field_selector="status.phase=Running")
    assert len(pods.items) > 0, "No running Jaeger pods found"
    pod = pods.items[0]
    pod_name = pod.metadata.name
    
    # Wait 10 minutes to accumulate probe results
    time.sleep(600)
    
    # Get updated pod status
    pod = v1.read_namespaced_pod(pod_name, NAMESPACE)
    
    # Check for liveness probe failures
    liveness_failed = any(
        c.last_state.terminated and "liveness probe failed" in (c.last_state.terminated.message or "")
        for c in pod.status.container_statuses
        if c.name == "jaeger"
    )
    assert not liveness_failed, "Liveness probe failures detected in running pod"
    
    # Check for readiness probe failures
    readiness_failed = any(
        c.readiness_probe_failed
        for c in pod.status.container_statuses
        if c.name == "jaeger"
    )
    assert not readiness_failed, "Readiness probe failures detected in running pod"


@pytest.mark.integration
def test_ac6_pod_restarts_on_liveness_failure():
    """Verify pod restarts within 40 seconds when Jaeger process is manually terminated"""
    # Get running Jaeger pod
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=JAEGER_LABEL_SELECTOR, field_selector="status.phase=Running")
    assert len(pods.items) > 0, "No running Jaeger pods found"
    pod = pods.items[0]
    pod_name = pod.metadata.name
    initial_restart_count = next(
        c.restart_count for c in pod.status.container_statuses if c.name == "jaeger"
    )
    
    # Kill the Jaeger process inside the pod
    exec_command = ["/bin/sh", "-c", "kill 1"]
    resp = v1.connect_post_namespaced_pod_exec(
        pod_name, NAMESPACE, command=exec_command,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    
    # Wait up to 40 seconds for restart
    start_time = time.time()
    restarted = False
    while time.time() - start_time < 40:
        pod = v1.read_namespaced_pod(pod_name, NAMESPACE)
        current_restart_count = next(
            c.restart_count for c in pod.status.container_statuses if c.name == "jaeger"
        )
        if current_restart_count > initial_restart_count:
            restarted = True
            break
        time.sleep(2)
    
    assert restarted, "Pod did not restart within 40 seconds after Jaeger process was killed"


@pytest.mark.integration
def test_ac7_pod_not_ready_until_health_endpoint_returns_200():
    """Verify pod remains in NotReady state until /health endpoint returns 200 OK during startup"""
    # Trigger rollout restart of Jaeger deployment
    apps_v1.patch_namespaced_deployment(
        "jaeger", NAMESPACE,
        {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ")}}}}}
    )
    
    # Wait for new pod to be created
    time.sleep(10)
    
    # Get new pod
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=JAEGER_LABEL_SELECTOR)
    new_pod = next(p for p in pods.items if p.status.phase != "Succeeded")
    pod_name = new_pod.metadata.name
    
    # Monitor pod readiness until it becomes ready
    start_time = time.time()
    was_not_ready = False
    while time.time() - start_time < 120:
        try:
            pod = v1.read_namespaced_pod(pod_name, NAMESPACE)
            ready = next(
                c.ready for c in pod.status.container_statuses if c.name == "jaeger"
            )
            if not ready:
                was_not_ready = True
            elif ready and was_not_ready:
                break
        except ApiException:
            pass
        time.sleep(1)
    
    assert was_not_ready, "Pod was immediately ready, no NotReady phase observed during startup"
    
    # Verify health endpoint returns 200 when pod is ready
    pod_ip = pod.status.pod_ip
    resp = requests.get(f"http://{pod_ip}:14269/health", timeout=5)
    assert resp.status_code == 200, f"Health endpoint returned {resp.status_code} when pod is ready"


@pytest.mark.integration
def test_ac8_running_as_non_root_user():
    """Verify container runs as non-root user 10001 and root commands fail"""
    # Get running Jaeger pod
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=JAEGER_LABEL_SELECTOR, field_selector="status.phase=Running")
    assert len(pods.items) > 0, "No running Jaeger pods found"
    pod = pods.items[0]
    pod_name = pod.metadata.name
    
    # Check user ID
    exec_command = ["id", "-u"]
    resp = v1.connect_post_namespaced_pod_exec(
        pod_name, NAMESPACE, command=exec_command,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    uid = resp.strip()
    assert uid == "10001", f"Expected running as user ID 10001, got {uid}"
    
    # Attempt to run root command (apt update)
    exec_command = ["apt", "update"]
    try:
        resp = v1.connect_post_namespaced_pod_exec(
            pod_name, NAMESPACE, command=exec_command,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        assert False, "apt update command succeeded when it should have failed for non-root user"
    except ApiException as e:
        assert "Permission denied" in str(e) or "permission denied" in str(e), "Root command failed for unexpected reason"


@pytest.mark.integration
def test_ac9_jaeger_service_functionality():
    """Verify Jaeger accepts OTLP traces and queries return expected results"""
    # Get running Jaeger pod
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=JAEGER_LABEL_SELECTOR, field_selector="status.phase=Running")
    assert len(pods.items) > 0, "No running Jaeger pods found"
    pod = pods.items[0]
    pod_ip = pod.status.pod_ip
    
    # Send test trace via OTLP
    test_trace = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "test-service"}}]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "test"},
                        "spans": [
                            {
                                "traceId": "abcdef1234567890abcdef1234567890",
                                "spanId": "1234567890abcdef",
                                "name": "test-span",
                                "startTimeUnixNano": int(time.time() * 1e9),
                                "endTimeUnixNano": int((time.time() + 0.1) * 1e9),
                                "attributes": []
                            }
                        ]
                    }
                ]
            }
        ]
    }
    
    resp = requests.post(f"http://{pod_ip}:4317/v1/traces", json=test_trace, timeout=5)
    assert resp.status_code in (200, 202), f"Failed to send OTLP trace, status code {resp.status_code}"
    
    # Wait for trace to be indexed
    time.sleep(5)
    
    # Query for trace via Jaeger API
    resp = requests.get(
        f"http://{pod_ip}:16686/api/traces/abcdef1234567890abcdef1234567890",
        timeout=5
    )
    assert resp.status_code == 200, f"Failed to query trace, status code {resp.status_code}"
    trace_data = resp.json()
    assert len(trace_data["data"]) > 0, "Test trace not found in Jaeger"
