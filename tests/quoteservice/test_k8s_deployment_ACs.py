#!/usr/bin/env python3
import os
import yaml
import subprocess
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
import requests

DEPLOYMENT_PATH = "./src/quoteservice/k8s/deployment.yaml"
SERVICE_PATH = "./src/quoteservice/k8s/service.yaml"
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")
SERVICE_NAME = "quote-service"
OTEL_COLLECTOR_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")

@pytest.fixture(scope="module")
def load_deployment_manifest():
    if not os.path.exists(DEPLOYMENT_PATH):
        pytest.fail(f"Deployment manifest not found at {DEPLOYMENT_PATH}")
    with open(DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture(scope="module")
def load_service_manifest():
    if not os.path.exists(SERVICE_PATH):
        pytest.fail(f"Service manifest not found at {SERVICE_PATH}")
    with open(SERVICE_PATH, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture(scope="module")
def kube_client():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()

@pytest.mark.ac1
def test_ac1_deployment_manifest_valid():
    """AC-1: Deployment manifest valid, passes kubectl validate without errors"""
    assert os.path.exists(DEPLOYMENT_PATH), "Deployment manifest file missing at expected path"
    
    result = subprocess.run(
        ["kubectl", "validate", "-f", DEPLOYMENT_PATH],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl validate failed for deployment: {result.stderr}"

@pytest.mark.ac2
def test_ac2_resource_limits_correct(load_deployment_manifest):
    """AC-2: Deployment has correct resource requests/limits: 100m CPU/128Mi mem request, 200m CPU/256Mi mem limit"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    quote_container = next(c for c in containers if c["name"] == SERVICE_NAME)
    
    resources = quote_container["resources"]
    assert resources["requests"]["cpu"] == "100m", "CPU request should be 100m"
    assert resources["requests"]["memory"] == "128Mi", "Memory request should be 128Mi"
    assert resources["limits"]["cpu"] == "200m", "CPU limit should be 200m"
    assert resources["limits"]["memory"] == "256Mi", "Memory limit should be 256Mi"

@pytest.mark.ac3
def test_ac3_probes_configured_correctly(load_deployment_manifest):
    """AC-3: Liveness and readiness probes configured to GET /health on port 8080 with correct parameters"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    quote_container = next(c for c in containers if c["name"] == SERVICE_NAME)
    
    # Test liveness probe
    liveness = quote_container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/health", "Liveness probe path should be /health"
    assert liveness["httpGet"]["port"] == 8080, "Liveness probe port should be 8080"
    assert liveness["initialDelaySeconds"] == 5, "Liveness initialDelaySeconds should be 5"
    assert liveness["periodSeconds"] == 10, "Liveness periodSeconds should be 10"
    assert liveness["timeoutSeconds"] == 1, "Liveness timeoutSeconds should be 1"
    assert liveness["failureThreshold"] == 3, "Liveness failureThreshold should be 3"
    
    # Test readiness probe
    readiness = quote_container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/health", "Readiness probe path should be /health"
    assert readiness["httpGet"]["port"] == 8080, "Readiness probe port should be 8080"
    assert readiness["initialDelaySeconds"] == 5, "Readiness initialDelaySeconds should be 5"
    assert readiness["periodSeconds"] == 10, "Readiness periodSeconds should be 10"
    assert readiness["timeoutSeconds"] == 1, "Readiness timeoutSeconds should be 1"
    assert readiness["failureThreshold"] == 3, "Readiness failureThreshold should be 3"

@pytest.mark.ac4
def test_ac4_security_context_non_root(load_deployment_manifest):
    """AC-4: Security context configured for non-root execution with correct hardening settings"""
    pod_spec = load_deployment_manifest["spec"]["template"]["spec"]
    pod_sc = pod_spec["securityContext"]
    
    assert pod_sc["runAsNonRoot"] == True, "runAsNonRoot must be true"
    assert pod_sc["runAsUser"] == 1000, "runAsUser must be 1000"
    assert pod_sc["runAsGroup"] == 1000, "runAsGroup must be 1000"
    
    container_sc = next(c for c in pod_spec["containers"] if c["name"] == SERVICE_NAME)["securityContext"]
    assert container_sc["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation must be false"
    assert container_sc["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem must be true"
    assert "ALL" in container_sc["capabilities"]["drop"], "Must drop all capabilities"

@pytest.mark.ac5
def test_ac5_otel_environment_variables_present(load_deployment_manifest):
    """AC-5: All standard OpenTelemetry environment variables are present"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    quote_container = next(c for c in containers if c["name"] == SERVICE_NAME)
    env_names = [env["name"] for env in quote_container["env"]]
    
    required_otel_vars = [
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER"
    ]
    
    for var in required_otel_vars:
        assert var in env_names, f"Required OTel environment variable {var} is missing"

@pytest.mark.ac6
def test_ac6_service_manifest_valid():
    """AC-6: Service manifest valid, passes kubectl validate without errors"""
    assert os.path.exists(SERVICE_PATH), "Service manifest file missing at expected path"
    
    result = subprocess.run(
        ["kubectl", "validate", "-f", SERVICE_PATH],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl validate failed for service: {result.stderr}"

@pytest.mark.ac7
def test_ac7_service_exposes_correct_ports(load_service_manifest):
    """AC-7: Service exposes HTTP 8080 and gRPC 8081 endpoints as ClusterIP type"""
    assert load_service_manifest["spec"]["type"] == "ClusterIP", "Service type must be ClusterIP"
    
    ports = load_service_manifest["spec"]["ports"]
    port_map = {p["port"]: p["targetPort"] for p in ports}
    
    assert 8080 in port_map, "HTTP port 8080 not exposed"
    assert port_map[8080] == 8080, "HTTP targetPort must be 8080"
    assert 8081 in port_map, "gRPC port 8081 not exposed"
    assert port_map[8081] == 8081, "gRPC targetPort must be 8081"

@pytest.mark.ac8
def test_ac8_pod_running_with_passing_probes(kube_client):
    """AC-8: Quote service pod transitions to Running status with all probes passing within 60 seconds"""
    start_time = time.time()
    while time.time() - start_time < 60:
        try:
            pods = kube_client.list_namespaced_pod(
                namespace=NAMESPACE,
                label_selector=f"app.kubernetes.io/name={SERVICE_NAME}"
            )
            if not pods.items:
                time.sleep(2)
                continue
            
            pod = pods.items[0]
            if pod.status.phase == "Running":
                # Check all container statuses are ready
                ready = all(cs.ready for cs in pod.status.container_statuses)
                if ready:
                    return
            time.sleep(2)
        except ApiException:
            time.sleep(2)
    
    pytest.fail("Quote service pod did not reach Running state with passing probes within 60 seconds")

@pytest.mark.ac9
def test_ac9_otel_export_successful():
    """AC-9: Quote service successfully exports traces and metrics to OTel collector"""
    # Send test request to quote service to trigger telemetry generation
    try:
        resp = requests.get(f"http://{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:8080/health", timeout=5)
        assert resp.status_code == 200, "Quote service health endpoint not reachable"
    except Exception as e:
        pytest.fail(f"Failed to reach quote service: {str(e)}")
    
    # Wait for telemetry to export, check collector metrics endpoint for quote service metrics
    time.sleep(10)
    try:
        collector_resp = requests.get(f"{OTEL_COLLECTOR_ENDPOINT}/metrics", timeout=10)
        assert collector_resp.status_code == 200, "OTel collector metrics endpoint not reachable"
        assert SERVICE_NAME in collector_resp.text, f"No metrics found for {SERVICE_NAME} in OTel collector"
    except Exception as e:
        pytest.fail(f"Failed to verify OTel export: {str(e)}")
