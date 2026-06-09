import pytest
import subprocess
import json
import time
from kubernetes import client, config
import grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

DEPLOYMENT_PATH = "kubernetes/paymentservice.deployment.yaml"
SERVICE_PATH = "kubernetes/paymentservice.service.yaml"
EXPECTED_IMAGE = "ghcr.io/lgu-se-internal/opentelemetry-demo/paymentservice:latest"
EXPECTED_PORT = 50051
EXPECTED_NAMESPACE = "default"
EXPECTED_SERVICE_DNS = f"paymentservice.{EXPECTED_NAMESPACE}.svc.cluster.local:{EXPECTED_PORT}"

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
    """Get the deployed payment service deployment from cluster"""
    try:
        return k8s_apps_client.read_namespaced_deployment("paymentservice", EXPECTED_NAMESPACE)
    except client.exceptions.ApiException:
        return None

@pytest.fixture(scope="module")
def applied_service(k8s_core_client):
    """Get the deployed payment service service from cluster"""
    try:
        return k8s_core_client.read_namespaced_service("paymentservice", EXPECTED_NAMESPACE)
    except client.exceptions.ApiException:
        return None

@pytest.fixture(scope="module")
def payment_pods(k8s_core_client, applied_deployment):
    """Get running payment service pods"""
    if not applied_deployment:
        return []
    try:
        pods = k8s_core_client.list_namespaced_pod(
            namespace=EXPECTED_NAMESPACE,
            label_selector="app.kubernetes.io/name=paymentservice"
        )
        return pods.items
    except client.exceptions.ApiException:
        return []

def test_ac1_deployment_manifest_valid_schema(deployment_manifest):
    """AC-1: Payment service Deployment manifest exists at correct path with valid apps/v1 schema and matching selector"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    assert deployment_manifest.get("apiVersion") == "apps/v1", f"Expected apiVersion apps/v1, got {deployment_manifest.get('apiVersion')}"
    assert deployment_manifest.get("kind") == "Deployment", f"Expected kind Deployment, got {deployment_manifest.get('kind')}"
    
    # Verify metadata labels
    metadata_labels = deployment_manifest.get("metadata", {}).get("labels", {})
    assert metadata_labels.get("app.kubernetes.io/name") == "paymentservice", "Missing or incorrect app.kubernetes.io/name label"
    assert metadata_labels.get("app.kubernetes.io/part-of") == "opentelemetry-demo", "Missing or incorrect app.kubernetes.io/part-of label"
    
    # Verify selector matches pod template labels
    selector_labels = deployment_manifest.get("spec", {}).get("selector", {}).get("matchLabels", {})
    pod_labels = deployment_manifest.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
    assert selector_labels.items() <= pod_labels.items(), "Deployment selector does not match pod template labels"

def test_ac2_resource_configuration(deployment_manifest):
    """AC-2: Deployment defines resource requests (min 100m CPU / 128Mi memory) and limits (max 200m CPU / 256Mi memory)"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    assert container.get("name") == "paymentservice", "Expected container name paymentservice"
    
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
    
    assert req_cpu >= 100, f"CPU request {req_cpu}m below minimum 100m"
    assert req_mem >= 128, f"Memory request {req_mem}Mi below minimum 128Mi"
    assert limit_cpu <= 200, f"CPU limit {limit_cpu}m above maximum 200m"
    assert limit_mem <= 256, f"Memory limit {limit_mem}Mi above maximum 256Mi"
    assert limit_cpu >= req_cpu, "CPU limit less than request"
    assert limit_mem >= req_mem, "Memory limit less than request"

def test_ac3_probe_configuration(deployment_manifest):
    """AC-3: Deployment includes gRPC liveness/readiness probes on port 50051 with correct timing parameters"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    
    liveness_probe = container.get("livenessProbe")
    assert liveness_probe, "Liveness probe not configured"
    assert liveness_probe.get("grpc"), "Liveness probe is not gRPC type"
    assert liveness_probe["grpc"].get("port") == EXPECTED_PORT, f"Liveness probe port expected {EXPECTED_PORT}"
    assert liveness_probe.get("initialDelaySeconds") == 5, "Liveness probe initial delay should be 5 seconds"
    assert liveness_probe.get("periodSeconds") == 10, "Liveness probe period should be 10 seconds"
    assert liveness_probe.get("failureThreshold") == 3, "Liveness probe failure threshold should be 3"
    
    readiness_probe = container.get("readinessProbe")
    assert readiness_probe, "Readiness probe not configured"
    assert readiness_probe.get("grpc"), "Readiness probe is not gRPC type"
    assert readiness_probe["grpc"].get("port") == EXPECTED_PORT, f"Readiness probe port expected {EXPECTED_PORT}"
    assert readiness_probe.get("initialDelaySeconds") == 5, "Readiness probe initial delay should be 5 seconds"
    assert readiness_probe.get("periodSeconds") == 10, "Readiness probe period should be 10 seconds"
    assert readiness_probe.get("failureThreshold") == 3, "Readiness probe failure threshold should be 3"

def test_ac4_security_context_configuration(deployment_manifest):
    """AC-4: Pod security context configured for non-root execution with least privileges"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    pod_spec = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {})
    pod_sc = pod_spec.get("securityContext", {})
    
    assert pod_sc.get("runAsNonRoot") is True, "runAsNonRoot must be true"
    assert pod_sc.get("runAsUser") == 1000, f"runAsUser should be 1000, got {pod_sc.get('runAsUser')}"
    
    container = pod_spec.get("containers", [])[0]
    container_sc = container.get("securityContext", {})
    
    assert container_sc.get("allowPrivilegeEscalation") is False, "allowPrivilegeEscalation must be false"
    assert container_sc.get("readOnlyRootFilesystem") is True, "readOnlyRootFilesystem must be true"
    
    capabilities = container_sc.get("capabilities", {})
    assert "drop" in capabilities, "Capabilities drop list not configured"
    assert "ALL" in capabilities["drop"], "All capabilities must be dropped"

def test_ac5_environment_variables(deployment_manifest):
    """AC-5: Deployment includes all required environment variables for the service"""
    assert deployment_manifest is not None, f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    container = deployment_manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])[0]
    env_vars = container.get("env", [])
    env_names = [var.get("name") for var in env_vars]
    
    required_vars = ["OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME", "PAYMENT_SERVICE_PORT"]
    for var in required_vars:
        assert var in env_names, f"Missing required environment variable {var}"
    
    # Verify PAYMENT_SERVICE_PORT is set to correct value
    port_var = next(v for v in env_vars if v["name"] == "PAYMENT_SERVICE_PORT")
    assert str(port_var.get("value")) == str(EXPECTED_PORT), f"PAYMENT_SERVICE_PORT should be {EXPECTED_PORT}"
    assert port_var.get("valueFrom") is not None or port_var.get("value") is not None, "Environment variable has no value"

def test_ac6_service_manifest_valid_schema(service_manifest):
    """AC-6: Payment service Service manifest exists at correct path with valid v1 schema, matching selector, port 50051 ClusterIP"""
    assert service_manifest is not None, f"Service file not found at {SERVICE_PATH}"
    assert service_manifest.get("apiVersion") == "v1", f"Expected apiVersion v1, got {service_manifest.get('apiVersion')}"
    assert service_manifest.get("kind") == "Service", f"Expected kind Service, got {service_manifest.get('kind')}"
    
    # Verify metadata labels
    metadata_labels = service_manifest.get("metadata", {}).get("labels", {})
    assert metadata_labels.get("app.kubernetes.io/name") == "paymentservice", "Missing or incorrect app.kubernetes.io/name label"
    assert metadata_labels.get("app.kubernetes.io/part-of") == "opentelemetry-demo", "Missing or incorrect app.kubernetes.io/part-of label"
    
    # Verify selector matches pod labels
    selector_labels = service_manifest.get("spec", {}).get("selector", {})
    assert selector_labels.get("app.kubernetes.io/name") == "paymentservice", "Service selector does not match pod labels"
    
    # Verify port configuration
    ports = service_manifest.get("spec", {}).get("ports", [])
    assert len(ports) >= 1, "No ports configured on service"
    payment_port = next(p for p in ports if p.get("name") == "grpc" or p.get("port") == EXPECTED_PORT)
    assert payment_port.get("port") == EXPECTED_PORT, f"Service port expected {EXPECTED_PORT}"
    assert payment_port.get("targetPort") == EXPECTED_PORT, f"Service targetPort expected {EXPECTED_PORT}"
    
    # Verify service type
    assert service_manifest.get("spec", {}).get("type") == "ClusterIP", f"Service type should be ClusterIP, got {service_manifest.get('spec', {}).get('type')}"

def test_ac7_deployment_succeeds_pods_ready(applied_deployment, payment_pods):
    """AC-7: kubectl apply succeeds, all pods reach Ready state within 60 seconds"""
    assert applied_deployment is not None, "Payment service deployment not found in cluster"
    
    # Wait up to 60s for ready replicas
    start_time = time.time()
    while time.time() - start_time < 60:
        ready_replicas = applied_deployment.status.ready_replicas or 0
        desired_replicas = applied_deployment.spec.replicas or 1
        if ready_replicas >= desired_replicas:
            # Verify pods are actually ready
            all_ready = True
            for pod in payment_pods:
                for cond in pod.status.conditions:
                    if cond.type == "Ready" and cond.status != "True":
                        all_ready = False
                        break
                if not all_ready:
                    break
            if all_ready:
                return
        time.sleep(2)
    
    assert False, f"Payment service pods did not reach Ready state within 60 seconds (ready: {ready_replicas}/{desired_replicas})"

def test_ac8_service_reachable_grpc_health_ok(payment_pods, k8s_core_client):
    """AC-8: Payment service is reachable via service DNS and responds to gRPC health check"""
    assert len(payment_pods) > 0, "No payment service pods found"
    
    # Run gRPC health check from a temporary pod in the cluster to test DNS connectivity
    exec_command = [
        '/bin/sh',
        '-c',
        f'grpc-health-probe -addr={EXPECTED_SERVICE_DNS} -service=""'
    ]
    
    try:
        # Use one of the existing pods to exec the test (or create a temp one, but for simplicity use existing payment pod)
        pod_name = payment_pods[0].metadata.name
        resp = k8s_core_client.connect_get_namespaced_pod_exec(
            pod_name,
            EXPECTED_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False,
            stdout=True, tty=False
        )
        assert "SERVING" in resp, f"gRPC health check failed, response: {resp}"
    except Exception as e:
        assert False, f"Failed to reach payment service at {EXPECTED_SERVICE_DNS}: {str(e)}"
