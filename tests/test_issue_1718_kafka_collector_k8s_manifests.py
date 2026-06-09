#!/usr/bin/env python3
import pytest
import yaml
from pathlib import Path

K8S_BASE_PATH = Path("./kubernetes/kafka-collector/")
DEPLOYMENT_PATH = K8S_BASE_PATH / "kafka-collector-deployment.yaml"
SERVICE_PATH = K8S_BASE_PATH / "kafka-collector-service.yaml"


def load_yaml(path):
    """Helper to load yaml file"""
    with open(path, "r") as f:
        return list(yaml.safe_load_all(f))[0]


@pytest.fixture(scope="module")
def deployment():
    assert DEPLOYMENT_PATH.exists(), f"Deployment manifest not found at {DEPLOYMENT_PATH}"
    return load_yaml(DEPLOYMENT_PATH)


@pytest.fixture(scope="module")
def service():
    assert SERVICE_PATH.exists(), f"Service manifest not found at {SERVICE_PATH}"
    return load_yaml(SERVICE_PATH)


@pytest.mark.ac1
def test_ac1_deployment_replicas_and_rolling_update(deployment):
    """AC-1: Deployment creates ReplicaSet maintaining at least 1 ready pod, no crash loops"""
    # Check API version and kind
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "kafka-collector"

    # Check replica count is at least 1
    replicas = deployment.get("spec", {}).get("replicas", 1)
    assert replicas >= 1, f"Deployment should have at least 1 replica, got {replicas}"

    # Check rolling update strategy for zero downtime
    strategy = deployment.get("spec", {}).get("strategy", {})
    assert strategy.get("type") == "RollingUpdate", "Deployment should use RollingUpdate strategy"
    rolling_update = strategy.get("rollingUpdate", {})
    assert rolling_update.get("maxUnavailable") == 0, "maxUnavailable should be 0 for zero downtime"
    assert rolling_update.get("maxSurge") == 1, "maxSurge should be 1 for zero downtime"

    # Check container exists
    containers = deployment["spec"]["template"]["spec"].get("containers", [])
    assert len(containers) > 0, "Deployment pod spec has no containers"
    assert containers[0]["name"] == "kafka-collector", "Container should be named kafka-collector"


@pytest.mark.ac2
def test_ac2_security_context_configuration(deployment):
    """AC-2: Pod spec includes required security context settings"""
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    # Check pod-level and container-level security context
    pod_sc = pod_spec.get("securityContext", {})
    container_sc = container.get("securityContext", {})

    # runAsNonRoot check
    assert pod_sc.get("runAsNonRoot") == True or container_sc.get("runAsNonRoot") == True, \
        "Missing runAsNonRoot: true in security context"

    # runAsUser check (non-zero UID < 65535)
    run_as_user = pod_sc.get("runAsUser", container_sc.get("runAsUser", 0))
    assert run_as_user > 0 and run_as_user < 65535, \
        f"runAsUser should be non-zero and less than 65535, got {run_as_user}"

    # readOnlyRootFilesystem check
    assert pod_sc.get("readOnlyRootFilesystem") == True or container_sc.get("readOnlyRootFilesystem") == True, \
        "Missing readOnlyRootFilesystem: true in security context"

    # allowPrivilegeEscalation check
    assert pod_sc.get("allowPrivilegeEscalation") == False or container_sc.get("allowPrivilegeEscalation") == False, \
        "allowPrivilegeEscalation should be false"

    # capabilities drop ALL check
    capabilities = container_sc.get("capabilities", {})
    drop_capabilities = capabilities.get("drop", [])
    assert "ALL" in drop_capabilities, "Should drop all Linux capabilities"


@pytest.mark.ac3
def test_ac3_liveness_probe_configuration(deployment):
    """AC-3: Liveness probe configured correctly for /health/live endpoint"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "Liveness probe not configured"

    probe = container["livenessProbe"]
    assert "httpGet" in probe, "Liveness probe should be HTTP GET type"
    http_get = probe["httpGet"]

    assert http_get["path"] == "/health/live", f"Liveness probe path should be /health/live, got {http_get['path']}"
    assert http_get["port"] == 8080, f"Liveness probe port should be 8080, got {http_get['port']}"

    assert probe.get("initialDelaySeconds") == 10, f"initialDelaySeconds should be 10, got {probe.get('initialDelaySeconds')}"
    assert probe.get("periodSeconds") == 30, f"periodSeconds should be 30, got {probe.get('periodSeconds')}"
    assert probe.get("failureThreshold") == 3, f"failureThreshold should be 3, got {probe.get('failureThreshold')}"


@pytest.mark.ac4
def test_ac4_readiness_probe_configuration(deployment):
    """AC-4: Readiness probe configured correctly for /health/ready endpoint"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "readinessProbe" in container, "Readiness probe not configured"

    probe = container["readinessProbe"]
    assert "httpGet" in probe, "Readiness probe should be HTTP GET type"
    http_get = probe["httpGet"]

    assert http_get["path"] == "/health/ready", f"Readiness probe path should be /health/ready, got {http_get['path']}"
    assert http_get["port"] == 8080, f"Readiness probe port should be 8080, got {http_get['port']}"

    assert probe.get("initialDelaySeconds") == 5, f"initialDelaySeconds should be 5, got {probe.get('initialDelaySeconds')}"
    assert probe.get("periodSeconds") == 10, f"periodSeconds should be 10, got {probe.get('periodSeconds')}"
    assert probe.get("failureThreshold") == 3, f"failureThreshold should be 3, got {probe.get('failureThreshold')}"


@pytest.mark.ac5
def test_ac5_resource_limits_and_requests(deployment):
    """AC-5: Resource requests/limits are within required ranges"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})

    # Check CPU request >=50m
    cpu_request = requests.get("cpu", "0m")
    if isinstance(cpu_request, str) and "m" in cpu_request:
        cpu_request_val = int(cpu_request.replace("m", ""))
    else:
        cpu_request_val = float(cpu_request) * 1000
    assert cpu_request_val >= 50, f"CPU request should be at least 50m, got {cpu_request}"

    # Check memory request >=64Mi
    mem_request = requests.get("memory", "0Mi")
    if isinstance(mem_request, str) and "Mi" in mem_request:
        mem_request_val = int(mem_request.replace("Mi", ""))
    elif isinstance(mem_request, str) and "Gi" in mem_request:
        mem_request_val = int(mem_request.replace("Gi", "")) * 1024
    assert mem_request_val >= 64, f"Memory request should be at least 64Mi, got {mem_request}"

    # Check CPU limit <=1000m
    cpu_limit = limits.get("cpu", "0m")
    if isinstance(cpu_limit, str) and "m" in cpu_limit:
        cpu_limit_val = int(cpu_limit.replace("m", ""))
    else:
        cpu_limit_val = float(cpu_limit) * 1000
    assert cpu_limit_val <= 1000, f"CPU limit should be at most 1000m (1 CPU), got {cpu_limit}"

    # Check memory limit <=512Mi
    mem_limit = limits.get("memory", "0Mi")
    if isinstance(mem_limit, str) and "Mi" in mem_request:
        mem_limit_val = int(mem_limit.replace("Mi", ""))
    elif isinstance(mem_limit, str) and "Gi" in mem_limit:
        mem_limit_val = int(mem_limit.replace("Gi", "")) * 1024
    assert mem_limit_val <= 512, f"Memory limit should be at most 512Mi, got {mem_limit}"


@pytest.mark.ac6
def test_ac6_environment_variables_configuration(deployment):
    """AC-6: All required environment variables are exposed as configurable"""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_vars = container.get("env", [])
    env_names = [e["name"] for e in env_vars]

    required_vars = [
        "KAFKA_BROKERS",
        "KAFKA_TOPICS",
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    ]

    for var in required_vars:
        assert var in env_names, f"Required environment variable {var} not found in deployment"

        # Check no hardcoded values (should use valueFrom or placeholder)
        env_entry = next(e for e in env_vars if e["name"] == var)
        assert "value" not in env_entry or len(env_entry["value"]) == 0 or env_entry["value"].startswith("${"), \
            f"Environment variable {var} should not have hardcoded value, use valueFrom or template placeholder"


@pytest.mark.ac7
def test_ac7_service_configuration(deployment, service):
    """AC-7: Service manifest correctly exposes metrics endpoint with matching selector"""
    # Check service spec
    assert service["apiVersion"] == "v1"
    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "kafka-collector"

    assert service["spec"]["type"] == "ClusterIP", "Service should be ClusterIP type as it is internal only"

    ports = service["spec"].get("ports", [])
    assert len(ports) == 1, "Service should expose exactly one port"
    metrics_port = ports[0]

    assert metrics_port["port"] == 8080, f"Service should expose port 8080, got {metrics_port['port']}"
    assert metrics_port["targetPort"] == 8080, f"Service targetPort should be 8080, got {metrics_port['targetPort']}"
    assert metrics_port["name"] == "http-metrics", f"Service port should be named http-metrics, got {metrics_port.get('name')}"

    # Check selector matches deployment pod labels
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]
    service_selector = service["spec"]["selector"]

    for key, val in service_selector.items():
        assert pod_labels.get(key) == val, f"Service selector {key}={val} does not match pod labels"


@pytest.mark.ac8
def test_ac8_no_elevated_rbac_permissions(deployment):
    """AC-8: Deployment does not use elevated RBAC permissions, uses default service account unless specified"""
    pod_spec = deployment["spec"]["template"]["spec"]
    service_account = pod_spec.get("serviceAccountName", "default")
    # If non-default service account is used, check if it's explicitly mentioned (but per spec, no extra RBAC)
    # We assume default service account unless explicitly configured otherwise, which is allowed per AC
    assert service_account == "default" or "serviceAccountName" not in pod_spec, \
        "Deployment should use default service account unless explicitly required"
