import yaml
import subprocess
import pytest
import time

DEPLOYMENT_FILE = "./k8s/grafana-deployment.yaml"
DEPLOYMENT_NAME = "grafana"
NAMESPACE = "default"  # Adjust if needed based on actual deployment namespace


def load_deployment_manifest():
    with open(DEPLOYMENT_FILE, "r") as f:
        return list(yaml.safe_load_all(f))[0]


def get_running_deployment():
    result = subprocess.run(
        ["kubectl", "get", "deployment", DEPLOYMENT_NAME, "-n", NAMESPACE, "-o", "yaml"],
        capture_output=True,
        text=True,
        check=True
    )
    return yaml.safe_load(result.stdout)


def get_running_pods():
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE, "-l", f"app={DEPLOYMENT_NAME}", "-o", "yaml"],
        capture_output=True,
        text=True,
        check=True
    )
    return yaml.safe_load(result.stdout)["items"]


def test_ac1_health_probes_configured_correctly():
    """AC-1: Liveness and readiness probes point to /api/health on port 3000 with correct timing"""
    deploy = load_deployment_manifest()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    # Check liveness probe
    assert "livenessProbe" in container
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/api/health"
    assert liveness["httpGet"]["port"] == 3000
    assert liveness["initialDelaySeconds"] == 30
    assert liveness["periodSeconds"] == 10
    assert liveness["timeoutSeconds"] == 5
    assert liveness["failureThreshold"] == 3
    
    # Check readiness probe
    assert "readinessProbe" in container
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/api/health"
    assert readiness["httpGet"]["port"] == 3000
    assert readiness["initialDelaySeconds"] == 5
    assert readiness["periodSeconds"] == 10
    assert readiness["timeoutSeconds"] == 3
    assert readiness["failureThreshold"] == 3


def test_ac2_resource_constraints_configured():
    """AC-2: CPU/memory requests and limits set correctly"""
    deploy = load_deployment_manifest()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "resources" in container
    resources = container["resources"]
    
    assert "requests" in resources
    assert resources["requests"]["cpu"] == "100m"
    assert resources["requests"]["memory"] == "256Mi"
    
    assert "limits" in resources
    assert resources["limits"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "512Mi"


def test_ac3_security_context_configured():
    """AC-3: Non-root security context with correct user ID and security settings"""
    deploy = load_deployment_manifest()
    pod_spec = deploy["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Check container security context first, fall back to pod-level
    sec_ctx = container.get("securityContext", pod_spec.get("securityContext", {}))
    
    assert sec_ctx.get("runAsNonRoot") == True
    assert sec_ctx.get("runAsUser") == 472
    assert sec_ctx.get("allowPrivilegeEscalation") == False
    assert sec_ctx.get("privileged") == False
    assert sec_ctx.get("readOnlyRootFilesystem") == True
    assert "drop" in sec_ctx.get("capabilities", {})
    assert "ALL" in sec_ctx["capabilities"]["drop"]


def test_ac4_pods_start_and_pass_probes():
    """AC-4: Pods reach Running state and pass probes within 60 seconds of rollout"""
    # Trigger rollout restart to test fresh startup
    subprocess.run(
        ["kubectl", "rollout", "restart", f"deployment/{DEPLOYMENT_NAME}", "-n", NAMESPACE],
        check=True, capture_output=True, text=True
    )
    
    # Wait for rollout to complete
    subprocess.run(
        ["kubectl", "rollout", "status", f"deployment/{DEPLOYMENT_NAME}", "-n", NAMESPACE, "--timeout=60s"],
        check=True, capture_output=True, text=True
    )
    
    pods = get_running_pods()
    assert len(pods) > 0
    
    for pod in pods:
        assert pod["status"]["phase"] == "Running"
        # Check all containers are ready
        for container_status in pod["status"]["containerStatuses"]:
            assert container_status["ready"] == True
            assert container_status["started"] == True


def test_ac5_existing_config_works():
    """AC-5: Existing Grafana configuration works, no breakage"""
    # Test that Grafana API is accessible and returns expected health status
    pod_name = get_running_pods()[0]["metadata"]["name"]
    
    # Port forward to pod
    port_forward = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod_name}", "3000:3000", "-n", NAMESPACE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    time.sleep(3)  # Wait for port forward to establish
    
    try:
        # Test health endpoint
        health_result = subprocess.run(
            ["curl", "-s", "http://localhost:3000/api/health"],
            capture_output=True, text=True, check=True
        )
        assert "healthy" in health_result.stdout
        
        # Test data sources are loaded
        ds_result = subprocess.run(
            ["curl", "-s", "http://localhost:3000/api/datasources"],
            capture_output=True, text=True, check=True
        )
        assert len(yaml.safe_load(ds_result.stdout)) > 0  # At least one data source exists
        
    finally:
        port_forward.terminate()
        port_forward.wait()


def test_ac6_no_privileged_capabilities():
    """AC-6: No CAP_* capabilities granted, no privileged mode"""
    deploy = load_deployment_manifest()
    pod_spec = deploy["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    sec_ctx = container.get("securityContext", pod_spec.get("securityContext", {}))
    
    assert sec_ctx.get("privileged") == False
    # Ensure no capabilities are added
    assert "add" not in sec_ctx.get("capabilities", {})
    # Ensure all capabilities are dropped
    assert "ALL" in sec_ctx.get("capabilities", {}).get("drop", [])
