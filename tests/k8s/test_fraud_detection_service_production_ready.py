import pytest
import subprocess
import json
import time
from kubernetes import client, config
import requests

DEPLOYMENT_PATH = "kubernetes/fraud-detection-service.deployment.yaml"
SERVICE_PATH = "kubernetes/fraud-detection-service.service.yaml"
EXPECTED_NAMESPACE = "demo"
EXPECTED_GRPC_PORT = 8080
EXPECTED_HEALTH_PORT = 9090
EXPECTED_SERVICE_DNS = f"fraud-detection-service.{EXPECTED_NAMESPACE}.svc.cluster.local:{EXPECTED_GRPC_PORT}"

@pytest.fixture(scope="module")
def k8s_core_client():
    config.load_kube_config()
    return client.CoreV1Api()

@pytest.fixture(scope="module")
def k8s_apps_client():
    config.load_kube_config()
    return client.AppsV1Api()

@pytest.fixture(scope="module")
def deployment_manifest():
    """Load and parse the deployment manifest YAML"""
    import yaml
    try:
        with open(DEPLOYMENT_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None

@pytest.fixture(scope="module")
def service_manifest():
    """Load and parse the service manifest YAML"""
    import yaml
    try:
        with open(SERVICE_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None

@pytest.fixture(scope="module")
def applied_deployment(k8s_apps_client):
    """Get the deployed fraud detection service deployment from cluster"""
    try:
        return k8s_apps_client.read_namespaced_deployment("fraud-detection-service", EXPECTED_NAMESPACE)
    except client.exceptions.ApiException:
        return None

@pytest.fixture(scope="module")
def applied_service(k8s_core_client):
    """Get the deployed fraud detection service service from cluster"""
    try:
        return k8s_core_client.read_namespaced_service("fraud-detection-service", EXPECTED_NAMESPACE)
    except client.exceptions.ApiException:
        return None

@pytest.fixture(scope="module")
def fraud_detection_pods(k8s_core_client, applied_deployment):
    """Get running fraud detection service pods"""
    if not applied_deployment:
        return []
    try:
        pods = k8s_core_client.list_namespaced_pod(
            namespace=EXPECTED_NAMESPACE,
            label_selector="app.kubernetes.io/name=fraud-detection-service"
        )
        return pods.items
    except client.exceptions.ApiException:
        return []

def test_ac1_deployment_resource_limits(deployment_manifest):
    """AC-1: Deployment manifest exists with correct CPU/memory requests and limits"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    assert deployment_manifest.get("apiVersion") == "apps/v1", f"Expected apiVersion apps/v1, got {deployment_manifest.get('apiVersion')}"
    assert deployment_manifest.get("kind") == "Deployment", f"Expected kind Deployment, got {deployment_manifest.get('kind')}"
    assert deployment_manifest.get("metadata", {}).get("name") == "fraud-detection-service", "Deployment name should be fraud-detection-service"
    assert deployment_manifest.get("metadata", {}).get("namespace") == EXPECTED_NAMESPACE, f"Deployment namespace should be {EXPECTED_NAMESPACE}"

    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    assert container.get("name") == "fraud-detection-service", "Expected container name fraud-detection-service"
    
    resources = container.get("resources", {})
    assert resources, "Resources section not configured"
    
    requests = resources.get("requests", {})
    assert requests, "Resource requests not configured"
    limits = resources.get("limits", {})
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
    
    assert req_cpu == 100, f"CPU request should be 100m, got {req_cpu}m"
    assert req_mem == 256, f"Memory request should be 256Mi, got {req_mem}Mi"
    assert limit_cpu == 500, f"CPU limit should be 500m, got {limit_cpu}m"
    assert limit_mem == 512, f"Memory limit should be 512Mi, got {limit_mem}Mi"

def test_ac2_liveness_probe_configuration(deployment_manifest):
    """AC-2: Deployment includes livenessProbe targeting /q/health/live on port 9090 with correct timing"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    
    liveness_probe = container.get("livenessProbe")
    assert liveness_probe, "Liveness probe not configured"
    assert liveness_probe.get("httpGet"), "Liveness probe is not HTTP GET type"
    assert liveness_probe["httpGet"].get("path") == "/q/health/live", f"Liveness probe path should be /q/health/live"
    assert liveness_probe["httpGet"].get("port") == EXPECTED_HEALTH_PORT, f"Liveness probe port should be {EXPECTED_HEALTH_PORT}"
    assert liveness_probe.get("initialDelaySeconds") == 10, "Liveness probe initial delay should be 10 seconds"
    assert liveness_probe.get("periodSeconds") == 5, "Liveness probe period should be 5 seconds"

def test_ac3_readiness_probe_configuration(deployment_manifest):
    """AC-3: Deployment includes readinessProbe targeting /q/health/ready on port 9090 with correct timing"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    
    readiness_probe = container.get("readinessProbe")
    assert readiness_probe, "Readiness probe not configured"
    assert readiness_probe.get("httpGet"), "Readiness probe is not HTTP GET type"
    assert readiness_probe["httpGet"].get("path") == "/q/health/ready", f"Readiness probe path should be /q/health/ready"
    assert readiness_probe["httpGet"].get("port") == EXPECTED_HEALTH_PORT, f"Readiness probe port should be {EXPECTED_HEALTH_PORT}"
    assert readiness_probe.get("initialDelaySeconds") == 5, "Readiness probe initial delay should be 5 seconds"
    assert readiness_probe.get("periodSeconds") == 2, "Readiness probe period should be 2 seconds"

def test_ac4_security_context_configuration(deployment_manifest):
    """AC-4: Deployment pod security context configured for non-root least privilege execution"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    pod_spec = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {})
    pod_sc = pod_spec.get("securityContext", {})
    
    assert pod_sc.get("runAsNonRoot") is True, "runAsNonRoot must be true"
    assert pod_sc.get("runAsUser") == 10001, f"runAsUser should be 10001, got {pod_sc.get('runAsUser')}"
    
    container = pod_spec.get("containers", [])[0]
    container_sc = container.get("securityContext", {})
    
    assert container_sc.get("allowPrivilegeEscalation") is False, "allowPrivilegeEscalation must be false"
    assert container_sc.get("readOnlyRootFilesystem") is True, "readOnlyRootFilesystem must be true"
    
    capabilities = container_sc.get("capabilities", {})
    assert "drop" in capabilities, "Capabilities drop list not configured"
    assert "ALL" in capabilities["drop"], "All capabilities must be dropped"

def test_ac5_environment_variables_configuration(deployment_manifest):
    """AC-5: Deployment includes all required environment variables with correct values"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    env_vars = container.get("env", [])
    env_dict = {var.get("name"): var.get("value") for var in env_vars}
    
    required_vars = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otelcol:4317",
        "FEATURE_FLAG_SERVICE_ENDPOINT": "feature-flag-service:8080",
        "GRPC_PORT": "8080",
        "MANAGEMENT_PORT": "9090",
        "REQUEST_TIMEOUT_MS": "5000"
    }
    
    for var_name, expected_value in required_vars.items():
        assert var_name in env_dict, f"Missing required environment variable {var_name}"
        assert str(env_dict[var_name]) == str(expected_value), f"Environment variable {var_name} should be {expected_value}, got {env_dict[var_name]}"

def test_ac6_service_manifest_configuration(service_manifest):
    """AC-6: Service manifest exists, type ClusterIP, exposing port 8080 gRPC endpoint"""
    assert service_manifest is not None, f"Service file not found at {SERVICE_PATH}"
    assert service_manifest.get("apiVersion") == "v1", f"Expected apiVersion v1, got {service_manifest.get('apiVersion')}"
    assert service_manifest.get("kind") == "Service", f"Expected kind Service, got {service_manifest.get('kind')}"
    assert service_manifest.get("metadata", {}).get("name") == "fraud-detection-service", "Service name should be fraud-detection-service"
    assert service_manifest.get("metadata", {}).get("namespace") == EXPECTED_NAMESPACE, f"Service namespace should be {EXPECTED_NAMESPACE}"
    
    # Verify selector matches pod labels
    selector_labels = service_manifest.get("spec", {}).get("selector", {})
    assert selector_labels.get("app.kubernetes.io/name") == "fraud-detection-service", "Service selector does not match pod labels"
    
    # Verify port configuration
    ports = service_manifest.get("spec", {}).get("ports", [])
    assert len(ports) >= 1, "No ports configured on service"
    grpc_port = next(p for p in ports if p.get("name") == "grpc" or p.get("port") == EXPECTED_GRPC_PORT)
    assert grpc_port.get("port") == EXPECTED_GRPC_PORT, f"Service port expected {EXPECTED_GRPC_PORT}"
    assert grpc_port.get("targetPort") == EXPECTED_GRPC_PORT, f"Service targetPort expected {EXPECTED_GRPC_PORT}"
    
    # Verify service type
    assert service_manifest.get("spec", {}).get("type") == "ClusterIP", f"Service type should be ClusterIP, got {service_manifest.get('spec', {}).get('type')}"

def test_ac7_deployment_ready_and_service_reachable(applied_deployment, fraud_detection_pods, k8s_core_client):
    """AC-7: Deployment reaches ready state within 60s and service is reachable internally"""
    assert applied_deployment is not None, "Fraud detection service deployment not found in cluster"
    
    # Wait up to 60s for ready replicas
    start_time = time.time()
    ready_replicas = 0
    desired_replicas = applied_deployment.spec.replicas or 1
    while time.time() - start_time < 60:
        try:
            deployment = k8s_apps_client.read_namespaced_deployment("fraud-detection-service", EXPECTED_NAMESPACE)
            ready_replicas = deployment.status.ready_replicas or 0
            if ready_replicas >= desired_replicas:
                # Verify pods are actually ready
                all_ready = True
                pods = k8s_core_client.list_namespaced_pod(
                    namespace=EXPECTED_NAMESPACE,
                    label_selector="app.kubernetes.io/name=fraud-detection-service"
                ).items
                for pod in pods:
                    for cond in pod.status.conditions:
                        if cond.type == "Ready" and cond.status != "True":
                            all_ready = False
                            break
                    if not all_ready:
                        break
                if all_ready:
                    break
        except Exception:
            pass
        time.sleep(2)
    
    assert ready_replicas >= desired_replicas, f"Fraud detection service pods did not reach Ready state within 60 seconds (ready: {ready_replicas}/{desired_replicas})"
    
    # Test service reachability via port forward or exec
    assert len(fraud_detection_pods) > 0, "No fraud detection service pods found"
    
    # Test health endpoint from within the pod
    exec_command = [
        '/bin/sh',
        '-c',
        f'wget -qO- http://localhost:{EXPECTED_HEALTH_PORT}/q/health/live || curl -s http://localhost:{EXPECTED_HEALTH_PORT}/q/health/live'
    ]
    
    try:
        pod_name = fraud_detection_pods[0].metadata.name
        resp = k8s_core_client.connect_get_namespaced_pod_exec(
            pod_name,
            EXPECTED_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False,
            stdout=True, tty=False
        )
        assert "UP" in resp, f"Health endpoint returned unhealthy status: {resp}"
    except Exception as e:
        assert False, f"Failed to access service health endpoint: {str(e)}"
