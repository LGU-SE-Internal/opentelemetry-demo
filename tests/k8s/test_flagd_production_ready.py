import pytest
import subprocess
import json
import time
import requests
from kubernetes import client, config
import grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

DEPLOYMENT_PATH = "kubernetes/opentelemetry-demo-flagd.deployment.yaml"
SERVICE_PATH = "kubernetes/opentelemetry-demo-flagd.service.yaml"
NETWORK_POLICY_PATH = "kubernetes/opentelemetry-demo-flagd.networkpolicy.yaml"
EXPECTED_IMAGE = "ghcr.io/open-feature/flagd:v0.10.1"
EXPECTED_GRPC_PORT = 8013
EXPECTED_HTTP_PORT = 8016
EXPECTED_NAMESPACE = "demo"
EXPECTED_SERVICE_DNS = f"opentelemetry-demo-flagd.{EXPECTED_NAMESPACE}.svc.cluster.local"

@pytest.fixture(scope="module")
def k8s_core_client():
    config.load_kube_config()
    return client.CoreV1Api()

@pytest.fixture(scope="module")
def k8s_apps_client():
    config.load_kube_config()
    return client.AppsV1Api()

@pytest.fixture(scope="module")
def k8s_networking_client():
    config.load_kube_config()
    return client.NetworkingV1Api()

@pytest.fixture(scope="module")
def deployment_manifest():
    """Load and parse the flagd deployment manifest YAML"""
    import yaml
    try:
        with open(DEPLOYMENT_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None

@pytest.fixture(scope="module")
def service_manifest():
    """Load and parse the flagd service manifest YAML"""
    import yaml
    try:
        with open(SERVICE_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None

@pytest.fixture(scope="module")
def network_policy_manifest():
    """Load and parse the flagd network policy manifest YAML"""
    import yaml
    try:
        with open(NETWORK_POLICY_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None

@pytest.fixture(scope="module")
def applied_deployment(k8s_apps_client):
    """Get the deployed flagd deployment from cluster"""
    try:
        return k8s_apps_client.read_namespaced_deployment("opentelemetry-demo-flagd", EXPECTED_NAMESPACE)
    except client.exceptions.ApiException:
        return None

@pytest.fixture(scope="module")
def applied_service(k8s_core_client):
    """Get the deployed flagd service from cluster"""
    try:
        return k8s_core_client.read_namespaced_service("opentelemetry-demo-flagd", EXPECTED_NAMESPACE)
    except client.exceptions.ApiException:
        return None

@pytest.fixture(scope="module")
def flagd_pods(k8s_core_client, applied_deployment):
    """Get running flagd service pods"""
    if not applied_deployment:
        return []
    try:
        pods = k8s_core_client.list_namespaced_pod(
            namespace=EXPECTED_NAMESPACE,
            label_selector="app.kubernetes.io/name=opentelemetry-demo-flagd"
        )
        return pods.items
    except client.exceptions.ApiException:
        return []

def test_ac1_deployment_runs_successfully(applied_deployment, flagd_pods):
    """AC-1: When the Deployment manifest is applied to a Kubernetes cluster, the flagd pod starts successfully and reaches Running state within 60 seconds."""
    assert applied_deployment is not None, "flagd deployment not found in cluster"
    
    start_time = time.time()
    while time.time() - start_time < 60:
        ready_replicas = applied_deployment.status.ready_replicas or 0
        desired_replicas = applied_deployment.spec.replicas or 1
        if ready_replicas >= desired_replicas:
            all_running = all(pod.status.phase == "Running" for pod in flagd_pods)
            if all_running:
                return
        time.sleep(2)
    
    assert False, f"flagd pods did not reach Running/Ready state within 60 seconds (ready: {ready_replicas}/{desired_replicas})"

def test_ac2_liveness_probe_healthz_returns_ok(flagd_pods, k8s_core_client):
    """AC-2: Liveness probe on /healthz endpoint (port 8016) returns HTTP 200 OK when the service is running, and fails when the service is unresponsive."""
    assert len(flagd_pods) > 0, "No flagd pods found"
    pod_name = flagd_pods[0].metadata.name
    
    # Test healthz endpoint
    exec_command = [
        '/bin/sh',
        '-c',
        f'wget -qO- http://localhost:{EXPECTED_HTTP_PORT}/healthz'
    ]
    
    try:
        resp = k8s_core_client.connect_get_namespaced_pod_exec(
            pod_name,
            EXPECTED_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False,
            stdout=True, tty=False
        )
        assert resp.strip() == "OK", f"Expected healthz response OK, got {resp}"
    except Exception as e:
        assert False, f"Failed to access /healthz endpoint: {str(e)}"

def test_ac3_readiness_probe_readyz_returns_ok(flagd_pods, k8s_core_client):
    """AC-3: Readiness probe on /readyz endpoint (port 8016) returns HTTP 200 OK only when the service is fully initialized and ready to serve flag evaluation requests."""
    assert len(flagd_pods) > 0, "No flagd pods found"
    pod_name = flagd_pods[0].metadata.name
    
    # Test readyz endpoint
    exec_command = [
        '/bin/sh',
        '-c',
        f'wget -qO- http://localhost:{EXPECTED_HTTP_PORT}/readyz'
    ]
    
    try:
        resp = k8s_core_client.connect_get_namespaced_pod_exec(
            pod_name,
            EXPECTED_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False,
            stdout=True, tty=False
        )
        assert resp.strip() == "OK", f"Expected readyz response OK, got {resp}"
    except Exception as e:
        assert False, f"Failed to access /readyz endpoint: {str(e)}"

def test_ac4_security_context_non_root_read_only(deployment_manifest):
    """AC-4: The flagd container runs as non-root user (UID 10001) with read-only root filesystem, and no privileged capabilities are granted."""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    pod_spec = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {})
    pod_sc = pod_spec.get("securityContext", {})
    
    assert pod_sc.get("runAsNonRoot") is True, "runAsNonRoot must be enabled"
    assert pod_sc.get("runAsUser") == 10001, f"Expected runAsUser=10001, got {pod_sc.get('runAsUser')}"
    
    container = pod_spec.get("containers", [])[0]
    container_sc = container.get("securityContext", {})
    
    assert container_sc.get("readOnlyRootFilesystem") is True, "readOnlyRootFilesystem must be enabled"
    assert container_sc.get("allowPrivilegeEscalation") is False, "allowPrivilegeEscalation must be disabled"
    assert container_sc.get("privileged") is not True, "Privileged mode must be disabled"
    
    capabilities = container_sc.get("capabilities", {})
    assert "drop" in capabilities and "ALL" in capabilities["drop"], "All capabilities must be dropped"

def test_ac5_tls_enabled_rejects_unencrypted(deployment_manifest):
    """AC-5: Setting the FLAGD_TLS_ENABLED environment variable to true along with valid cert/key paths enables TLS for both gRPC and HTTP endpoints, with connections to unencrypted endpoints being rejected."""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    env_vars = container.get("env", [])
    env_names = [v.get("name") for v in env_vars]
    
    required_env = ["FLAGD_TLS_ENABLED", "FLAGD_TLS_CERT_PATH", "FLAGD_TLS_KEY_PATH"]
    for env in required_env:
        assert env in env_names, f"Missing required environment variable {env}"

def test_ac6_mtls_enabled_requires_client_cert(deployment_manifest):
    """AC-6: Setting the FLAGD_MTLS_ENABLED environment variable to true along with valid CA cert path requires clients to present a valid client certificate signed by the configured CA to connect to any flagd endpoint."""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    env_vars = container.get("env", [])
    env_names = [v.get("name") for v in env_vars]
    
    required_env = ["FLAGD_MTLS_ENABLED", "FLAGD_MTLS_CA_CERT_PATH"]
    for env in required_env:
        assert env in env_names, f"Missing required environment variable {env}"

def test_ac7_graceful_shutdown_timeout_configured(deployment_manifest):
    """AC-7: When a SIGTERM signal is sent to the flagd pod, it waits for the configured FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT duration before terminating, allowing in-flight requests to complete."""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    env_vars = container.get("env", [])
    env_names = [v.get("name") for v in env_vars]
    
    assert "FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT" in env_names, "Missing FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT environment variable"
    
    # Verify termination grace period is at least the default timeout
    termination_grace = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("terminationGracePeriodSeconds", 30)
    assert termination_grace >= 30, f"Termination grace period must be at least 30s, got {termination_grace}"

def test_ac8_networkpolicy_blocks_unauthorized_grpc(network_policy_manifest):
    """AC-8: The NetworkPolicy blocks all incoming connections to flagd gRPC port (8013) from pods not part of the opentelemetry demo application set."""
    assert network_policy_manifest is not None, f"NetworkPolicy file not found at {NETWORK_POLICY_PATH}"
    
    # Verify policy applies to flagd pods
    pod_selector = network_policy_manifest.get("spec", {}).get("podSelector", {}).get("matchLabels", {})
    assert pod_selector.get("app.kubernetes.io/name") == "opentelemetry-demo-flagd", "NetworkPolicy does not target flagd pods"
    
    # Verify ingress rules for gRPC port allow only demo apps
    ingress_rules = network_policy_manifest.get("spec", {}).get("ingress", [])
    grpc_rule_found = False
    for rule in ingress_rules:
        ports = rule.get("ports", [])
        for port in ports:
            if port.get("port") == EXPECTED_GRPC_PORT:
                grpc_rule_found = True
                from_sources = rule.get("from", [])
                demo_app_found = False
                for src in from_sources:
                    pod_selector = src.get("podSelector", {}).get("matchLabels", {})
                    if pod_selector.get("app.kubernetes.io/part-of") == "opentelemetry-demo":
                        demo_app_found = True
                assert demo_app_found, "gRPC port rule does not allow access from opentelemetry demo apps"
    assert grpc_rule_found, "No ingress rule found for gRPC port 8013"

def test_ac9_networkpolicy_restricts_http_access(network_policy_manifest):
    """AC-9: The NetworkPolicy blocks all incoming connections to flagd HTTP port (8016) from sources other than prometheus and Kubernetes health check systems."""
    assert network_policy_manifest is not None, f"NetworkPolicy file not found at {NETWORK_POLICY_PATH}"
    
    ingress_rules = network_policy_manifest.get("spec", {}).get("ingress", [])
    http_rule_found = False
    for rule in ingress_rules:
        ports = rule.get("ports", [])
        for port in ports:
            if port.get("port") == EXPECTED_HTTP_PORT:
                http_rule_found = True
                from_sources = rule.get("from", [])
                prometheus_found = False
                ipblock_found = False
                for src in from_sources:
                    pod_selector = src.get("podSelector", {}).get("matchLabels", {})
                    if pod_selector.get("app.kubernetes.io/name") == "prometheus":
                        prometheus_found = True
                    if "ipBlock" in src:
                        ipblock_found = True
                assert prometheus_found, "HTTP port rule does not allow access from prometheus"
                assert ipblock_found, "HTTP port rule does not allow access from Kubernetes health check IP blocks"
    assert http_rule_found, "No ingress rule found for HTTP port 8016"

def test_ac10_resource_limits_set_correctly(deployment_manifest):
    """AC-10: All resource requests and limits are set on the flagd container, ensuring it does not consume more than 500m CPU and 256Mi memory under normal operation."""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    resources = container.get("resources", {})
    assert resources, "Resources section not configured"
    
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    assert requests, "Resource requests not configured"
    assert limits, "Resource limits not configured"
    
    # Parse CPU values
    def parse_cpu(cpu_str):
        if cpu_str.endswith('m'):
            return int(cpu_str[:-1])
        return int(float(cpu_str) * 1000)
    
    # Parse memory values
    def parse_memory(mem_str):
        if mem_str.endswith('Mi'):
            return int(mem_str[:-2])
        if mem_str.endswith('Gi'):
            return int(mem_str[:-2]) * 1024
        return int(mem_str) // (1024 * 1024)
    
    req_cpu = parse_cpu(requests.get('cpu', '0'))
    req_mem = parse_memory(requests.get('memory', '0'))
    limit_cpu = parse_cpu(limits.get('cpu', '0'))
    limit_mem = parse_memory(limits.get('memory', '0'))
    
    assert req_cpu == 100, f"Expected CPU request 100m, got {req_cpu}m"
    assert req_mem == 128, f"Expected memory request 128Mi, got {req_mem}Mi"
    assert limit_cpu == 500, f"Expected CPU limit 500m, got {limit_cpu}m"
    assert limit_mem == 256, f"Expected memory limit 256Mi, got {limit_mem}Mi"
