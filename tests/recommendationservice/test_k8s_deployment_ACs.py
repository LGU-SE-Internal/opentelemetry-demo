#!/usr/bin/env python3
import os
import yaml
import subprocess
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

DEPLOYMENT_PATH = "./src/recommendation/k8s/deployment.yaml"
SERVICE_PATH = "./src/recommendation/k8s/service.yaml"
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")
SERVICE_NAME = "recommendationservice"
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
def kube_clients():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api()

@pytest.mark.ac1
def test_ac1_resource_limits_configurable_defaults(load_deployment_manifest):
    """AC-1: Deployment has configurable CPU/memory requests and limits with sensible production defaults"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    rec_container = next(c for c in containers if c["name"] == SERVICE_NAME)
    
    resources = rec_container["resources"]
    assert resources["requests"]["cpu"] == "100m", "Default CPU request should be 100m"
    assert resources["requests"]["memory"] == "128Mi", "Default memory request should be 128Mi"
    assert resources["limits"]["cpu"] == "200m", "Default CPU limit should be 200m"
    assert resources["limits"]["memory"] == "256Mi", "Default memory limit should be 256Mi"
    
    # Verify manifest is templatable for kustomize/Helm
    result = subprocess.run(
        ["kubectl", "kustomize", os.path.dirname(DEPLOYMENT_PATH)],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Manifest should be compatible with kustomize patches: {result.stderr}"

@pytest.mark.ac2
def test_ac2_grpc_probes_configured_correctly(load_deployment_manifest):
    """AC-2: Deployment has liveness and readiness probes configured to use existing gRPC health endpoint"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    rec_container = next(c for c in containers if c["name"] == SERVICE_NAME)
    
    # Test liveness probe
    liveness = rec_container["livenessProbe"]
    assert "grpc" in liveness, "Liveness probe must be gRPC type"
    assert liveness["grpc"]["port"] == 8080, "Liveness probe gRPC port should be 8080"
    assert liveness["grpc"]["service"] == SERVICE_NAME, f"Liveness probe service should be {SERVICE_NAME}"
    assert liveness["initialDelaySeconds"] == 5, "Liveness initialDelaySeconds should be 5"
    assert liveness["periodSeconds"] == 10, "Liveness periodSeconds should be 10"
    assert liveness["timeoutSeconds"] == 1, "Liveness timeoutSeconds should be 1"
    assert liveness["failureThreshold"] == 3, "Liveness failureThreshold should be 3"
    
    # Test readiness probe
    readiness = rec_container["readinessProbe"]
    assert "grpc" in readiness, "Readiness probe must be gRPC type"
    assert readiness["grpc"]["port"] == 8080, "Readiness probe gRPC port should be 8080"
    assert readiness["grpc"]["service"] == SERVICE_NAME, f"Readiness probe service should be {SERVICE_NAME}"
    assert readiness["initialDelaySeconds"] == 2, "Readiness initialDelaySeconds should be 2"
    assert readiness["periodSeconds"] == 5, "Readiness periodSeconds should be 5"
    assert readiness["timeoutSeconds"] == 1, "Readiness timeoutSeconds should be 1"
    assert readiness["failureThreshold"] == 3, "Readiness failureThreshold should be 3"

@pytest.mark.ac3
def test_ac3_security_context_non_root_hardened(load_deployment_manifest):
    """AC-3: Deployment runs with non-root security context with no elevated privileges"""
    pod_spec = load_deployment_manifest["spec"]["template"]["spec"]
    pod_sc = pod_spec["securityContext"]
    
    assert pod_sc["runAsNonRoot"] == True, "runAsNonRoot must be enabled"
    assert pod_sc["runAsUser"] == 1000, "runAsUser must be non-privileged UID 1000"
    assert pod_sc["runAsUser"] > 0, "runAsUser must not be root (UID 0)"
    assert pod_sc["runAsUser"] <= 65535, "runAsUser must be valid UID <= 65535"
    
    container_sc = next(c for c in pod_spec["containers"] if c["name"] == SERVICE_NAME)["securityContext"]
    assert container_sc["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation must be disabled"
    assert container_sc["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem must be enabled"
    assert "ALL" in container_sc["capabilities"]["drop"], "All capabilities must be dropped"

@pytest.mark.ac4
def test_ac4_service_exposes_grpc_port(load_service_manifest):
    """AC-4: Service manifest exists exposing gRPC port 8080 for internal cluster access"""
    assert load_service_manifest["metadata"]["name"] == SERVICE_NAME, f"Service name must be {SERVICE_NAME}"
    assert load_service_manifest["spec"]["type"] == "ClusterIP", "Service type must be ClusterIP for internal access only"
    
    ports = load_service_manifest["spec"]["ports"]
    port_map = {p["port"]: p["targetPort"] for p in ports}
    
    assert 8080 in port_map, "gRPC port 8080 not exposed"
    assert port_map[8080] == 8080, "gRPC targetPort must match container port 8080"
    
    selector = load_service_manifest["spec"]["selector"]
    assert selector["app.kubernetes.io/name"] == SERVICE_NAME, "Service selector must match deployment labels"

@pytest.mark.ac5
def test_ac5_environment_variables_configurable(load_deployment_manifest):
    """AC-5: All existing service runtime parameters are configurable via environment variables"""
    containers = load_deployment_manifest["spec"]["template"]["spec"]["containers"]
    rec_container = next(c for c in containers if c["name"] == SERVICE_NAME)
    env_vars = {env["name"]: env.get("valueFrom", env.get("value", None)) for env in rec_container["env"]}
    
    required_env_vars = [
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "TLS_ENABLED",
        "RETRY_MAX_ATTEMPTS",
        "UPSTREAM_CATALOG_SERVICE_ADDR"
    ]
    
    for var in required_env_vars:
        assert var in env_vars, f"Required service parameter {var} is not exposed as environment variable"
        # Verify variables are templatable for overrides
        assert isinstance(env_vars[var], str), f"Environment variable {var} should be configurable"

@pytest.mark.ac6
def test_ac6_standard_kubernetes_labels(load_deployment_manifest, load_service_manifest):
    """AC-6: All resources have standard Kubernetes labels matching opentelemetry-demo conventions"""
    # Check deployment labels
    deployment_labels = load_deployment_manifest["metadata"]["labels"]
    assert deployment_labels["app.kubernetes.io/name"] == SERVICE_NAME
    assert deployment_labels["app.kubernetes.io/part-of"] == "opentelemetry-demo"
    assert deployment_labels["app.kubernetes.io/component"] == "service"
    
    # Check pod template labels
    pod_labels = load_deployment_manifest["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["app.kubernetes.io/name"] == SERVICE_NAME
    assert pod_labels["app.kubernetes.io/part-of"] == "opentelemetry-demo"
    assert pod_labels["app.kubernetes.io/component"] == "service"
    
    # Check service labels
    service_labels = load_service_manifest["metadata"]["labels"]
    assert service_labels["app.kubernetes.io/name"] == SERVICE_NAME
    assert service_labels["app.kubernetes.io/part-of"] == "opentelemetry-demo"
    assert service_labels["app.kubernetes.io/component"] == "service"

@pytest.mark.ac3_e2e
def test_ac3_pod_runs_as_non_root_user(kube_clients):
    """AC-3 E2E: Verify running container process is not root user"""
    core_v1, _ = kube_clients
    start_time = time.time()
    pod_name = None
    
    while time.time() - start_time < 60:
        try:
            pods = core_v1.list_namespaced_pod(
                namespace=NAMESPACE,
                label_selector=f"app.kubernetes.io/name={SERVICE_NAME}",
                field_selector="status.phase=Running"
            )
            if pods.items:
                pod_name = pods.items[0].metadata.name
                break
            time.sleep(2)
        except ApiException:
            time.sleep(2)
    
    if not pod_name:
        pytest.fail(f"No running {SERVICE_NAME} pods found to verify user context")
    
    # Exec into pod to check running user
    exec_command = [
        "/bin/sh",
        "-c",
        "id -u"
    ]
    
    resp = core_v1.connect_get_namespaced_pod_exec(
        pod_name,
        NAMESPACE,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    
    uid = int(resp.strip())
    assert uid == 1000, f"Container running as UID {uid}, expected non-root UID 1000"
    assert uid != 0, "Container must not run as root (UID 0)"

@pytest.mark.ac4_e2e
def test_ac4_service_accessible_via_cluster_dns():
    """AC-4 E2E: Verify service is reachable via cluster DNS on port 8080"""
    service_addr = f"{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:8080"
    
    try:
        # Test gRPC health check
        channel = grpc.insecure_channel(service_addr)
        stub = health_pb2_grpc.HealthStub(channel)
        response = stub.Check(health_pb2.HealthCheckRequest(service=SERVICE_NAME), timeout=5)
        assert response.status == health_pb2.HealthCheckResponse.SERVING, "Service health check failed"
    except Exception as e:
        pytest.fail(f"Failed to reach {SERVICE_NAME} via cluster DNS: {str(e)}")
