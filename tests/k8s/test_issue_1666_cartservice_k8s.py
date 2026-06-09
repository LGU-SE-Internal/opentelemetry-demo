import os
import time
import subprocess
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from kubernetes import client, config
import pytest

# Config
MANIFEST_DIR = "k8s/cartservice/"
DEPLOYMENT_PATH = os.path.join(MANIFEST_DIR, "cartservice-deployment.yaml")
SERVICE_PATH = os.path.join(MANIFEST_DIR, "cartservice-service.yaml")
NAMESPACE = "default"
SERVICE_FQDN = f"cartservice.{NAMESPACE}.svc.cluster.local:8080"


@pytest.fixture(scope="module")
def k8s_client():
    config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api()


def test_ac1_deployment_creates_running_pods(k8s_client):
    """AC-1: Deployment pods transition to Running within 60s, no restarts in 5 minutes"""
    core_v1, apps_v1 = k8s_client

    # Apply deployment
    subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "-n", NAMESPACE],
        check=True,
        capture_output=True,
    )

    # Wait up to 60s for pods to be running
    start_time = time.time()
    pods_running = False
    while time.time() - start_time < 60:
        pods = core_v1.list_namespaced_pod(
            NAMESPACE, label_selector="app.kubernetes.io/name=cartservice"
        )
        if len(pods.items) > 0 and all(p.status.phase == "Running" for p in pods.items):
            pods_running = True
            break
        time.sleep(5)
    assert pods_running, "Pods did not reach Running state within 60 seconds"

    # Check no restarts for 5 minutes
    time.sleep(300)
    pods = core_v1.list_namespaced_pod(
        NAMESPACE, label_selector="app.kubernetes.io/name=cartservice"
    )
    for pod in pods.items:
        for container in pod.status.container_statuses:
            assert container.restart_count == 0, f"Pod {pod.metadata.name} had unexpected restarts"


def test_ac2_pod_security_context(k8s_client):
    """AC-2: Pods run with least privilege security context"""
    core_v1, apps_v1 = k8s_client
    deployment = apps_v1.read_namespaced_deployment("cartservice", NAMESPACE)
    pod_spec = deployment.spec.template.spec

    # Check security context fields
    assert pod_spec.security_context.run_as_non_root == True
    assert pod_spec.security_context.run_as_user == 1000
    container = pod_spec.containers[0]
    assert container.security_context.allow_privilege_escalation == False
    assert container.security_context.read_only_root_filesystem == True
    assert "ALL" in container.security_context.capabilities.drop


def test_ac3_health_probes_success(k8s_client):
    """AC-3: Liveness and readiness probes return success within 10s of pod start"""
    core_v1, apps_v1 = k8s_client
    deployment = apps_v1.read_namespaced_deployment("cartservice", NAMESPACE)
    container = deployment.spec.template.spec.containers[0]

    # Verify probes are configured correctly
    assert container.liveness_probe is not None
    assert container.liveness_probe.grpc.port == 8080
    assert container.liveness_probe.grpc.service == ""  # default health check service

    assert container.readiness_probe is not None
    assert container.readiness_probe.grpc.port == 8080
    assert container.readiness_probe.grpc.service == ""

    # Wait for probes to report success
    time.sleep(10)
    pods = core_v1.list_namespaced_pod(
        NAMESPACE, label_selector="app.kubernetes.io/name=cartservice"
    )
    for pod in pods.items:
        for condition in pod.status.conditions:
            if condition.type == "Ready":
                assert condition.status == "True", f"Pod {pod.metadata.name} is not ready"
        for container_status in pod.status.container_statuses:
            assert container_status.ready == True, f"Container {container_status.name} is not ready"


def test_ac4_service_grpc_health_check():
    """AC-4: Service exposes working gRPC health check endpoint"""
    # Apply service manifest
    subprocess.run(
        ["kubectl", "apply", "-f", SERVICE_PATH, "-n", NAMESPACE],
        check=True,
        capture_output=True,
    )
    time.sleep(10)

    # Create gRPC channel and call health check
    with grpc.insecure_channel(SERVICE_FQDN) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        response = stub.Check(health_pb2.HealthCheckRequest())
        assert response.status == health_pb2.HealthCheckResponse.ServingStatus.SERVING, \
            "gRPC health check returned non-success status"


def test_ac5_redis_connection_works():
    """AC-5: Cartservice connects to Redis successfully when REDIS_ADDR is set"""
    # Note: This test assumes Redis is already deployed at redis:6379 in the cluster
    subprocess.run(
        ["kubectl", "set", "env", "deployment/cartservice", "REDIS_ADDR=redis:6379", "-n", NAMESPACE],
        check=True,
        capture_output=True,
    )
    # Wait for rollout
    subprocess.run(
        ["kubectl", "rollout", "status", "deployment/cartservice", "-n", NAMESPACE, "--timeout=60s"],
        check=True,
        capture_output=True,
    )
    # Verify no connection errors in logs
    pods = client.CoreV1Api().list_namespaced_pod(
        NAMESPACE, label_selector="app.kubernetes.io/name=cartservice"
    )
    for pod in pods.items:
        logs = client.CoreV1Api().read_namespaced_pod_log(pod.metadata.name, NAMESPACE)
        assert "Redis connection error" not in logs, "Redis connection error found in pod logs"
        assert "Connected to Redis" in logs, "Redis connection success not found in logs"


def test_ac6_tls_connections_work():
    """AC-6: Cartservice accepts TLS connections when cert paths are configured"""
    # Note: Assumes test certs are already mounted to /certs path in the deployment
    subprocess.run(
        ["kubectl", "set", "env", "deployment/cartservice",
         "TLS_CERT_PATH=/certs/tls.crt",
         "TLS_KEY_PATH=/certs/tls.key",
         "-n", NAMESPACE],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["kubectl", "rollout", "status", "deployment/cartservice", "-n", NAMESPACE, "--timeout=60s"],
        check=True,
        capture_output=True,
    )
    # Test TLS connection
    with open("/tmp/tls.crt", "rb") as f:
        creds = grpc.ssl_channel_credentials(f.read())
    with grpc.secure_channel(SERVICE_FQDN, creds) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        response = stub.Check(health_pb2.HealthCheckRequest())
        assert response.status == health_pb2.HealthCheckResponse.ServingStatus.SERVING, \
            "TLS gRPC health check failed"


def test_ac7_mtls_enforces_client_certs():
    """AC-7: Cartservice only accepts mTLS connections with valid client certs when CA is configured"""
    subprocess.run(
        ["kubectl", "set", "env", "deployment/cartservice",
         "MTLS_CA_CERT_PATH=/certs/ca.crt",
         "-n", NAMESPACE],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["kubectl", "rollout", "status", "deployment/cartservice", "-n", NAMESPACE, "--timeout=60s"],
        check=True,
        capture_output=True,
    )

    # Test connection without client cert should fail
    with open("/tmp/ca.crt", "rb") as f:
        creds = grpc.ssl_channel_credentials(f.read())
    with grpc.secure_channel(SERVICE_FQDN, creds) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.Check(health_pb2.HealthCheckRequest())
        assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED, \
            "Connection without client cert should have failed"

    # Test connection with valid client cert should succeed
    with open("/tmp/ca.crt", "rb") as f:
        root_cert = f.read()
    with open("/tmp/client.crt", "rb") as f:
        client_cert = f.read()
    with open("/tmp/client.key", "rb") as f:
        client_key = f.read()
    creds = grpc.ssl_channel_credentials(
        root_certificates=root_cert,
        private_key=client_key,
        certificate_chain=client_cert
    )
    with grpc.secure_channel(SERVICE_FQDN, creds) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        response = stub.Check(health_pb2.HealthCheckRequest())
        assert response.status == health_pb2.HealthCheckResponse.ServingStatus.SERVING, \
            "mTLS connection with valid client cert failed"


def test_ac8_resource_requests_limits(k8s_client):
    """AC-8: Deployment has correct resource requests and limits"""
    _, apps_v1 = k8s_client
    deployment = apps_v1.read_namespaced_deployment("cartservice", NAMESPACE)
    container = deployment.spec.template.spec.containers[0]

    assert container.resources.requests["cpu"] == "100m"
    assert container.resources.requests["memory"] == "128Mi"
    assert container.resources.limits["cpu"] == "200m"
    assert container.resources.limits["memory"] == "256Mi"


def test_ac9_manifests_pass_kube_lint():
    """AC-9: Manifest files pass kube-lint validation with no errors"""
    result = subprocess.run(
        ["kube-lint", DEPLOYMENT_PATH, SERVICE_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"kube-lint found errors: {result.stderr}"
    assert "error" not in result.stderr.lower(), "kube-lint reported error-level findings"
