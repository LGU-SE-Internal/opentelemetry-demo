import pytest
import requests
from kubernetes import client, config
import json
import time

# Constants from spec
DEFAULT_RETENTION_TIME = "30d"
DEFAULT_RETENTION_SIZE = "3.5Gi"
RETENTION_TIME_FLAG = f"--storage.tsdb.retention.time={DEFAULT_RETENTION_TIME}"
RETENTION_SIZE_FLAG = f"--storage.tsdb.retention.size={DEFAULT_RETENTION_SIZE}"
PROMETHEUS_NAMESPACE = "default"
PROMETHEUS_LABEL_SELECTOR = "app.kubernetes.io/name=prometheus"


def get_prometheus_pod():
    """Helper to get running prometheus pod"""
    config.load_kube_config()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(
        namespace=PROMETHEUS_NAMESPACE,
        label_selector=PROMETHEUS_LABEL_SELECTOR,
        field_selector="status.phase=Running"
    )
    assert len(pods.items) > 0, "No running Prometheus pods found"
    return pods.items[0]


def get_prometheus_config(pod_ip):
    """Get prometheus runtime config from API endpoint"""
    url = f"http://{pod_ip}:9090/api/v1/status/config"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


@pytest.mark.integration
@pytest.mark.prometheus
def test_ac1_default_retention_time_cli_arg_present():
    """AC-1: Default deployment has --storage.tsdb.retention.time=30d in command line"""
    pod = get_prometheus_pod()
    command_args = pod.spec.containers[0].args
    assert RETENTION_TIME_FLAG in command_args, \
        f"Retention time flag {RETENTION_TIME_FLAG} not found in container args: {command_args}"


@pytest.mark.integration
@pytest.mark.prometheus
def test_ac2_default_retention_size_cli_arg_present():
    """AC-2: Default deployment has --storage.tsdb.retention.size=3.5Gi in command line"""
    pod = get_prometheus_pod()
    command_args = pod.spec.containers[0].args
    assert RETENTION_SIZE_FLAG in command_args, \
        f"Retention size flag {RETENTION_SIZE_FLAG} not found in container args: {command_args}"


@pytest.mark.integration
@pytest.mark.prometheus
def test_ac3_retention_values_configurable_via_values():
    """AC-3: Retention values can be modified via deployment configuration without editing base manifests"""
    # This test verifies that the values are exposed as configurable parameters
    # First check if Helm values exist with correct fields
    import yaml
    with open("kubernetes/helm/opentelemetry-demo/values.yaml", "r") as f:
        values = yaml.safe_load(f)
    assert "prometheus" in values, "prometheus section missing from values.yaml"
    assert "retentionTime" in values["prometheus"], "prometheus.retentionTime missing from values.yaml"
    assert "retentionSize" in values["prometheus"], "prometheus.retentionSize missing from values.yaml"
    assert values["prometheus"]["retentionTime"] == DEFAULT_RETENTION_TIME, \
        f"Default retentionTime should be {DEFAULT_RETENTION_TIME}"
    assert values["prometheus"]["retentionSize"] == DEFAULT_RETENTION_SIZE, \
        f"Default retentionSize should be {DEFAULT_RETENTION_SIZE}"
    
    # Verify that the deployment template references these values
    with open("kubernetes/helm/opentelemetry-demo/charts/prometheus/templates/deployment.yaml", "r") as f:
        deployment_template = f.read()
    assert ".Values.prometheus.retentionTime" in deployment_template, \
        "Deployment template does not reference prometheus.retentionTime value"
    assert ".Values.prometheus.retentionSize" in deployment_template, \
        "Deployment template does not reference prometheus.retentionSize value"


@pytest.mark.integration
@pytest.mark.prometheus
def test_ac4_readme_documentation():
    """AC-4: Prometheus README documents default retention values, modification instructions and capacity constraints"""
    with open("kubernetes/helm/opentelemetry-demo/charts/prometheus/README.md", "r") as f:
        readme_content = f.read()
    
    # Verify documentation entries exist
    assert "Default retention time (30 days)" in readme_content, \
        "Default retention time not documented in README"
    assert "Default retention size (3.5Gi)" in readme_content, \
        "Default retention size not documented in README"
    assert "Instructions for modifying both values" in readme_content, \
        "Modification instructions missing from README"
    assert "retention size must not exceed allocated PVC capacity" in readme_content.lower(), \
        "PVC capacity constraint note missing from README"


@pytest.mark.integration
@pytest.mark.prometheus
def test_ac5_runtime_config_matches_defaults():
    """AC-5: Prometheus starts successfully and /api/v1/status/config returns matching retention settings"""
    pod = get_prometheus_pod()
    pod_ip = pod.status.pod_ip
    
    # Wait for prometheus API to be available
    for _ in range(30):
        try:
            config_data = get_prometheus_config(pod_ip)
            break
        except Exception:
            time.sleep(2)
    else:
        pytest.fail("Prometheus API did not become available within 60 seconds")
    
    assert config_data["status"] == "success", "Prometheus config API returned non-success status"
    runtime_config = config_data["data"]["yaml"]
    
    # Verify retention settings match defaults
    assert f"retention_time: {DEFAULT_RETENTION_TIME}" in runtime_config, \
        f"Runtime retention time does not match default {DEFAULT_RETENTION_TIME}"
    assert f"retention_size: {DEFAULT_RETENTION_SIZE}" in runtime_config, \
        f"Runtime retention size does not match default {DEFAULT_RETENTION_SIZE}"
