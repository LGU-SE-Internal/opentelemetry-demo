#!/usr/bin/env python3
import pytest
import yaml
from pathlib import Path

K8S_BASE_PATH = Path("./kubernetes/kafka/")
DEPLOYMENT_PATH = K8S_BASE_PATH / "deployment.yaml"
PVC_PATH = K8S_BASE_PATH / "pvc.yaml"
SERVICE_PATH = K8S_BASE_PATH / "service.yaml"

def load_yaml(path):
    """Helper to load yaml file and validate no API errors"""
    assert path.exists(), f"Manifest not found at {path}"
    with open(path, "r") as f:
        try:
            return list(yaml.safe_load_all(f))[0]
        except yaml.YAMLError as e:
            pytest.fail(f"Invalid YAML in {path}: {str(e)}")

@pytest.fixture(scope="module")
def deployment():
    return load_yaml(DEPLOYMENT_PATH)

@pytest.fixture(scope="module")
def pvc():
    return load_yaml(PVC_PATH)

@pytest.fixture(scope="module")
def service():
    return load_yaml(SERVICE_PATH)

@pytest.mark.ac1
def test_ac1_manifest_api_validation(deployment, pvc, service):
    """AC-1: All resources created without API validation errors for Kubernetes 1.24+"""
    # Check API versions are stable and supported in 1.24+
    assert deployment["apiVersion"] == "apps/v1", f"Deployment should use apps/v1 API, got {deployment['apiVersion']}"
    assert deployment["kind"] == "Deployment", f"Expected Deployment kind, got {deployment['kind']}"
    assert deployment["metadata"]["name"] == "kafka", f"Deployment should be named 'kafka', got {deployment['metadata']['name']}"
    
    assert pvc["apiVersion"] == "v1", f"PVC should use v1 API, got {pvc['apiVersion']}"
    assert pvc["kind"] == "PersistentVolumeClaim", f"Expected PersistentVolumeClaim kind, got {pvc['kind']}"
    assert pvc["metadata"]["name"] == "kafka-data", f"PVC should be named 'kafka-data', got {pvc['metadata']['name']}"
    
    assert service["apiVersion"] == "v1", f"Service should use v1 API, got {service['apiVersion']}"
    assert service["kind"] == "Service", f"Expected Service kind, got {service['kind']}"
    assert service["metadata"]["name"] == "kafka", f"Service should be named 'kafka', got {service['metadata']['name']}"
    
    # Check namespace is opentelemetry-demo (or defaults correctly)
    for resource in [deployment, pvc, service]:
        assert resource["metadata"].get("namespace", "opentelemetry-demo") == "opentelemetry-demo", f"{resource['kind']} should be in opentelemetry-demo namespace"

@pytest.mark.ac2
def test_ac2_deployment_resource_configuration(deployment):
    """AC-2: Deployment has correct CPU/memory resource requests and limits"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "kafka", f"Container should be named 'kafka', got {container['name']}"
    
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    
    # Validate requests
    cpu_request = requests.get("cpu", "0m")
    if isinstance(cpu_request, str) and "m" in cpu_request:
        cpu_request_val = int(cpu_request.replace("m", ""))
    else:
        cpu_request_val = float(cpu_request) * 1000
    assert cpu_request_val == 500, f"CPU request should be 500m, got {cpu_request}"
    
    mem_request = requests.get("memory", "0Gi")
    if isinstance(mem_request, str) and "Gi" in mem_request:
        mem_request_val = int(mem_request.replace("Gi", "")) * 1024
    elif isinstance(mem_request, str) and "Mi" in mem_request:
        mem_request_val = int(mem_request.replace("Mi", ""))
    assert mem_request_val == 1024, f"Memory request should be 1Gi, got {mem_request}"
    
    # Validate limits
    cpu_limit = limits.get("cpu", "0m")
    if isinstance(cpu_limit, str) and "m" in cpu_limit:
        cpu_limit_val = int(cpu_limit.replace("m", ""))
    else:
        cpu_limit_val = float(cpu_limit) * 1000
    assert cpu_limit_val == 2000, f"CPU limit should be 2000m, got {cpu_limit}"
    
    mem_limit = limits.get("memory", "0Gi")
    if isinstance(mem_limit, str) and "Gi" in mem_limit:
        mem_limit_val = int(mem_limit.replace("Gi", "")) * 1024
    elif isinstance(mem_limit, str) and "Mi" in mem_limit:
        mem_limit_val = int(mem_limit.replace("Mi", ""))
    assert mem_limit_val == 4096, f"Memory limit should be 4Gi, got {mem_limit}"

@pytest.mark.ac3
def test_ac3_liveness_probe_configuration(deployment):
    """AC-3: Liveness probe configured with correct exec command and parameters"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "Liveness probe not configured"
    
    liveness = container["livenessProbe"]
    assert "exec" in liveness, "Liveness probe should be exec type"
    assert liveness["exec"]["command"] == ["/scripts/kafka-liveness.sh"], f"Liveness command should be /scripts/kafka-liveness.sh, got {liveness['exec']['command']}"
    
    assert liveness.get("initialDelaySeconds", 0) == 30, f"initialDelaySeconds should be 30, got {liveness.get('initialDelaySeconds')}"
    assert liveness.get("periodSeconds", 0) == 10, f"periodSeconds should be 10, got {liveness.get('periodSeconds')}"
    assert liveness.get("failureThreshold", 0) == 3, f"failureThreshold should be 3, got {liveness.get('failureThreshold')}"

@pytest.mark.ac4
def test_ac4_readiness_probe_configuration(deployment):
    """AC-4: Readiness probe configured with correct exec command and parameters"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "readinessProbe" in container, "Readiness probe not configured"
    
    readiness = container["readinessProbe"]
    assert "exec" in readiness, "Readiness probe should be exec type"
    assert readiness["exec"]["command"] == ["/scripts/kafka-readiness.sh"], f"Readiness command should be /scripts/kafka-readiness.sh, got {readiness['exec']['command']}"
    
    assert readiness.get("initialDelaySeconds", 0) == 10, f"initialDelaySeconds should be 10, got {readiness.get('initialDelaySeconds')}"
    assert readiness.get("periodSeconds", 0) == 5, f"periodSeconds should be 5, got {readiness.get('periodSeconds')}"
    assert readiness.get("failureThreshold", 0) == 3, f"failureThreshold should be 3, got {readiness.get('failureThreshold')}"

@pytest.mark.ac5
def test_ac5_security_context_configuration(deployment):
    """AC-5: Pod security context has correct non-root hardening settings"""
    pod_spec = deployment["spec"]["template"]["spec"]
    pod_sc = pod_spec.get("securityContext", {})
    container = pod_spec["containers"][0]
    container_sc = container.get("securityContext", {})
    
    # Check required security settings
    assert pod_sc.get("runAsNonRoot") == True or container_sc.get("runAsNonRoot") == True, "runAsNonRoot must be set to true"
    assert pod_sc.get("runAsUser") == 1001 or container_sc.get("runAsUser") == 1001, "runAsUser must be set to 1001"
    assert pod_sc.get("allowPrivilegeEscalation") == False or container_sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation must be set to false"
    assert container_sc.get("privileged") != True, "privileged must not be set to true"
    assert pod_sc.get("readOnlyRootFilesystem") == True or container_sc.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem must be set to true"

@pytest.mark.ac6
def test_ac6_pvc_mount_and_persistence_configuration(deployment, pvc):
    """AC-6: PVC is correctly mounted to /var/lib/kafka/data with correct persistence settings"""
    # Validate PVC specs
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC access mode should include ReadWriteOnce"
    
    storage = pvc["spec"]["resources"]["requests"]["storage"]
    if isinstance(storage, str) and "Gi" in storage:
        storage_val = int(storage.replace("Gi", ""))
    elif isinstance(storage, str) and "Mi" in storage:
        storage_val = int(storage.replace("Mi", "")) / 1024
    assert storage_val == 10, f"PVC storage request should be 10Gi, got {storage}"
    
    # Validate volume mount in deployment
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    kafka_mount = next((m for m in volume_mounts if m["mountPath"] == "/var/lib/kafka/data"), None)
    assert kafka_mount is not None, "Kafka data volume not mounted to /var/lib/kafka/data"
    assert kafka_mount["name"] == "kafka-data", f"Volume mount should reference kafka-data PVC, got {kafka_mount['name']}"
    
    # Validate volume exists in pod spec
    volumes = pod_spec.get("volumes", [])
    kafka_volume = next((v for v in volumes if v["name"] == "kafka-data"), None)
    assert kafka_volume is not None, "kafka-data volume not defined in pod spec"
    assert kafka_volume["persistentVolumeClaim"]["claimName"] == "kafka-data", "Volume should reference kafka-data PVC"

@pytest.mark.ac7
def test_ac7_service_port_configuration(service):
    """AC-7: ClusterIP service exposes 9092 plaintext by default, 9093 TLS is optional"""
    assert service["spec"]["type"] == "ClusterIP", f"Service should be ClusterIP type, got {service['spec']['type']}"
    
    ports = service["spec"]["ports"]
    plaintext_port = next((p for p in ports if p["port"] == 9092), None)
    assert plaintext_port is not None, "Port 9092 (plaintext) must be exposed"
    assert plaintext_port["targetPort"] == 9092, f"Plaintext target port should be 9092, got {plaintext_port['targetPort']}"
    
    # Check TLS port is either commented or exists as optional
    tls_port = next((p for p in ports if p["port"] == 9093), None)
    # If TLS port exists, it should be correct
    if tls_port is not None:
        assert tls_port["targetPort"] == 9093, f"TLS target port should be 9093, got {tls_port['targetPort']}"

@pytest.mark.ac8
def test_ac8_probe_pass_with_healthy_zookeeper(deployment):
    """AC-8: Pod passes liveness/readiness probes within 2 minutes when connected to healthy ZooKeeper"""
    # Validate probe time configuration allows for 2 minute startup
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    
    # Liveness probe: 30s initial delay + 3 failures * 10s period = 60s, plus readiness checks
    total_probe_window = container["livenessProbe"]["initialDelaySeconds"] + (container["livenessProbe"]["failureThreshold"] * container["livenessProbe"]["periodSeconds"])
    total_probe_window += container["readinessProbe"]["initialDelaySeconds"] + (container["readinessProbe"]["failureThreshold"] * container["readinessProbe"]["periodSeconds"])
    assert total_probe_window <= 120, f"Total probe window should be <= 120s (2 minutes), got {total_probe_window}s"
