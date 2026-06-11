#!/usr/bin/env python3
import yaml
import subprocess
import os
import pytest

MANIFEST_PATH = "./kubernetes/shippingservice.yaml"

EXPECTED_PROBE_TIMINGS = {
    "livenessProbe": {
        "initialDelaySeconds": 10,
        "periodSeconds": 10,
        "timeoutSeconds": 1,
        "failureThreshold": 3,
        "successThreshold": 1
    },
    "readinessProbe": {
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "timeoutSeconds": 1,
        "failureThreshold": 3,
        "successThreshold": 1
    }
}

@pytest.fixture(scope="module")
def shippingservice_deployment():
    assert os.path.exists(MANIFEST_PATH), f"Manifest {MANIFEST_PATH} not found"
    with open(MANIFEST_PATH, "r") as f:
        docs = list(yaml.safe_load_all(f))
    deployment = next(doc for doc in docs if doc and doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "shippingservice")
    assert deployment, "shippingservice Deployment not found in manifest"
    return deployment

@pytest.fixture(scope="module")
def container_spec(shippingservice_deployment):
    containers = shippingservice_deployment["spec"]["template"]["spec"]["containers"]
    shipping_container = next(c for c in containers if c["name"] == "shippingservice")
    assert shipping_container, "shippingservice container not found in deployment"
    return shipping_container

def test_ac1_liveness_probe_httpget_config(container_spec):
    """AC-1: livenessProbe uses httpGet with path /health, port 8080, no grpc field"""
    liveness_probe = container_spec.get("livenessProbe", {})
    
    # Verify no grpc field exists
    assert "grpc" not in liveness_probe, "livenessProbe should not have grpc field"
    
    # Verify httpGet exists and has correct configuration
    assert "httpGet" in liveness_probe, "livenessProbe must have httpGet field"
    http_get = liveness_probe["httpGet"]
    assert http_get.get("path") == "/health", f"Expected livenessProbe path /health, got {http_get.get('path')}"
    assert http_get.get("port") == 8080, f"Expected livenessProbe port 8080, got {http_get.get('port')}"

def test_ac2_readiness_probe_httpget_config(container_spec):
    """AC-2: readinessProbe uses httpGet with path /health, port 8080, no grpc field"""
    readiness_probe = container_spec.get("readinessProbe", {})
    
    # Verify no grpc field exists
    assert "grpc" not in readiness_probe, "readinessProbe should not have grpc field"
    
    # Verify httpGet exists and has correct configuration
    assert "httpGet" in readiness_probe, "readinessProbe must have httpGet field"
    http_get = readiness_probe["httpGet"]
    assert http_get.get("path") == "/health", f"Expected readinessProbe path /health, got {http_get.get('path')}"
    assert http_get.get("port") == 8080, f"Expected readinessProbe port 8080, got {http_get.get('port')}"

def test_ac3_probe_timing_parameters_unchanged(container_spec):
    """AC-3: All probe timing parameters remain unchanged from original values"""
    for probe_name, expected_timings in EXPECTED_PROBE_TIMINGS.items():
        probe = container_spec.get(probe_name, {})
        for param, expected_value in expected_timings.items():
            actual_value = probe.get(param)
            assert actual_value == expected_value, f"{probe_name} {param} mismatch: expected {expected_value}, got {actual_value}"

def test_ac4_manifest_passes_dry_run_validation():
    """AC-4: Modified manifest passes kubectl apply --dry-run=client validation"""
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-f", MANIFEST_PATH],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl dry-run failed: {result.stderr}"
