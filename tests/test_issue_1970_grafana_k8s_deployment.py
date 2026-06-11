#!/usr/bin/env python3
import os
import yaml

DEPLOYMENT_PATH = "./kubernetes/grafana/deployment.yaml"
EXPECTED_WRITABLE_PATHS = ["/tmp", "/var/log/grafana"]
EXPECTED_PERSISTENT_VOLUME_NAME = "grafana-storage"
EXPECTED_PERSISTENT_MOUNT_PATH = "/var/lib/grafana"

def test_ac1_seccomp_profile_configured():
    """AC-1: Grafana Deployment manifest includes seccompProfile: { type: RuntimeDefault } in both pod securityContext and grafana container securityContext fields"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    # Check pod-level seccomp profile
    pod_security_context = dep["spec"]["template"]["spec"].get("securityContext", {})
    assert "seccompProfile" in pod_security_context, "Pod securityContext missing seccompProfile"
    assert pod_security_context["seccompProfile"]["type"] == "RuntimeDefault", f"Pod seccompProfile expected type RuntimeDefault, got {pod_security_context['seccompProfile'].get('type')}"
    
    # Check container-level seccomp profile
    container = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana")
    container_security_context = container.get("securityContext", {})
    assert "seccompProfile" in container_security_context, "Grafana container securityContext missing seccompProfile"
    assert container_security_context["seccompProfile"]["type"] == "RuntimeDefault", f"Container seccompProfile expected type RuntimeDefault, got {container_security_context['seccompProfile'].get('type')}"

def test_ac2_security_context_hardening_configured():
    """AC-2: Grafana container securityContext includes readOnlyRootFilesystem: true and capabilities: { drop: ["ALL"] } with no extra capabilities added"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana")
    security_context = container.get("securityContext", {})
    
    # Check readOnlyRootFilesystem
    assert "readOnlyRootFilesystem" in security_context, "Container securityContext missing readOnlyRootFilesystem"
    assert security_context["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem expected to be true"
    
    # Check capabilities drop ALL
    assert "capabilities" in security_context, "Container securityContext missing capabilities configuration"
    assert "drop" in security_context["capabilities"], "Capabilities configuration missing drop list"
    assert "ALL" in security_context["capabilities"]["drop"], "Capabilities drop list missing ALL"
    # Ensure no capabilities are added
    assert "add" not in security_context["capabilities"] or len(security_context["capabilities"]["add"]) == 0, "No extra capabilities should be added"

def test_ac3_writable_paths_and_persistent_storage_configured():
    """AC-3: All required temporary writable paths for Grafana operation are mounted from emptyDir volumes, with no changes to the existing persistent storage volume for user data"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_spec = dep["spec"]["template"]["spec"]
    container = next(c for c in pod_spec["containers"] if c["name"] == "grafana")
    
    # Check required writable paths have emptyDir volumes and mounts
    volumes = pod_spec.get("volumes", [])
    volume_mounts = container.get("volumeMounts", [])
    
    for path in EXPECTED_WRITABLE_PATHS:
        volume_name = path.replace("/", "-").strip("-")
        # Check volume exists as emptyDir
        volume_found = any(
            vol.get("name") == volume_name and vol.get("emptyDir") is not None
            for vol in volumes
        )
        assert volume_found, f"Missing emptyDir volume for path {path}"
        # Check volume is mounted to correct path
        mount_found = any(
            mnt.get("name") == volume_name and mnt.get("mountPath") == path
            for mnt in volume_mounts
        )
        assert mount_found, f"Missing volume mount for path {path}"
    
    # Check persistent storage remains unchanged
    persistent_volume_found = any(
        vol.get("name") == EXPECTED_PERSISTENT_VOLUME_NAME and vol.get("persistentVolumeClaim") is not None
        for vol in volumes
    )
    assert persistent_volume_found, f"Persistent volume {EXPECTED_PERSISTENT_VOLUME_NAME} should remain unchanged"
    persistent_mount_found = any(
        mnt.get("name") == EXPECTED_PERSISTENT_VOLUME_NAME and mnt.get("mountPath") == EXPECTED_PERSISTENT_MOUNT_PATH
        for mnt in volume_mounts
    )
    assert persistent_mount_found, f"Persistent volume mount to {EXPECTED_PERSISTENT_MOUNT_PATH} should remain unchanged"

def test_ac4_deployment_spec_valid_for_k8s_1_19_plus():
    """AC-4: When deployed to a Kubernetes 1.19+ cluster, the Grafana pod starts successfully and enters Running state with 0 restarts within 2 minutes of deployment (manifest validity check)"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    # Check apiVersion and kind are valid
    assert dep["apiVersion"] == "apps/v1", f"Expected apiVersion apps/v1, got {dep.get('apiVersion')}"
    assert dep["kind"] == "Deployment", f"Expected kind Deployment, got {dep.get('kind')}"
    # Check seccompProfile fields are valid for k8s 1.19+
    pod_security_context = dep["spec"]["template"]["spec"].get("securityContext", {})
    assert "seccompProfile" in pod_security_context, "Pod seccompProfile required for k8s 1.19+ compatibility"
    container = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana")
    container_security_context = container.get("securityContext", {})
    assert "seccompProfile" in container_security_context, "Container seccompProfile required for k8s 1.19+ compatibility"

def test_ac5_service_and_ports_configured_for_ui_access():
    """AC-5: Grafana UI is accessible on its configured service port, standard operations (view dashboards, run queries) complete without errors (service configuration check)"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    assert os.path.exists("./kubernetes/grafana/service.yaml"), "Grafana service manifest not found"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    with open("./kubernetes/grafana/service.yaml", "r") as f:
        svc = yaml.safe_load(f)
    
    # Check container exposes grafana port
    container = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana")
    ports = [p["containerPort"] for p in container.get("ports", [])]
    assert 3000 in ports, "Grafana container should expose port 3000 for UI access"
    
    # Check service targets correct port
    svc_ports = [p["port"] for p in svc.get("spec", {}).get("ports", [])]
    assert 3000 in svc_ports, "Grafana service should expose port 3000 for UI access"

def test_ac6_security_context_matches_standard_pattern():
    """AC-6: Security context configuration matches the exact pattern used by other production-ready backend services in the repository"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    # Check standard security context properties that apply to all backend services
    pod_security_context = dep["spec"]["template"]["spec"].get("securityContext", {})
    container = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana")
    container_security_context = container.get("securityContext", {})
    
    # Standard settings across all production services
    assert pod_security_context.get("runAsNonRoot", False) == True, "Standard pattern requires runAsNonRoot: true at pod level"
    assert container_security_context.get("allowPrivilegeEscalation", True) == False, "Standard pattern requires allowPrivilegeEscalation: false at container level"
    assert container_security_context.get("privileged", True) == False, "Standard pattern requires privileged: false at container level"
