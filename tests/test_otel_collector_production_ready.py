#!/usr/bin/env python3
"""Tests for OpenTelemetry Collector production safeguards implementation (issue #1480)"""
import os
import yaml
import pytest
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Constants from spec
COLLECTOR_HEALTH_PORT = 13133
EXPECTED_UID = 10001
EXPECTED_RESOURCES = {
    "requests": {"cpu": "100m", "memory": "256Mi"},
    "limits": {"cpu": "500m", "memory": "1Gi"}
}
EXPECTED_LIVENESS_PROBE = {
    "httpGet": {"port": COLLECTOR_HEALTH_PORT, "path": "/"},
    "failureThreshold": 3,
    "periodSeconds": 10,
    "initialDelaySeconds": 5
}
EXPECTED_READINESS_PROBE = {
    "httpGet": {"port": COLLECTOR_HEALTH_PORT, "path": "/"},
    "failureThreshold": 3,
    "periodSeconds": 10,
    "initialDelaySeconds": 2
}
EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": EXPECTED_UID,
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
    "readOnlyRootFilesystem": True
}

# Paths to deployment manifests
K8S_COLLECTOR_DEPLOYMENTS = [
    "k8s/monitoring/otel-collector-deployment.yaml",
    # Add any other collector deployment paths if they exist
]
DOCKER_COMPOSE_FILES = [
    "compose.yaml",
    "compose.observability.yaml",
    "compose.full.yaml"
]

@pytest.mark.ac1
def test_ac1_k8s_liveness_probe_configured():
    """AC-1: Kubernetes collector manifests have correctly configured liveness probe"""
    for deploy_path in K8S_COLLECTOR_DEPLOYMENTS:
        assert os.path.exists(deploy_path), f"Missing deployment file: {deploy_path}"
        with open(deploy_path, "r") as f:
            deploy = yaml.safe_load(f)
        
        # Find collector container spec
        containers = deploy["spec"]["template"]["spec"]["containers"]
        collector_container = next(c for c in containers if "otelcol" in c["name"].lower() or "collector" in c["name"].lower())
        
        assert "livenessProbe" in collector_container, "Missing livenessProbe in collector container spec"
        probe = collector_container["livenessProbe"]
        
        assert probe["httpGet"]["port"] == EXPECTED_LIVENESS_PROBE["httpGet"]["port"], f"Wrong liveness probe port, expected {COLLECTOR_HEALTH_PORT}"
        assert probe["httpGet"]["path"] == EXPECTED_LIVENESS_PROBE["httpGet"]["path"], "Wrong liveness probe path"
        assert probe["failureThreshold"] == EXPECTED_LIVENESS_PROBE["failureThreshold"], "Wrong liveness failureThreshold"
        assert probe["periodSeconds"] == EXPECTED_LIVENESS_PROBE["periodSeconds"], "Wrong liveness periodSeconds"
        assert probe["initialDelaySeconds"] == EXPECTED_LIVENESS_PROBE["initialDelaySeconds"], "Wrong liveness initialDelaySeconds"

@pytest.mark.ac2
def test_ac2_k8s_readiness_probe_configured():
    """AC-2: Kubernetes collector manifests have correctly configured readiness probe"""
    for deploy_path in K8S_COLLECTOR_DEPLOYMENTS:
        assert os.path.exists(deploy_path), f"Missing deployment file: {deploy_path}"
        with open(deploy_path, "r") as f:
            deploy = yaml.safe_load(f)
        
        containers = deploy["spec"]["template"]["spec"]["containers"]
        collector_container = next(c for c in containers if "otelcol" in c["name"].lower() or "collector" in c["name"].lower())
        
        assert "readinessProbe" in collector_container, "Missing readinessProbe in collector container spec"
        probe = collector_container["readinessProbe"]
        
        assert probe["httpGet"]["port"] == EXPECTED_READINESS_PROBE["httpGet"]["port"], f"Wrong readiness probe port, expected {COLLECTOR_HEALTH_PORT}"
        assert probe["httpGet"]["path"] == EXPECTED_READINESS_PROBE["httpGet"]["path"], "Wrong readiness probe path"
        assert probe["failureThreshold"] == EXPECTED_READINESS_PROBE["failureThreshold"], "Wrong readiness failureThreshold"
        assert probe["periodSeconds"] == EXPECTED_READINESS_PROBE["periodSeconds"], "Wrong readiness periodSeconds"
        assert probe["initialDelaySeconds"] == EXPECTED_READINESS_PROBE["initialDelaySeconds"], "Wrong readiness initialDelaySeconds"

@pytest.mark.ac3
def test_ac3_resource_limits_configured():
    """AC-3: All collector manifests have correct CPU/memory requests and limits"""
    # Check Kubernetes manifests
    for deploy_path in K8S_COLLECTOR_DEPLOYMENTS:
        assert os.path.exists(deploy_path), f"Missing deployment file: {deploy_path}"
        with open(deploy_path, "r") as f:
            deploy = yaml.safe_load(f)
        
        containers = deploy["spec"]["template"]["spec"]["containers"]
        collector_container = next(c for c in containers if "otelcol" in c["name"].lower() or "collector" in c["name"].lower())
        
        assert "resources" in collector_container, "Missing resources field in collector container"
        resources = collector_container["resources"]
        
        assert "requests" in resources, "Missing resources.requests"
        assert resources["requests"]["cpu"] >= EXPECTED_RESOURCES["requests"]["cpu"], "CPU request too low"
        assert resources["requests"]["memory"] >= EXPECTED_RESOURCES["requests"]["memory"], "Memory request too low"
        
        assert "limits" in resources, "Missing resources.limits"
        assert resources["limits"]["cpu"] <= EXPECTED_RESOURCES["limits"]["cpu"], "CPU limit too high"
        assert resources["limits"]["memory"] <= EXPECTED_RESOURCES["limits"]["memory"], "Memory limit too high"
    
    # Check Docker Compose manifests
    for compose_path in DOCKER_COMPOSE_FILES:
        assert os.path.exists(compose_path), f"Missing compose file: {compose_path}"
        with open(compose_path, "r") as f:
            compose = yaml.safe_load(f)
        
        if "otelcol" not in compose["services"] and "otel-collector" not in compose["services"]:
            continue  # Skip if collector not in this compose file
        
        collector_service = compose["services"].get("otelcol") or compose["services"].get("otel-collector")
        
        assert "deploy" in collector_service, "Missing deploy section in compose collector service"
        assert "resources" in collector_service["deploy"], "Missing deploy.resources in compose collector service"
        resources = collector_service["deploy"]["resources"]
        
        assert "reservations" in resources, "Missing resources.reservations in compose"
        assert resources["reservations"]["cpus"] >= 0.1, "CPU reservation too low (expected min 0.1)"
        assert resources["reservations"]["memory"] >= EXPECTED_RESOURCES["requests"]["memory"], "Memory reservation too low"
        
        assert "limits" in resources, "Missing resources.limits in compose"
        assert resources["limits"]["cpus"] <= 0.5, "CPU limit too high (expected max 0.5)"
        assert resources["limits"]["memory"] <= EXPECTED_RESOURCES["limits"]["memory"], "Memory limit too high"

@pytest.mark.ac4
def test_ac4_k8s_security_context_configured():
    """AC-4: Kubernetes collector has correct security context configuration"""
    for deploy_path in K8S_COLLECTOR_DEPLOYMENTS:
        assert os.path.exists(deploy_path), f"Missing deployment file: {deploy_path}"
        with open(deploy_path, "r") as f:
            deploy = yaml.safe_load(f)
        
        containers = deploy["spec"]["template"]["spec"]["containers"]
        collector_container = next(c for c in containers if "otelcol" in c["name"].lower() or "collector" in c["name"].lower())
        
        assert "securityContext" in collector_container, "Missing securityContext in collector container"
        sc = collector_container["securityContext"]
        
        assert sc["runAsNonRoot"] == EXPECTED_SECURITY_CONTEXT["runAsNonRoot"], "runAsNonRoot must be true"
        assert sc["runAsUser"] == EXPECTED_SECURITY_CONTEXT["runAsUser"], f"runAsUser must be {EXPECTED_UID}"
        assert sc["allowPrivilegeEscalation"] == EXPECTED_SECURITY_CONTEXT["allowPrivilegeEscalation"], "allowPrivilegeEscalation must be false"
        assert "drop" in sc["capabilities"], "Missing capabilities.drop"
        assert "ALL" in sc["capabilities"]["drop"], "Must drop ALL capabilities"
        assert sc["readOnlyRootFilesystem"] == EXPECTED_SECURITY_CONTEXT["readOnlyRootFilesystem"], "readOnlyRootFilesystem must be true"

@pytest.mark.ac5
def test_ac5_docker_compose_non_root_user():
    """AC-5: Docker Compose collector runs as non-root user 10001"""
    for compose_path in DOCKER_COMPOSE_FILES:
        assert os.path.exists(compose_path), f"Missing compose file: {compose_path}"
        with open(compose_path, "r") as f:
            compose = yaml.safe_load(f)
        
        if "otelcol" not in compose["services"] and "otel-collector" not in compose["services"]:
            continue
        
        collector_service = compose["services"].get("otelcol") or compose["services"].get("otel-collector")
        assert "user" in collector_service, "Missing user field in compose collector service"
        assert collector_service["user"] == str(EXPECTED_UID), f"User must be {EXPECTED_UID} in compose"

@pytest.mark.ac6
@pytest.mark.integration
def test_ac6_k8s_collector_starts_successfully():
    """AC-6: Collector starts successfully on Kubernetes without crash loops"""
    # Load Kubernetes config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    
    # Find collector pods
    pods = v1.list_namespaced_pod(namespace="default", label_selector="app.kubernetes.io/component=otel-collector")
    assert len(pods.items) > 0, "No collector pods found in Kubernetes"
    
    for pod in pods.items:
        assert pod.status.phase == "Running", f"Collector pod {pod.metadata.name} is not Running"
        assert pod.status.container_statuses[0].ready == True, f"Collector pod {pod.metadata.name} is not ready"
        assert pod.status.container_statuses[0].restart_count < 2, f"Collector pod {pod.metadata.name} has crashed too many times"
    
    # Check deployment status
    deployments = apps_v1.list_namespaced_deployment(namespace="default", label_selector="app.kubernetes.io/component=otel-collector")
    assert len(deployments.items) > 0, "No collector deployment found"
    for deploy in deployments.items:
        assert deploy.status.ready_replicas == deploy.status.replicas, "Not all collector replicas are ready"

@pytest.mark.ac7
@pytest.mark.integration
def test_ac7_docker_compose_collector_starts_successfully():
    """AC-7: Collector starts successfully on Docker Compose without exit errors"""
    import subprocess
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "{{.Service}} {{.State}} {{.ExitCode}}"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, "Failed to get docker compose status"
    
    collector_found = False
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        service, state, exit_code = line.split()
        if "otelcol" in service or "collector" in service:
            collector_found = True
            assert state.lower() == "running", f"Collector service {service} is not running"
            assert exit_code == "0", f"Collector service {service} exited with non-zero code"
    
    assert collector_found, "No collector service found in Docker Compose"

@pytest.mark.ac8
@pytest.mark.integration
def test_ac8_collector_runs_as_non_root():
    """AC-8: Collector process runs as UID 10001 inside container"""
    # Check Kubernetes pods first
    try:
        config.load_incluster_config()
        v1 = client.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace="default", label_selector="app.kubernetes.io/component=otel-collector")
        if len(pods.items) > 0:
            pod = pods.items[0]
            exec_command = ["/bin/sh", "-c", "ps aux | grep otelcol | grep -v grep | awk '{print $1}'"]
            resp = v1.connect_get_namespaced_pod_exec(
                pod.metadata.name, "default",
                command=exec_command,
                stderr=True, stdin=False,
                stdout=True, tty=False
            )
            assert str(EXPECTED_UID) in resp or "10001" in resp, f"Collector process running as wrong user: {resp}"
    except Exception:
        # Fall back to Docker Compose check
        import subprocess
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "otelcol", "sh", "-c", "ps aux | grep otelcol | grep -v grep | awk '{print $1}'"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["docker", "compose", "exec", "-T", "otel-collector", "sh", "-c", "ps aux | grep otelcol | grep -v grep | awk '{print $1}'"],
                capture_output=True, text=True
            )
        assert result.returncode == 0, "Failed to exec into collector container"
        assert str(EXPECTED_UID) in result.stdout or "10001" in result.stdout, f"Collector process running as wrong user: {result.stdout}"

@pytest.mark.ac9
@pytest.mark.integration
def test_ac9_health_endpoint_returns_200():
    """AC-9: Health endpoint returns 200 OK when service is healthy"""
    # Try Kubernetes service first
    try:
        config.load_incluster_config()
        v1 = client.CoreV1Api()
        svc = v1.read_namespaced_service(name="otel-collector", namespace="default")
        health_url = f"http://{svc.spec.cluster_ip}:{COLLECTOR_HEALTH_PORT}/"
        resp = requests.get(health_url, timeout=5)
        assert resp.status_code == 200, f"Health endpoint returned {resp.status_code} instead of 200"
    except Exception:
        # Fall back to Docker Compose endpoint
        health_url = f"http://localhost:{COLLECTOR_HEALTH_PORT}/"
        resp = requests.get(health_url, timeout=5)
        assert resp.status_code == 200, f"Health endpoint returned {resp.status_code} instead of 200"
EOF && chmod +x tests/test_otel_collector_production_ready.py
