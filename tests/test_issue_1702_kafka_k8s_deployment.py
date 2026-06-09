#!/usr/bin/env python3
import pytest
import yaml
import os
from pathlib import Path

K8S_BASE_PATH = Path("./kubernetes/kafka/")
STATEFULSET_PATH = K8S_BASE_PATH / "statefulset.yaml"
PVC_PATH = K8S_BASE_PATH / "pvc.yaml"
SERVICE_BROKER_PATH = K8S_BASE_PATH / "service-broker.yaml"
SERVICE_CONTROLLER_PATH = K8S_BASE_PATH / "service-controller.yaml"

def load_yaml(path):
    """Helper to load yaml file"""
    with open(path, "r") as f:
        return list(yaml.safe_load_all(f))[0]

@pytest.fixture(scope="module")
def statefulset():
    assert STATEFULSET_PATH.exists(), f"StatefulSet manifest not found at {STATEFULSET_PATH}"
    return load_yaml(STATEFULSET_PATH)

@pytest.fixture(scope="module")
def pvc():
    assert PVC_PATH.exists(), f"PVC manifest not found at {PVC_PATH}"
    return load_yaml(PVC_PATH)

@pytest.fixture(scope="module")
def service_broker():
    assert SERVICE_BROKER_PATH.exists(), f"Broker service manifest not found at {SERVICE_BROKER_PATH}"
    return load_yaml(SERVICE_BROKER_PATH)

@pytest.fixture(scope="module")
def service_controller():
    assert SERVICE_CONTROLLER_PATH.exists(), f"Controller service manifest not found at {SERVICE_CONTROLLER_PATH}"
    return load_yaml(SERVICE_CONTROLLER_PATH)

@pytest.mark.ac1
def test_ac1_statefulset_resources_security_probes(statefulset):
    """AC-1: Kafka StatefulSet has proper resources, security context, and probes"""
    # Check API version and kind
    assert statefulset["apiVersion"] == "apps/v1"
    assert statefulset["kind"] == "StatefulSet"
    assert statefulset["metadata"]["name"] == "kafka-broker"
    
    container = statefulset["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    
    # Check resource requests meet minimum requirements
    cpu_request = requests.get("cpu", "0m")
    if isinstance(cpu_request, str) and "m" in cpu_request:
        cpu_request_val = int(cpu_request.replace("m", ""))
    else:
        cpu_request_val = float(cpu_request) * 1000
    assert cpu_request_val >= 200, f"CPU request should be at least 200m, got {cpu_request}"
    
    mem_request = requests.get("memory", "0Mi")
    if isinstance(mem_request, str) and "Mi" in mem_request:
        mem_request_val = int(mem_request.replace("Mi", ""))
    elif isinstance(mem_request, str) and "Gi" in mem_request:
        mem_request_val = int(mem_request.replace("Gi", "")) * 1024
    assert mem_request_val >= 512, f"Memory request should be at least 512Mi, got {mem_request}"
    
    # Check resource limits meet maximum requirements
    cpu_limit = limits.get("cpu", "0m")
    if isinstance(cpu_limit, str) and "m" in cpu_limit:
        cpu_limit_val = int(cpu_limit.replace("m", ""))
    else:
        cpu_limit_val = float(cpu_limit) * 1000
    assert cpu_limit_val <= 2000, f"CPU limit should be at most 2 CPU, got {cpu_limit}"
    
    mem_limit = limits.get("memory", "0Mi")
    if isinstance(mem_limit, str) and "Mi" in mem_limit:
        mem_limit_val = int(mem_limit.replace("Mi", ""))
    elif isinstance(mem_limit, str) and "Gi" in mem_limit:
        mem_limit_val = int(mem_limit.replace("Gi", "")) * 1024
    assert mem_limit_val <= 4096, f"Memory limit should be at most 4Gi, got {mem_limit}"
    
    # Check security context
    pod_sc = statefulset["spec"]["template"]["spec"].get("securityContext", {})
    container_sc = container.get("securityContext", {})
    
    assert pod_sc.get("runAsNonRoot") == True or container_sc.get("runAsNonRoot") == True, "Missing runAsNonRoot: true in security context"
    assert pod_sc.get("allowPrivilegeEscalation") == False or container_sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation should be false"
    assert container_sc.get("privileged") != True, "Container should not run in privileged mode"
    
    # Check probes
    assert "startupProbe" in container, "Startup probe not configured"
    assert "livenessProbe" in container, "Liveness probe not configured"
    assert "readinessProbe" in container, "Readiness probe not configured"
    
    for probe_name in ["startupProbe", "livenessProbe", "readinessProbe"]:
        probe = container[probe_name]
        assert "tcpSocket" in probe, f"{probe_name} should be TCP socket type"
        assert probe["tcpSocket"]["port"] == 9092, f"{probe_name} should target port 9092"

@pytest.mark.ac2
def test_ac2_pvc_configuration_and_mount(statefulset, pvc):
    """AC-2: PVC with ReadWriteOnce, >=5Gi storage, mounted to correct path"""
    # Check PVC specs
    assert pvc["apiVersion"] == "v1"
    assert pvc["kind"] == "PersistentVolumeClaim"
    assert pvc["metadata"]["name"] == "kafka-broker-data"
    
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC access mode should be ReadWriteOnce"
    
    storage = pvc["spec"]["resources"]["requests"]["storage"]
    if isinstance(storage, str) and "Gi" in storage:
        storage_val = int(storage.replace("Gi", ""))
    elif isinstance(storage, str) and "Mi" in storage:
        storage_val = int(storage.replace("Mi", "")) / 1024
    assert storage_val >= 5, f"PVC storage should be at least 5Gi, got {storage}"
    
    # Check volume mount in statefulset
    container = statefulset["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    kafka_mount = next((m for m in volume_mounts if m["mountPath"] == "/var/lib/kafka/data"), None)
    assert kafka_mount is not None, "Kafka data volume not mounted to /var/lib/kafka/data"

@pytest.mark.ac3
def test_ac3_service_configurations(service_broker, service_controller, statefulset):
    """AC-3: Two ClusterIP services for broker and controller with correct selectors"""
    # Check broker service
    assert service_broker["apiVersion"] == "v1"
    assert service_broker["kind"] == "Service"
    assert service_broker["metadata"]["name"] == "kafka-broker"
    assert service_broker["spec"]["type"] == "ClusterIP", "Broker service should be ClusterIP type"
    assert service_broker["spec"]["ports"][0]["port"] == 9092, "Broker service should expose port 9092"
    assert service_broker["spec"]["ports"][0]["targetPort"] == 9092, "Broker service targetPort should be 9092"
    
    # Check controller service
    assert service_controller["apiVersion"] == "v1"
    assert service_controller["kind"] == "Service"
    assert service_controller["metadata"]["name"] == "kafka-controller"
    assert service_controller["spec"]["type"] == "ClusterIP", "Controller service should be ClusterIP type"
    assert service_controller["spec"]["ports"][0]["port"] == 9093, "Controller service should expose port 9093"
    assert service_controller["spec"]["ports"][0]["targetPort"] == 9093, "Controller service targetPort should be 9093"
    
    # Check selectors match statefulset pod labels
    pod_labels = statefulset["spec"]["template"]["metadata"]["labels"]
    expected_selector = {
        "app.kubernetes.io/name": "kafka",
        "app.kubernetes.io/component": "broker"
    }
    
    for key, val in expected_selector.items():
        assert service_broker["spec"]["selector"][key] == val, f"Broker service missing selector {key}={val}"
        assert service_controller["spec"]["selector"][key] == val, f"Controller service missing selector {key}={val}"
        assert pod_labels[key] == val, f"StatefulSet pod template missing label {key}={val}"
