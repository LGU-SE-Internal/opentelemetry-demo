#!/usr/bin/env python3
import pytest
import yaml
import os
from pathlib import Path

K8S_BASE_PATH = Path("/workspace/k8s/postgresql/")
DEPLOYMENT_PATH = K8S_BASE_PATH / "deployment.yaml"
PVC_PATH = K8S_BASE_PATH / "pvc.yaml"
SERVICE_PATH = K8S_BASE_PATH / "service.yaml"

def load_yaml(path):
    """Helper to load yaml file"""
    with open(path, "r") as f:
        return list(yaml.safe_load_all(f))[0]

@pytest.fixture(scope="module")
def deployment():
    assert DEPLOYMENT_PATH.exists(), f"Deployment manifest not found at {DEPLOYMENT_PATH}"
    return load_yaml(DEPLOYMENT_PATH)

@pytest.fixture(scope="module")
def pvc():
    assert PVC_PATH.exists(), f"PVC manifest not found at {PVC_PATH}"
    return load_yaml(PVC_PATH)

@pytest.fixture(scope="module")
def service():
    assert SERVICE_PATH.exists(), f"Service manifest not found at {SERVICE_PATH}"
    return load_yaml(SERVICE_PATH)

@pytest.mark.ac1
def test_ac1_security_context_non_root_least_privilege(deployment):
    """AC-1: Pod runs as non-root user UID 999, least privilege security context"""
    # Check deployment API version and kind
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    security_context = deployment["spec"]["template"]["spec"].get("securityContext", {})
    container_sc = container.get("securityContext", {})
    
    # Check runAsNonRoot
    assert security_context.get("runAsNonRoot") == True, "Security context missing runAsNonRoot: true"
    assert security_context.get("runAsUser") == 999, "Should run as postgres user UID 999"
    
    # Check disallow privilege escalation
    assert container_sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation should be false"
    assert container_sc.get("privileged") == False, "Container should not be privileged"
    assert container_sc.get("runAsUser") == 999 or security_context.get("runAsUser") == 999, "Run as user 999 required"
    assert security_context.get("runAsGroup") == 999 or container_sc.get("runAsGroup") == 999, "Run as group 999 required"
    assert security_context.get("readOnlyRootFilesystem") == True or container_sc.get("readOnlyRootFilesystem") == True, "Root filesystem should be read only"

@pytest.mark.ac2
def test_ac2_liveness_probe_configuration(deployment):
    """AC-2: Liveness probe uses pg_isready with correct parameters"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    liveness = container.get("livenessProbe", {})
    
    assert liveness is not None, "Liveness probe not configured"
    # Check probe uses exec with pg_isready
    assert "exec" in liveness, "Liveness probe should be exec type"
    assert "pg_isready" in liveness["exec"]["command"], "Liveness probe should use pg_isready"
    assert "-U" in liveness["exec"]["command"], "Liveness probe should specify user"
    assert "$POSTGRES_USER" in liveness["exec"]["command"] or "postgres" in liveness["exec"]["command"], "Liveness probe uses POSTGRES_USER"
    
    # Check probe parameters
    assert liveness.get("initialDelaySeconds") == 30, f"Initial delay should be 30, got {liveness.get('initialDelaySeconds')}"
    assert liveness.get("periodSeconds") == 10, f"Period should be 10, got {liveness.get('periodSeconds')}"
    assert liveness.get("failureThreshold") == 3, f"Failure threshold should be 3, got {liveness.get('failureThreshold')}"
    assert liveness.get("port") == 5432 or any("5432" in arg for arg in liveness["exec"]["command"]), "Liveness probe targets port 5432"

@pytest.mark.ac3
def test_ac3_readiness_probe_configuration(deployment):
    """AC-3: Readiness probe uses pg_isready with correct parameters"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    readiness = container.get("readinessProbe", {})
    
    assert readiness is not None, "Readiness probe not configured"
    # Check probe uses exec with pg_isready
    assert "exec" in readiness, "Readiness probe should be exec type"
    assert "pg_isready" in readiness["exec"]["command"], "Readiness probe should use pg_isready"
    assert "-U" in readiness["exec"]["command"], "Readiness probe should specify user"
    assert "$POSTGRES_USER" in readiness["exec"]["command"] or "postgres" in readiness["exec"]["command"], "Readiness probe uses POSTGRES_USER"
    
    # Check probe parameters
    assert readiness.get("initialDelaySeconds") == 5, f"Initial delay should be 5, got {readiness.get('initialDelaySeconds')}"
    assert readiness.get("periodSeconds") == 5, f"Period should be 5, got {readiness.get('periodSeconds')}"
    assert readiness.get("failureThreshold") == 3, f"Failure threshold should be 3, got {readiness.get('failureThreshold')}"
    assert readiness.get("port") == 5432 or any("5432" in arg for arg in readiness["exec"]["command"]), "Readiness probe targets port 5432"

@pytest.mark.ac4
def test_ac4_resource_requests_limits(deployment):
    """AC-4: Correct CPU/memory resource requests and limits configured"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    
    # Check requests
    assert requests.get("cpu") == "100m" or requests.get("cpu") == "0.1", f"CPU request should be 100m, got {requests.get('cpu')}"
    assert requests.get("memory") == "256Mi", f"Memory request should be 256Mi, got {requests.get('memory')}"
    
    # Check limits
    assert limits.get("cpu") == "500m" or limits.get("cpu") == "0.5", f"CPU limit should be 500m, got {limits.get('cpu')}"
    assert limits.get("memory") == "1Gi", f"Memory limit should be 1Gi, got {limits.get('memory')}"

@pytest.mark.ac5
def test_ac5_pvc_configuration(deployment, pvc):
    """AC-5: PVC configuration with ReadWriteOnce, 10Gi storage, correct mount path"""
    # Check PVC specs
    assert pvc["apiVersion"] == "v1"
    assert pvc["kind"] == "PersistentVolumeClaim"
    
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC access mode should be ReadWriteOnce"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi", f"PVC storage should be 10Gi, got {pvc['spec']['resources']['requests']['storage']}"
    
    # Check volume mount in deployment
    volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
    pvc_volume = next((v for v in volumes if "persistentVolumeClaim" in v), None)
    assert pvc_volume is not None, "Deployment missing PVC volume"
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    postgres_mount = next((m for m in volume_mounts if m["mountPath"] == "/var/lib/postgresql/data"), None)
    assert postgres_mount is not None, "Postgres data volume not mounted to /var/lib/postgresql/data"
    assert postgres_mount["name"] == pvc_volume["name"], "Volume mount name does not match PVC volume name"

@pytest.mark.ac6
def test_ac6_clusterip_service_only(service):
    """AC-6: Service is ClusterIP type only, no external exposure, port 5432"""
    assert service["apiVersion"] == "v1"
    assert service["kind"] == "Service"
    
    assert service["spec"]["type"] == "ClusterIP", f"Service type should be ClusterIP, got {service['spec']['type']}"
    assert "nodePort" not in service["spec"].get("ports", [{}])[0], "Service should not have NodePort configured"
    assert "loadBalancerIP" not in service["spec"], "Service should not have LoadBalancer configuration"
    
    port = service["spec"]["ports"][0]
    assert port["port"] == 5432, f"Service port should be 5432, got {port['port']}"
    assert port["targetPort"] == 5432, f"Service targetPort should be 5432, got {port['targetPort']}"

@pytest.mark.ac7
def test_ac7_standard_labels_on_all_resources(deployment, pvc, service):
    """AC-7: All resources have standard OpenTelemetry demo labels"""
    required_labels = {
        "app.kubernetes.io/name": "postgresql",
        "app.kubernetes.io/part-of": "opentelemetry-demo"
    }
    
    for resource, name in [(deployment, "deployment"), (pvc, "pvc"), (service, "service")]:
        labels = resource.get("metadata", {}).get("labels", {})
        for key, expected_value in required_labels.items():
            assert key in labels, f"Missing label {key} on {name}"
            assert labels[key] == expected_value, f"Label {key} on {name} should be {expected_value}, got {labels[key]}"
        
        # Check pod template labels on deployment
        if name == "deployment":
            pod_labels = resource["spec"]["template"]["metadata"]["labels"]
            for key, expected_value in required_labels.items():
                assert key in pod_labels, f"Missing label {key} on deployment pod template"
                assert pod_labels[key] == expected_value, f"Label {key} on pod template should be {expected_value}, got {pod_labels[key]}"

@pytest.mark.ac8
def test_ac8_no_hardcoded_credentials(deployment):
    """AC-8: No hardcoded credentials, all sensitive values from postgresql-secrets secret"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = container.get("env", [])
    
    required_env_vars = ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]
    for var_name in required_env_vars:
        env_var = next((e for e in env if e["name"] == var_name), None)
        assert env_var is not None, f"Missing environment variable {var_name}"
        # Check value from secret
        assert "valueFrom" in env_var, f"{var_name} should be sourced from secret, not hardcoded"
        assert "secretKeyRef" in env_var["valueFrom"], f"{var_name} should use secretKeyRef"
        assert env_var["valueFrom"]["secretKeyRef"]["name"] == "postgresql-secrets", f"{var_name} should reference postgresql-secrets secret"
        assert env_var["valueFrom"]["secretKeyRef"]["key"] == var_name.lower(), f"Secret key should be {var_name.lower()}"
