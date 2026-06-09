#!/usr/bin/env python3
"""Integration tests for Prometheus Service ACs (issue #1593)"""
import os
import yaml
import requests
from kubernetes import client, config

PROMETHEUS_SERVICE_PATH = "./k8s/prometheus-service.yaml"
EXPECTED_SERVICE_NAME = "prometheus"
EXPECTED_PORT = 9090
EXPECTED_TARGET_PORT = 9090
EXPECTED_PORT_NAME = "http-metrics"
EXPECTED_SELECTOR_LABELS = {
    "app.kubernetes.io/name": "prometheus"
}
EXPECTED_STANDARD_LABELS = {
    "app.kubernetes.io/name": "prometheus",
    "app.kubernetes.io/part-of": "opentelemetry-demo"
}


def test_ac1_prometheus_service_manifest_exists():
    """AC-1: Valid Kubernetes Service YAML manifest for prometheus exists in k8s directory"""
    assert os.path.exists(PROMETHEUS_SERVICE_PATH), f"Prometheus Service manifest not found at {PROMETHEUS_SERVICE_PATH}"
    
    # Verify it's valid YAML
    with open(PROMETHEUS_SERVICE_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert manifest is not None, "Manifest is empty or invalid YAML"
    assert manifest.get("kind") == "Service", "Manifest kind is not Service"
    assert manifest.get("apiVersion") == "v1", "Manifest apiVersion is not v1"


def test_ac2_service_type_clusterip():
    """AC-2: Service is explicitly configured as type: ClusterIP"""
    with open(PROMETHEUS_SERVICE_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert "spec" in manifest, "Service has no spec section"
    assert manifest["spec"].get("type") == "ClusterIP", f"Expected service type ClusterIP, got {manifest['spec'].get('type')}"


def test_ac3_port_configuration():
    """AC-3: Service exposes port 9090 with targetPort 9090 matching prometheus container port"""
    with open(PROMETHEUS_SERVICE_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert "ports" in manifest["spec"], "Service has no ports defined"
    ports = manifest["spec"]["ports"]
    assert len(ports) >= 1, "Service has no ports configured"
    
    prom_port = next((p for p in ports if p["port"] == EXPECTED_PORT), None)
    assert prom_port is not None, f"Port {EXPECTED_PORT} not found in service ports"
    
    assert prom_port.get("targetPort") == EXPECTED_TARGET_PORT, f"Expected targetPort {EXPECTED_TARGET_PORT}, got {prom_port.get('targetPort')}"
    assert prom_port.get("name") == EXPECTED_PORT_NAME, f"Expected port name '{EXPECTED_PORT_NAME}', got {prom_port.get('name')}"


def test_ac4_selector_matches_deployment_labels():
    """AC-4: Service spec.selector exactly matches prometheus Deployment pod labels"""
    with open(PROMETHEUS_SERVICE_PATH, "r") as f:
        svc_manifest = yaml.safe_load(f)
    
    assert "selector" in svc_manifest["spec"], "Service has no selector defined"
    selector = svc_manifest["spec"]["selector"]
    
    # Verify all expected selector labels are present
    for key, expected_value in EXPECTED_SELECTOR_LABELS.items():
        assert key in selector, f"Selector missing required label {key}"
        assert selector[key] == expected_value, f"Selector label {key} has value {selector[key]}, expected {expected_value}"
    
    # No extra selector labels allowed
    extra_labels = set(selector.keys()) - set(EXPECTED_SELECTOR_LABELS.keys())
    assert len(extra_labels) == 0, f"Service selector has unexpected extra labels: {extra_labels}"


def test_ac5_service_name_matches_deployment():
    """AC-5: Service metadata.name is identical to prometheus Deployment name"""
    with open(PROMETHEUS_SERVICE_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert "metadata" in manifest, "Service has no metadata section"
    assert "name" in manifest["metadata"], "Service has no name defined"
    assert manifest["metadata"]["name"] == EXPECTED_SERVICE_NAME, f"Expected service name {EXPECTED_SERVICE_NAME}, got {manifest['metadata']['name']}"


def test_ac6_standard_metadata_labels():
    """AC-6: Service metadata includes all standard labels present on other demo services"""
    with open(PROMETHEUS_SERVICE_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert "labels" in manifest["metadata"], "Service metadata has no labels"
    labels = manifest["metadata"]["labels"]
    
    # Verify all standard labels are present
    for key, expected_value in EXPECTED_STANDARD_LABELS.items():
        assert key in labels, f"Metadata missing required standard label {key}"
        assert labels[key] == expected_value, f"Metadata label {key} has value {labels[key]}, expected {expected_value}"


def test_ac7_service_accessible_in_cluster():
    """AC-7: Service is accessible within cluster, returns 200 for config endpoint"""
    # Load kubeconfig
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    
    v1 = client.CoreV1Api()
    
    # Get service in default namespace (adjust if needed for your environment)
    namespace = os.getenv("DEMO_NAMESPACE", "default")
    
    try:
        service = v1.read_namespaced_service(name=EXPECTED_SERVICE_NAME, namespace=namespace)
    except client.exceptions.ApiException as e:
        assert False, f"Could not find Prometheus service in cluster: {e}"
    
    # Test access via service DNS name
    service_dns = f"{EXPECTED_SERVICE_NAME}.{namespace}.svc.cluster.local:{EXPECTED_PORT}"
    endpoint = f"http://{service_dns}/api/v1/status/config"
    
    try:
        response = requests.get(endpoint, timeout=10)
    except requests.exceptions.RequestException as e:
        assert False, f"Failed to connect to Prometheus service at {endpoint}: {e}"
    
    assert response.status_code == 200, f"Expected HTTP 200 from Prometheus config endpoint, got {response.status_code}"
    assert "status" in response.json(), "Prometheus config response is invalid, missing status field"
    assert response.json()["status"] == "success", "Prometheus config endpoint returned non-success status"
