"""Integration tests for recommendation service Kubernetes manifests
"""
import os
import time
import grpc
from kubernetes import client, config
from grpc_health.v1 import health_pb2, health_pb2_grpc

# Configuration
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")
DEPLOYMENT_NAME = "otel-demo-recommendation"
SERVICE_NAME = "otel-demo-recommendation"
GRPC_PORT = 8080

# Load kube config
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()


def test_ac1_deployment_ready_within_60s():
    """AC-1: Deployment named otel-demo-recommendation exists and has at least 1 ready replica within 60s
    """
    end_time = time.time() + 60
    while time.time() < end_time:
        try:
            deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
            if deploy.status.ready_replicas and deploy.status.ready_replicas >= 1:
                return True
        except client.exceptions.ApiException:
            pass
        time.sleep(2)
    assert False, f"Deployment {DEPLOYMENT_NAME} did not have 1 ready replica within 60 seconds"


def test_ac2_resource_requests_limits_configured():
    """AC-2: Deployment has correct resource requests and limits as specified
    """
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    containers = deploy.spec.template.spec.containers
    assert len(containers) > 0, "Deployment has no containers"
    resources = containers[0].resources
    # Check requests
    assert resources.requests is not None, "No resource requests configured"
    assert resources.requests.get("cpu") == "100m", f"Expected CPU request 100m, got {resources.requests.get('cpu')}"
    assert resources.requests.get("memory") == "128Mi", f"Expected memory request 128Mi, got {resources.requests.get('memory')}"
    # Check limits
    assert resources.limits is not None, "No resource limits configured"
    assert resources.limits.get("cpu") == "300m", f"Expected CPU limit 300m, got {resources.limits.get('cpu')}"
    assert resources.limits.get("memory") == "256Mi", f"Expected memory limit 256Mi, got {resources.limits.get('memory')}"


def test_ac3_probes_configured():
    """AC-3: Liveness and readiness probes are correctly configured for gRPC health checks
    """
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    
    # Check liveness probe
    assert container.liveness_probe is not None, "No liveness probe configured"
    assert container.liveness_probe.grpc is not None, "Liveness probe is not gRPC"
    assert container.liveness_probe.grpc.port == GRPC_PORT, f"Liveness probe port wrong port {container.liveness_probe.grpc.port}, expected {GRPC_PORT}"
    assert container.liveness_probe.grpc.service == "recommendation", f"Liveness probe service wrong, expected recommendation"
    assert container.liveness_probe.initial_delay_seconds == 5, f"Liveness initial delay wrong, expected 5"
    assert container.liveness_probe.period_seconds == 10, f"Liveness period wrong, expected 10"
    
    # Check readiness probe
    assert container.readiness_probe is not None, "No readiness probe configured"
    assert container.readiness_probe.grpc is not None, "Readiness probe is not gRPC"
    assert container.readiness_probe.grpc.port == GRPC_PORT, f"Readiness probe port wrong port {container.readiness_probe.grpc.port}, expected {GRPC_PORT}"
    assert container.readiness_probe.grpc.service == "recommendation", f"Readiness probe service wrong, expected recommendation"
    assert container.readiness_probe.initial_delay_seconds == 2, f"Readiness initial delay wrong, expected 2"
    assert container.readiness_probe.period_seconds == 5, f"Readiness period wrong, expected 5"


def test_ac4_security_context_configured():
    """AC-4: Security context has all required flags set correctly
    """
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    security_context = deploy.spec.template.spec.security_context
    assert security_context is not None, "No pod security context configured"
    assert security_context.run_as_non_root == True, "runAsNonRoot should be true"
    assert security_context.run_as_user == 10001, f"runAsUser should be 10001, got {security_context.run_as_user}"
    assert security_context.read_only_root_filesystem == True, "readOnlyRootFilesystem should be true"
    assert security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation should be false"


def test_ac5_service_configured():
    """AC-5: ClusterIP Service exists with correct port mapping
    """
    svc = core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    assert svc.spec.type == "ClusterIP", f"Service type should be ClusterIP, got {svc.spec.type}"
    assert len(svc.spec.ports) > 0, "Service has no ports configured"
    port = svc.spec.ports[0]
    assert port.port == GRPC_PORT, f"Service port should be {GRPC_PORT}, got {port.port}"
    assert port.target_port == GRPC_PORT, f"Target port should be {GRPC_PORT}, got {port.target_port}"


def test_ac6_labels_match_pattern():
    """AC-6: Standard labels on Deployment and Service match existing service pattern
    """
    required_labels = [
        "app.kubernetes.io/name",
        "app.kubernetes.io/instance",
        "app.kubernetes.io/version",
        "opentelemetry.io/demo-service"
    ]
    
    deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    deploy_labels = deploy.metadata.labels
    deploy_template_labels = deploy.spec.template.metadata.labels
    svc = core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    svc_labels = svc.metadata.labels
    
    # Check all required labels exist on all resources
    for label in required_labels:
        assert label in deploy_labels, f"Deployment missing label {label}"
        assert label in deploy_template_labels, f"Deployment template missing label {label}"
        assert label in svc_labels, f"Service missing label {label}"
        
    # Check opentelemetry.io/demo-service value is recommendation
    assert deploy_labels["opentelemetry.io/demo-service"] == "recommendation"
    assert deploy_template_labels["opentelemetry.io/demo-service"] == "recommendation"
    assert svc_labels["opentelemetry.io/demo-service"] == "recommendation"


def test_ac7_grpc_health_check_returns_serving():
    """AC-7: gRPC health check returns SERVING status when pod is healthy
    """
    # Get service cluster IP
    svc = core_v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    cluster_ip = svc.spec.cluster_ip
    assert cluster_ip is not None, "Service has no cluster IP"
    
    # Connect to gRPC health endpoint
    channel = grpc.insecure_channel(f"{cluster_ip}:{GRPC_PORT}")
    stub = health_pb2_grpc.HealthStub(channel)
    request = health_pb2.HealthCheckRequest(service="recommendation")
    
    # Try for up to 30s
    end_time = time.time() + 30
    while time.time() < end_time:
        try:
            response = stub.Check(request)
            if response.status == health_pb2.HealthCheckResponse.SERVING:
                return True
        except grpc.RpcError:
            pass
        time.sleep(1)
    assert False, "gRPC health check did not return SERVING status"
