import yaml
import subprocess
import requests
import pytest
import os
from pathlib import Path

DEPLOYMENT_FILE = Path(__file__).parent.parent / "k8s" / "image-provider-deployment.yaml"

def test_ac1_readiness_probe_path_is_ready():
    """AC-1: Verify readinessProbe.httpGet.path is set to /ready"""
    with open(DEPLOYMENT_FILE, "r") as f:
        docs = list(yaml.safe_load_all(f))
    deployment = next(doc for doc in docs if doc["kind"] == "Deployment")
    containers = deployment["spec"]["template"]["spec"]["containers"]
    readiness_probe = containers[0]["readinessProbe"]
    assert readiness_probe["httpGet"]["path"] == "/ready"

def test_ac2_liveness_probe_path_remains_health():
    """AC-2: Verify livenessProbe.httpGet.path remains set to /health"""
    with open(DEPLOYMENT_FILE, "r") as f:
        docs = list(yaml.safe_load_all(f))
    deployment = next(doc for doc in docs if doc["kind"] == "Deployment")
    containers = deployment["spec"]["template"]["spec"]["containers"]
    liveness_probe = containers[0]["livenessProbe"]
    assert liveness_probe["httpGet"]["path"] == "/health"

def test_ac3_manifest_dry_run_succeeds():
    """AC-3: Verify kubectl apply --dry-run=client completes with no errors"""
    result = subprocess.run(
        ["kubectl", "apply", "-f", str(DEPLOYMENT_FILE), "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Dry run failed: {result.stderr}"

@pytest.mark.docker
def test_ac4_ready_endpoint_returns_200_when_static_exists():
    """AC-4: /ready returns 200 when /static directory exists and is readable"""
    # Run container with static dir present
    container_id = subprocess.check_output([
        "docker", "run", "-d", "--rm",
        "-p", "8080:8080",
        "-v", f"{os.getcwd()}/src/image-provider/static:/static",
        "ghcr.io/open-telemetry/demo:latest-image-provider"
    ]).strip().decode()
    
    try:
        # Wait for container to start
        subprocess.run(["docker", "exec", container_id, "wait-for-it", "localhost:8080", "-t", "10"], check=True)
        response = requests.get("http://localhost:8080/ready")
        assert response.status_code == 200
    finally:
        subprocess.run(["docker", "stop", container_id], capture_output=True)

@pytest.mark.docker
def test_ac5_ready_endpoint_returns_error_when_static_missing():
    """AC-5: /ready returns >=400 when /static directory is missing or unreadable"""
    # Run container without static dir mounted
    container_id = subprocess.check_output([
        "docker", "run", "-d", "--rm",
        "-p", "8081:8080",
        "ghcr.io/open-telemetry/demo:latest-image-provider"
    ]).strip().decode()
    
    try:
        # Wait for container to start
        subprocess.run(["docker", "exec", container_id, "wait-for-it", "localhost:8080", "-t", "10"], check=True)
        # Delete static dir inside container
        subprocess.run(["docker", "exec", container_id, "rm", "-rf", "/static"], check=True)
        response = requests.get("http://localhost:8081/ready")
        assert response.status_code >= 400
    finally:
        subprocess.run(["docker", "stop", container_id], capture_output=True)

@pytest.mark.docker
def test_ac6_health_endpoint_always_returns_200_when_nginx_running():
    """AC-6: /health returns 200 whenever Nginx is running, regardless of /static state"""
    # Run container without static dir
    container_id = subprocess.check_output([
        "docker", "run", "-d", "--rm",
        "-p", "8082:8080",
        "ghcr.io/open-telemetry/demo:latest-image-provider"
    ]).strip().decode()
    
    try:
        # Wait for container to start
        subprocess.run(["docker", "exec", container_id, "wait-for-it", "localhost:8080", "-t", "10"], check=True)
        # Delete static dir
        subprocess.run(["docker", "exec", container_id, "rm", "-rf", "/static"], check=True)
        # Health check should still pass
        response = requests.get("http://localhost:8082/health")
        assert response.status_code == 200
    finally:
        subprocess.run(["docker", "stop", container_id], capture_output=True)
