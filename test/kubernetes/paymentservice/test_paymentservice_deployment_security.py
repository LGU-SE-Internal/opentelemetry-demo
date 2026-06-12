#!/usr/bin/env python3
"""Test suite for payment service Kubernetes deployment security and resource requirements ACs"""
import yaml
import pytest
from pathlib import Path

DEPLOYMENT_PATH = Path(__file__).parent.parent.parent.parent / "kubernetes" / "paymentservice.deployment.yaml"

@pytest.fixture(scope="module")
def deployment_manifest():
    """Load and parse the deployment manifest"""
    assert DEPLOYMENT_PATH.exists(), f"Deployment file not found at {DEPLOYMENT_PATH}"
    with open(DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture(scope="module")
def payment_container_spec(deployment_manifest):
    """Extract the payment service container spec from the deployment"""
    containers = deployment_manifest["spec"]["template"]["spec"]["containers"]
    payment_container = next(c for c in containers if c["name"] == "paymentservice")
    assert payment_container, "paymentservice container not found in deployment"
    return payment_container

# AC-1: Resource requests and limits tests
def test_ac1_has_resource_requirements(payment_container_spec):
    """AC-1: Payment service deployment has resource requests and limits configured"""
    assert "resources" in payment_container_spec, "No resources field found in container spec"
    resources = payment_container_spec["resources"]
    assert "requests" in resources, "No resource requests configured"
    assert "limits" in resources, "No resource limits configured"

def test_ac1_cpu_request_in_range(payment_container_spec):
    """AC-1: CPU request is between 100m and 200m"""
    cpu_request = payment_container_spec["resources"]["requests"]["cpu"]
    # Convert millicores to integer
    if cpu_request.endswith("m"):
        cpu_val = int(cpu_request.rstrip("m"))
    else:
        cpu_val = int(float(cpu_request) * 1000)
    assert 100 <= cpu_val <= 200, f"CPU request {cpu_request} is outside allowed range (100m-200m)"

def test_ac1_cpu_limit_in_range(payment_container_spec):
    """AC-1: CPU limit is between 300m and 500m"""
    cpu_limit = payment_container_spec["resources"]["limits"]["cpu"]
    if cpu_limit.endswith("m"):
        cpu_val = int(cpu_limit.rstrip("m"))
    else:
        cpu_val = int(float(cpu_limit) * 1000)
    assert 300 <= cpu_val <= 500, f"CPU limit {cpu_limit} is outside allowed range (300m-500m)"

def test_ac1_memory_request_in_range(payment_container_spec):
    """AC-1: Memory request is between 128Mi and 192Mi"""
    mem_request = payment_container_spec["resources"]["requests"]["memory"]
    # Convert Mi to integer
    assert mem_request.endswith("Mi"), "Memory request should be in Mi units"
    mem_val = int(mem_request.rstrip("Mi"))
    assert 128 <= mem_val <= 192, f"Memory request {mem_request} is outside allowed range (128Mi-192Mi)"

def test_ac1_memory_limit_in_range(payment_container_spec):
    """AC-1: Memory limit is between 256Mi and 384Mi"""
    mem_limit = payment_container_spec["resources"]["limits"]["memory"]
    assert mem_limit.endswith("Mi"), "Memory limit should be in Mi units"
    mem_val = int(mem_limit.rstrip("Mi"))
    assert 256 <= mem_val <= 384, f"Memory limit {mem_limit} is outside allowed range (256Mi-384Mi)"

# AC-2: Security context basic configuration tests
def test_ac2_has_security_context(payment_container_spec):
    """AC-2: Container has securityContext configured"""
    assert "securityContext" in payment_container_spec, "No securityContext field found in container spec"
    sc = payment_container_spec["securityContext"]
    assert sc is not None, "securityContext is empty"

def test_ac2_run_as_non_root_enabled(payment_container_spec):
    """AC-2: runAsNonRoot is set to true"""
    sc = payment_container_spec["securityContext"]
    assert "runAsNonRoot" in sc, "runAsNonRoot not configured"
    assert sc["runAsNonRoot"] is True, "runAsNonRoot is not enabled"

def test_ac2_run_as_user_non_root(payment_container_spec):
    """AC-2: runAsUser is set to a non-root UID > 0"""
    sc = payment_container_spec["securityContext"]
    assert "runAsUser" in sc, "runAsUser not configured"
    uid = sc["runAsUser"]
    assert isinstance(uid, int), "runAsUser must be an integer"
    assert uid > 0, f"runAsUser {uid} is root (0) or invalid"

def test_ac2_allow_privilege_escalation_disabled(payment_container_spec):
    """AC-2: allowPrivilegeEscalation is set to false"""
    sc = payment_container_spec["securityContext"]
    assert "allowPrivilegeEscalation" in sc, "allowPrivilegeEscalation not configured"
    assert sc["allowPrivilegeEscalation"] is False, "allowPrivilegeEscalation is not disabled"

def test_ac2_privileged_disabled(payment_container_spec):
    """AC-2: privileged is set to false"""
    sc = payment_container_spec["securityContext"]
    assert "privileged" in sc, "privileged not configured"
    assert sc["privileged"] is False, "privileged mode is not disabled"

# AC-3: Read-only root filesystem test
def test_ac3_read_only_root_filesystem_enabled(payment_container_spec):
    """AC-3: readOnlyRootFilesystem is set to true"""
    sc = payment_container_spec["securityContext"]
    assert "readOnlyRootFilesystem" in sc, "readOnlyRootFilesystem not configured"
    assert sc["readOnlyRootFilesystem"] is True, "readOnlyRootFilesystem is not enabled"

# AC-4: Capabilities drop test
def test_ac4_all_capabilities_dropped(payment_container_spec):
    """AC-4: All Linux capabilities are dropped, no capabilities added"""
    sc = payment_container_spec["securityContext"]
    assert "capabilities" in sc, "capabilities not configured in securityContext"
    capabilities = sc["capabilities"]
    assert "drop" in capabilities, "No capabilities.drop configured"
    assert "ALL" in capabilities["drop"], "ALL capabilities are not dropped"
    assert "add" not in capabilities or len(capabilities["add"]) == 0, "Capabilities are being added, which is not allowed"

# AC-6: Deployment validity test (basic schema validation)
def test_ac6_deployment_has_tmp_volume_mount(deployment_manifest, payment_container_spec):
    """AC-6: Deployment has emptyDir volume mounted to /tmp for write access"""
    # Check volume mount exists in container
    volume_mounts = payment_container_spec.get("volumeMounts", [])
    tmp_mount = next((vm for vm in volume_mounts if vm["mountPath"] == "/tmp"), None)
    assert tmp_mount is not None, "No volume mount for /tmp found"
    # Check volume exists in pod spec
    volumes = deployment_manifest["spec"]["template"]["spec"].get("volumes", [])
    tmp_volume = next((v for v in volumes if v["name"] == tmp_mount["name"]), None)
    assert tmp_volume is not None, f"Volume {tmp_mount['name']} for /tmp not found in pod volumes"
    assert "emptyDir" in tmp_volume, "/tmp volume is not an emptyDir volume"
