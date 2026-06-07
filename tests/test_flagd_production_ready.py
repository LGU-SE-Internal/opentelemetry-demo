#!/usr/bin/env python3
import subprocess
import json
import requests
import pytest
import time

K8S_FLAGD_DEPLOYMENT_NAME = "flagd"
FLAGD_NAMESPACE = "default"
FLAGD_HEALTH_PORT = 8013
FLAGD_HEALTH_PATH = "/healthz"
EXPECTED_REQUESTS_CPU = "100m"
EXPECTED_REQUESTS_MEM = "128Mi"
EXPECTED_LIMITS_CPU = "500m"
EXPECTED_LIMITS_MEM = "256Mi"
EXPECTED_RUN_AS_USER = 1000
EXPECTED_RUN_AS_NON_ROOT = True
EXPECTED_ALLOW_PRIVILEGE_ESCALATION = False
EXPECTED_READ_ONLY_ROOT_FILESYSTEM = True
EXPECTED_SECCOMP_PROFILE_TYPE = "RuntimeDefault"
EXPECTED_CAPABILITIES_DROP = ["ALL"]

def get_flagd_deployment_spec():
    """Helper to get flagd deployment spec from Kubernetes"""
    cmd = [
        "kubectl", "get", "deployment", K8S_FLAGD_DEPLOYMENT_NAME,
        "-n", FLAGD_NAMESPACE,
        "-o", "jsonpath={.spec.template.spec.containers[?(@.name=='" + K8S_FLAGD_DEPLOYMENT_NAME + "')]}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to get flagd deployment: {result.stderr}"
    return json.loads(result.stdout)

def get_running_flagd_pod_name():
    """Helper to get running flagd pod name"""
    cmd = [
        "kubectl", "get", "pods", "-n", FLAGD_NAMESPACE,
        "-l", f"app={K8S_FLAGD_DEPLOYMENT_NAME}",
        "--field-selector", "status.phase=Running",
        "-o", "jsonpath={.items[0].metadata.name}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to get running flagd pod: {result.stderr}"
    return result.stdout.strip()

def test_ac1_probes_exist_and_work():
    """AC-1: Liveness and readiness probes exist pointing to :8013/healthz, work correctly"""
    spec = get_flagd_deployment_spec()
    
    # Verify probes exist and have correct configuration
    assert "livenessProbe" in spec, "LivenessProbe not found in flagd container spec"
    assert "readinessProbe" in spec, "ReadinessProbe not found in flagd container spec"
    
    for probe_type in ["livenessProbe", "readinessProbe"]:
        probe = spec[probe_type]
        assert "httpGet" in probe, f"{probe_type} must be HTTPGet type"
        assert probe["httpGet"]["path"] == FLAGD_HEALTH_PATH, f"{probe_type} path incorrect: expected {FLAGD_HEALTH_PATH}, got {probe['httpGet']['path']}"
        assert probe["httpGet"]["port"] == FLAGD_HEALTH_PORT, f"{probe_type} port incorrect: expected {FLAGD_HEALTH_PORT}, got {probe['httpGet']['port']}"
        assert probe["initialDelaySeconds"] == 5, f"{probe_type} initialDelaySeconds incorrect"
        assert probe["periodSeconds"] == 10, f"{probe_type} periodSeconds incorrect"
        assert probe["timeoutSeconds"] == 3, f"{probe_type} timeoutSeconds incorrect"
        assert probe["failureThreshold"] == 3, f"{probe_type} failureThreshold incorrect"
    
    # Verify pod becomes ready within 10 seconds of startup
    pod_name = get_running_flagd_pod_name()
    cmd = ["kubectl", "wait", f"pod/{pod_name}", "-n", FLAGD_NAMESPACE, "--for=condition=Ready", "--timeout=10s"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Flagd pod did not become ready within 10 seconds: {result.stderr}"
    
    # Verify health endpoint returns 200 OK
    cmd = [
        "kubectl", "port-forward", f"pod/{pod_name}",
        f"{FLAGD_HEALTH_PORT}:{FLAGD_HEALTH_PORT}",
        "-n", FLAGD_NAMESPACE
    ]
    port_forward = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)  # Wait for port forward to start
    
    try:
        response = requests.get(f"http://localhost:{FLAGD_HEALTH_PORT}{FLAGD_HEALTH_PATH}", timeout=5)
        assert response.status_code == 200, f"Health endpoint returned {response.status_code}, expected 200"
    finally:
        port_forward.terminate()
        port_forward.wait()

def test_ac2_resource_requests_limits_configured():
    """AC-2: Resource requests and limits are set to specified values"""
    spec = get_flagd_deployment_spec()
    
    assert "resources" in spec, "Resources section not found in flagd container spec"
    resources = spec["resources"]
    
    assert "requests" in resources, "Resource requests not configured"
    assert resources["requests"]["cpu"] == EXPECTED_REQUESTS_CPU, f"CPU request incorrect: expected {EXPECTED_REQUESTS_CPU}, got {resources['requests']['cpu']}"
    assert resources["requests"]["memory"] == EXPECTED_REQUESTS_MEM, f"Memory request incorrect: expected {EXPECTED_REQUESTS_MEM}, got {resources['requests']['memory']}"
    
    assert "limits" in resources, "Resource limits not configured"
    assert resources["limits"]["cpu"] == EXPECTED_LIMITS_CPU, f"CPU limit incorrect: expected {EXPECTED_LIMITS_CPU}, got {resources['limits']['cpu']}"
    assert resources["limits"]["memory"] == EXPECTED_LIMITS_MEM, f"Memory limit incorrect: expected {EXPECTED_LIMITS_MEM}, got {resources['limits']['memory']}"

def test_ac3_security_context_configured():
    """AC-3: Non-root security context is properly configured with no elevated privileges"""
    spec = get_flagd_deployment_spec()
    
    assert "securityContext" in spec, "SecurityContext not found in flagd container spec"
    sc = spec["securityContext"]
    
    assert sc.get("runAsNonRoot") == EXPECTED_RUN_AS_NON_ROOT, "runAsNonRoot not set correctly"
    assert sc.get("runAsUser") == EXPECTED_RUN_AS_USER, f"runAsUser incorrect: expected {EXPECTED_RUN_AS_USER}, got {sc.get('runAsUser')}"
    assert sc.get("allowPrivilegeEscalation") == EXPECTED_ALLOW_PRIVILEGE_ESCALATION, "allowPrivilegeEscalation not set correctly"
    assert sc.get("readOnlyRootFilesystem") == EXPECTED_READ_ONLY_ROOT_FILESYSTEM, "readOnlyRootFilesystem not set correctly"
    
    assert "seccompProfile" in sc, "seccompProfile not configured"
    assert sc["seccompProfile"].get("type") == EXPECTED_SECCOMP_PROFILE_TYPE, f"seccompProfile type incorrect: expected {EXPECTED_SECCOMP_PROFILE_TYPE}, got {sc['seccompProfile'].get('type')}"
    
    assert "capabilities" in sc, "Capabilities not configured"
    assert "drop" in sc["capabilities"], "Capabilities drop list not configured"
    assert set(sc["capabilities"]["drop"]) == set(EXPECTED_CAPABILITIES_DROP), f"Capabilities drop incorrect: expected {EXPECTED_CAPABILITIES_DROP}, got {sc['capabilities']['drop']}"
    
    # Verify running as non-root user inside pod
    pod_name = get_running_flagd_pod_name()
    cmd = ["kubectl", "exec", f"pod/{pod_name}", "-n", FLAGD_NAMESPACE, "--", "id", "-u"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to exec into pod: {result.stderr}"
    assert int(result.stdout.strip()) == EXPECTED_RUN_AS_USER, f"Running as wrong user: expected UID {EXPECTED_RUN_AS_USER}, got {result.stdout.strip()}"
    
    # Verify cannot write to root filesystem
    cmd = ["kubectl", "exec", f"pod/{pod_name}", "-n", FLAGD_NAMESPACE, "--", "touch", "/test-write.txt"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode != 0, "Should not be able to write to root filesystem"
    assert "Permission denied" in result.stderr, "Expected permission denied error when writing to root filesystem"

def test_ac4_deployment_works_with_existing_tls_and_functionality():
    """AC-4: Deployment starts successfully with existing TLS config and feature flag evaluation works"""
    # Verify deployment is in ready state
    cmd = [
        "kubectl", "rollout", "status", f"deployment/{K8S_FLAGD_DEPLOYMENT_NAME}",
        "-n", FLAGD_NAMESPACE, "--timeout=60s"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Flagd deployment rollout failed: {result.stderr}"
    
    # Verify entrypoint.sh TLS config loaded correctly (check logs for no TLS errors)
    pod_name = get_running_flagd_pod_name()
    cmd = ["kubectl", "logs", f"pod/{pod_name}", "-n", FLAGD_NAMESPACE]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to get pod logs: {result.stderr}"
    assert "TLS configuration loaded successfully" in result.stdout or "Serving flag evaluation with TLS" in result.stdout, "TLS configuration not loaded correctly"
    assert "error" not in result.stdout.lower(), f"Error found in flagd logs: {result.stdout}"
    
    # Verify feature flag evaluation works (sample test against flagd API)
    # This assumes default demo flags exist
    cmd = [
        "kubectl", "port-forward", f"pod/{pod_name}",
        f"8013:{FLAGD_HEALTH_PORT}", "8014:8014",
        "-n", FLAGD_NAMESPACE
    ]
    port_forward = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)
    
    try:
        # Test feature flag evaluation endpoint
        payload = {
            "flagKey": "productCatalogServiceFailure",
            "context": {}
        }
        response = requests.post("http://localhost:8014/schema.v1.Service/ResolveBoolean", json=payload, timeout=5)
        assert response.status_code == 200, f"Flag evaluation failed with status {response.status_code}"
        response_data = response.json()
        assert "value" in response_data, "Flag evaluation response missing value field"
        assert "reason" in response_data, "Flag evaluation response missing reason field"
    finally:
        port_forward.terminate()
        port_forward.wait()
