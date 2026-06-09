"""
Integration tests for Kubernetes deployment guardrails for recommendation service
AC-1 to AC-7 as per spec
"""
import os
import subprocess
import yaml
import time
import grpc
from google.protobuf import json_format
from pb import demo_pb2, demo_pb2_grpc

DEPLOYMENT_PATH = "k8s/recommendation-service/deployment.yaml"
SERVICE_NAME = "recommendation"
GRPC_PORT = 8080
TEST_NAMESPACE = os.getenv("TEST_NAMESPACE", "default")

def run_cmd(cmd, shell=True, check=True):
    """Run shell command and return output"""
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {cmd}\nStderr: {result.stderr}\nStdout: {result.stdout}")
    return result

def get_deployment_spec():
    """Load and return deployment spec from yaml"""
    with open(DEPLOYMENT_PATH, 'r') as f:
        docs = list(yaml.safe_load_all(f))
        for doc in docs:
            if doc and doc.get("kind") == "Deployment":
                return doc
    raise Exception(f"No Deployment found in {DEPLOYMENT_PATH}")

def get_running_pod_name():
    """Get name of running recommendation service pod"""
    cmd = f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].metadata.name}}'"
    result = run_cmd(cmd)
    return result.stdout.strip()

def test_ac1_probe_configuration_present():
    """AC-1: Liveness and readiness gRPC probes present in deployment spec"""
    deploy = get_deployment_spec()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    # Check liveness probe
    assert "livenessProbe" in container
    assert "grpc" in container["livenessProbe"]
    assert container["livenessProbe"]["grpc"]["port"] == GRPC_PORT
    assert container["livenessProbe"]["initialDelaySeconds"] == 10
    assert container["livenessProbe"]["periodSeconds"] == 30
    
    # Check readiness probe
    assert "readinessProbe" in container
    assert "grpc" in container["readinessProbe"]
    assert container["readinessProbe"]["grpc"]["port"] == GRPC_PORT
    assert container["readinessProbe"]["initialDelaySeconds"] == 5
    assert container["readinessProbe"]["periodSeconds"] == 10

def test_ac2_probes_passing_in_running_pod():
    """AC-2: Liveness and readiness probes are passing in running pod"""
    pod_name = get_running_pod_name()
    # Wait for initial delay before checking
    time.sleep(15)
    
    # Get pod events to check probe status
    cmd = f"kubectl describe pod -n {TEST_NAMESPACE} {pod_name}"
    result = run_cmd(cmd)
    
    # Verify no probe failures
    assert "Liveness probe failed" not in result.stdout
    assert "Readiness probe failed" not in result.stdout
    # Verify readiness status is ready
    cmd = f"kubectl get pod -n {TEST_NAMESPACE} {pod_name} -o jsonpath='{{.status.containerStatuses[0].ready}}'"
    result = run_cmd(cmd)
    assert result.stdout.strip() == "true"

def test_ac3_resource_limits_configured():
    """AC-3: CPU and memory requests/limits set correctly in deployment spec"""
    deploy = get_deployment_spec()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "resources" in container
    assert "requests" in container["resources"]
    assert "limits" in container["resources"]
    
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert container["resources"]["requests"]["memory"] == "128Mi"
    assert container["resources"]["limits"]["cpu"] == "500m"
    assert container["resources"]["limits"]["memory"] == "256Mi"

def test_ac4_non_root_user_running():
    """AC-4: Container runs as non-root user (uid 1000)"""
    pod_name = get_running_pod_name()
    cmd = f"kubectl exec -n {TEST_NAMESPACE} {pod_name} -- whoami"
    result = run_cmd(cmd)
    assert result.stdout.strip() != "root"
    
    # Check UID
    cmd = f"kubectl exec -n {TEST_NAMESPACE} {pod_name} -- id -u"
    result = run_cmd(cmd)
    assert result.stdout.strip() == "1000"

def test_ac5_readonly_root_filesystem():
    """AC-5: Root filesystem is read-only"""
    pod_name = get_running_pod_name()
    # Try to create file on root filesystem, expect failure
    cmd = f"kubectl exec -n {TEST_NAMESPACE} {pod_name} -- touch /test-file 2>&1"
    result = run_cmd(cmd, check=False)
    assert result.returncode != 0
    assert "Read-only file system" in result.stderr

def test_ac6_no_privileged_access():
    """AC-6: Container has no privileged access or elevated capabilities"""
    deploy = get_deployment_spec()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    # Check deployment spec flags
    assert "securityContext" in container
    assert container["securityContext"]["runAsNonRoot"] == True
    assert container["securityContext"]["readOnlyRootFilesystem"] == True
    assert container["securityContext"]["allowPrivilegeEscalation"] == False
    assert container["securityContext"]["privileged"] == False
    
    # Check running container capabilities
    pod_name = get_running_pod_name()
    cmd = f"kubectl exec -n {TEST_NAMESPACE} {pod_name} -- capsh --print 2>&1"
    result = run_cmd(cmd, check=False)
    if result.returncode == 0:
        assert "CAP_SYS_ADMIN" not in result.stdout
        assert "cap_sys_admin" not in result.stdout.lower()

def test_ac7_service_grpc_operations_work():
    """AC-7: Service serves gRPC requests correctly: health checks and recommendations work"""
    # First check health endpoint
    pod_name = get_running_pod_name()
    # Port forward to access gRPC endpoint
    port_forward = subprocess.Popen(
        f"kubectl port-forward -n {TEST_NAMESPACE} {pod_name} {GRPC_PORT}:{GRPC_PORT}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    
    try:
        # Check health status
        channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
        health_stub = demo_pb2_grpc.HealthStub(channel)
        health_response = health_stub.Check(demo_pb2.HealthCheckRequest(service=""))
        assert health_response.status == demo_pb2.HealthCheckResponse.SERVING
        
        # Check recommendation endpoint
        rec_stub = demo_pb2_grpc.RecommendationServiceStub(channel)
        rec_response = rec_stub.ListRecommendations(demo_pb2.ListRecommendationsRequest(user_id="test_user", product_ids=["1", "2", "3"]))
        assert len(rec_response.product_ids) > 0
        for pid in rec_response.product_ids:
            assert isinstance(pid, str)
            assert len(pid) > 0
    finally:
        port_forward.terminate()
        port_forward.wait()
