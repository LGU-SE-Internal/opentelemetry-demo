#!/usr/bin/env python3
import os
import yaml

DEPLOYMENT_PATH = "./kubernetes/paymentservice.deployment.yaml"
SERVICE_PATH = "./kubernetes/paymentservice.service.yaml"

# Expected values from spec
EXPECTED_DEPLOYMENT_NAME = "paymentservice"
EXPECTED_PROBE_PORT = 50051
EXPECTED_PROBE_PATH = "/health"
EXPECTED_LIVENESS_PROBE_CONFIG = {
    "httpGet": {
        "path": EXPECTED_PROBE_PATH,
        "port": EXPECTED_PROBE_PORT
    },
    "initialDelaySeconds": 30,
    "periodSeconds": 10,
    "failureThreshold": 3
}
EXPECTED_READINESS_PROBE_CONFIG = {
    "httpGet": {
        "path": EXPECTED_PROBE_PATH,
        "port": EXPECTED_PROBE_PORT
    },
    "initialDelaySeconds": 5,
    "periodSeconds": 5,
    "failureThreshold": 2
}
EXPECTED_RESOURCES = {
    "requests": {
        "cpu": "100m",
        "memory": "128Mi"
    },
    "limits": {
        "cpu": "500m",
        "memory": "256Mi"
    }
}
EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "readOnlyRootFilesystem": True,
    "capabilities": {
        "drop": ["ALL"]
    }
}
EXPECTED_ENV_VARS = [
    "PORT",
    "TLS_ENABLED",
    "RATE_LIMIT_RPS",
    "OTEL_EXPORTER_OTLP_ENDPOINT"
]
EXPECTED_POD_ANNOTATIONS = {
    "prometheus.io/scrape": "true",
    "prometheus.io/port": "9464",
    "prometheus.io/path": "/metrics"
}
EXPECTED_CONTAINER_PORTS = [50051, 9464]
EXPECTED_SERVICE_PORTS = [50051, 9464]


def test_ac1_liveness_probe_configured_correctly():
    """AC-1: LivenessProbe configured with path /health, port 50051, initialDelaySeconds=30, periodSeconds=10, failureThreshold=3"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    assert dep["metadata"]["name"] == EXPECTED_DEPLOYMENT_NAME, f"Expected deployment name {EXPECTED_DEPLOYMENT_NAME}, got {dep.get('metadata', {}).get('name')}"
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    liveness_probe = container.get("livenessProbe", {})
    assert liveness_probe, "Missing livenessProbe configuration"
    
    # Check httpGet config
    assert "httpGet" in liveness_probe, "livenessProbe missing httpGet configuration"
    assert liveness_probe["httpGet"]["path"] == EXPECTED_LIVENESS_PROBE_CONFIG["httpGet"]["path"], f"livenessProbe path expected {EXPECTED_LIVENESS_PROBE_CONFIG['httpGet']['path']}, got {liveness_probe['httpGet']['path']}"
    assert liveness_probe["httpGet"]["port"] == EXPECTED_LIVENESS_PROBE_CONFIG["httpGet"]["port"], f"livenessProbe port expected {EXPECTED_LIVENESS_PROBE_CONFIG['httpGet']['port']}, got {liveness_probe['httpGet']['port']}"
    
    # Check timing parameters
    assert liveness_probe["initialDelaySeconds"] == EXPECTED_LIVENESS_PROBE_CONFIG["initialDelaySeconds"], f"livenessProbe initialDelaySeconds expected {EXPECTED_LIVENESS_PROBE_CONFIG['initialDelaySeconds']}, got {liveness_probe.get('initialDelaySeconds')}"
    assert liveness_probe["periodSeconds"] == EXPECTED_LIVENESS_PROBE_CONFIG["periodSeconds"], f"livenessProbe periodSeconds expected {EXPECTED_LIVENESS_PROBE_CONFIG['periodSeconds']}, got {liveness_probe.get('periodSeconds')}"
    assert liveness_probe["failureThreshold"] == EXPECTED_LIVENESS_PROBE_CONFIG["failureThreshold"], f"livenessProbe failureThreshold expected {EXPECTED_LIVENESS_PROBE_CONFIG['failureThreshold']}, got {liveness_probe.get('failureThreshold')}"


def test_ac2_readiness_probe_configured_correctly():
    """AC-2: ReadinessProbe configured with path /health, port 50051, initialDelaySeconds=5, periodSeconds=5, failureThreshold=2"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    readiness_probe = container.get("readinessProbe", {})
    assert readiness_probe, "Missing readinessProbe configuration"
    
    # Check httpGet config
    assert "httpGet" in readiness_probe, "readinessProbe missing httpGet configuration"
    assert readiness_probe["httpGet"]["path"] == EXPECTED_READINESS_PROBE_CONFIG["httpGet"]["path"], f"readinessProbe path expected {EXPECTED_READINESS_PROBE_CONFIG['httpGet']['path']}, got {readiness_probe['httpGet']['path']}"
    assert readiness_probe["httpGet"]["port"] == EXPECTED_READINESS_PROBE_CONFIG["httpGet"]["port"], f"readinessProbe port expected {EXPECTED_READINESS_PROBE_CONFIG['httpGet']['port']}, got {readiness_probe['httpGet']['port']}"
    
    # Check timing parameters
    assert readiness_probe["initialDelaySeconds"] == EXPECTED_READINESS_PROBE_CONFIG["initialDelaySeconds"], f"readinessProbe initialDelaySeconds expected {EXPECTED_READINESS_PROBE_CONFIG['initialDelaySeconds']}, got {readiness_probe.get('initialDelaySeconds')}"
    assert readiness_probe["periodSeconds"] == EXPECTED_READINESS_PROBE_CONFIG["periodSeconds"], f"readinessProbe periodSeconds expected {EXPECTED_READINESS_PROBE_CONFIG['periodSeconds']}, got {readiness_probe.get('periodSeconds')}"
    assert readiness_probe["failureThreshold"] == EXPECTED_READINESS_PROBE_CONFIG["failureThreshold"], f"readinessProbe failureThreshold expected {EXPECTED_READINESS_PROBE_CONFIG['failureThreshold']}, got {readiness_probe.get('failureThreshold')}"


def test_ac3_resource_requests_limits_configured():
    """AC-3: Resource requests set to >=100m CPU / >=128Mi memory, resource limits set to <=500m CPU / <=256Mi memory"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    # Check requests
    assert "requests" in resources, "Missing resource requests configuration"
    cpu_request = resources["requests"].get("cpu", "0m")
    mem_request = resources["requests"].get("memory", "0Mi")
    
    cpu_request_val = int(cpu_request.replace("m", ""))
    mem_request_val = int(mem_request.replace("Mi", ""))
    assert cpu_request_val >= 100, f"CPU request {cpu_request} is less than minimum 100m"
    assert mem_request_val >= 128, f"Memory request {mem_request} is less than minimum 128Mi"
    
    # Check limits
    assert "limits" in resources, "Missing resource limits configuration"
    cpu_limit = resources["limits"].get("cpu", "1000m")
    mem_limit = resources["limits"].get("memory", "1024Mi")
    
    cpu_limit_val = int(cpu_limit.replace("m", ""))
    mem_limit_val = int(mem_limit.replace("Mi", ""))
    assert cpu_limit_val <= 500, f"CPU limit {cpu_limit} exceeds maximum 500m"
    assert mem_limit_val <= 256, f"Memory limit {mem_limit} exceeds maximum 256Mi"


def test_ac4_security_context_configured():
    """AC-4: Security context with runAsNonRoot: true, runAsUser: 1000, readOnlyRootFilesystem: true, capabilities.drop includes ALL"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    security_context = container.get("securityContext", {})
    
    assert security_context.get("runAsNonRoot") == EXPECTED_SECURITY_CONTEXT["runAsNonRoot"], f"Expected runAsNonRoot={EXPECTED_SECURITY_CONTEXT['runAsNonRoot']}, got {security_context.get('runAsNonRoot')}"
    assert security_context.get("runAsUser") == EXPECTED_SECURITY_CONTEXT["runAsUser"], f"Expected runAsUser={EXPECTED_SECURITY_CONTEXT['runAsUser']}, got {security_context.get('runAsUser')}"
    assert security_context.get("readOnlyRootFilesystem") == EXPECTED_SECURITY_CONTEXT["readOnlyRootFilesystem"], f"Expected readOnlyRootFilesystem={EXPECTED_SECURITY_CONTEXT['readOnlyRootFilesystem']}, got {security_context.get('readOnlyRootFilesystem')}"
    
    assert "capabilities" in security_context, "Missing capabilities in security context"
    assert "drop" in security_context["capabilities"], "Missing drop list in capabilities"
    assert "ALL" in security_context["capabilities"]["drop"], "ALL not found in capabilities.drop list"


def test_ac5_required_environment_variables_configured():
    """AC-5: Deployment contains all required environment variables: PORT, TLS_ENABLED, RATE_LIMIT_RPS, OTEL_EXPORTER_OTLP_ENDPOINT"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_vars = [env["name"] for env in container.get("env", [])]
    
    for expected_env in EXPECTED_ENV_VARS:
        assert expected_env in env_vars, f"Missing required environment variable {expected_env}"


def test_ac6_prometheus_scrape_annotations_configured():
    """AC-6: Pod template includes three prometheus scrape annotations for OpenTelemetry metrics collection"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_annotations = dep["spec"]["template"]["metadata"].get("annotations", {})
    
    for k, v in EXPECTED_POD_ANNOTATIONS.items():
        assert k in pod_annotations, f"Missing pod annotation {k}"
        assert pod_annotations[k] == v, f"Pod annotation {k} expected {v}, got {pod_annotations[k]}"


def test_ac7_health_endpoint_returns_200_when_running(k8s_client):
    """AC-7: Health endpoint http://localhost:50051/health returns 200 status code when pod is running"""
    pods = k8s_client.list_namespaced_pod(namespace="default", label_selector=f"app={EXPECTED_DEPLOYMENT_NAME}").items
    assert len(pods) > 0, f"No running pods found for {EXPECTED_DEPLOYMENT_NAME}"
    
    pod = pods[0]
    assert pod.status.phase == "Running", f"Pod {pod.metadata.name} is not in Running state"
    
    # Exec into pod to check health endpoint
    exec_command = [
        "/bin/sh",
        "-c",
        f"curl -s -o /dev/null -w \"%{{http_code}}\" http://localhost:{EXPECTED_PROBE_PORT}{EXPECTED_PROBE_PATH}"
    ]
    
    resp = k8s_client.stream_namespaced_pod_exec(
        name=pod.metadata.name,
        namespace="default",
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    assert resp.strip() == "200", f"Expected health endpoint to return 200, got {resp.strip()}"


def test_ac8_non_root_user_and_readonly_root_filesystem(k8s_client):
    """AC-8: Pod runs as non-root user (uid 1000) and root filesystem is mounted read-only"""
    pods = k8s_client.list_namespaced_pod(namespace="default", label_selector=f"app={EXPECTED_DEPLOYMENT_NAME}").items
    assert len(pods) > 0, f"No running pods found for {EXPECTED_DEPLOYMENT_NAME}"
    
    pod = pods[0]
    assert pod.status.phase == "Running", f"Pod {pod.metadata.name} is not in Running state"
    
    # Check user ID
    exec_command_id = ["/bin/sh", "-c", "id -u"]
    id_resp = k8s_client.stream_namespaced_pod_exec(
        name=pod.metadata.name,
        namespace="default",
        command=exec_command_id,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    assert id_resp.strip() == "1000", f"Expected user ID 1000, got {id_resp.strip()}"
    
    # Check root filesystem mount
    exec_command_mount = ["/bin/sh", "-c", "mount | grep \" / \""]
    mount_resp = k8s_client.stream_namespaced_pod_exec(
        name=pod.metadata.name,
        namespace="default",
        command=exec_command_mount,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    assert "ro," in mount_resp.lower(), f"Root filesystem is not mounted read-only: {mount_resp.strip()}"


def test_ac9_service_ports_configured_correctly():
    """Verify service exposes required ports gRPC 50051 and Metrics 9464"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    
    ports = [p["port"] for p in svc.get("spec", {}).get("ports", [])]
    for expected_port in EXPECTED_SERVICE_PORTS:
        assert expected_port in ports, f"Missing service port {expected_port}"
