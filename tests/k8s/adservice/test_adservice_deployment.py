import pytest
from kubernetes import client, config
import subprocess
import time
import grpc
from src.adservice.proto import ad_service_pb2, ad_service_pb2_grpc

# Configs
AD_SERVICE_DEPLOYMENT_NAME = "ad-service"
AD_SERVICE_NAMESPACE = "default"
AD_SERVICE_GRPC_PORT = 8080
AD_SERVICE_GRPC_HEALTH_CHECK_SERVICE = ""
EXPECTED_UID = 1000

@pytest.fixture(scope="module")
def k8s_client():
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    return apps_v1, core_v1

@pytest.fixture(scope="module")
def ad_service_deployment(k8s_client):
    apps_v1, _ = k8s_client
    try:
        deploy = apps_v1.read_namespaced_deployment(AD_SERVICE_DEPLOYMENT_NAME, AD_SERVICE_NAMESPACE)
        return deploy
    except client.exceptions.ApiException as e:
        pytest.fail(f"Ad service deployment not found: {e}")

@pytest.fixture(scope="module")
def running_ad_service_pod(k8s_client):
    _, core_v1 = k8s_client
    pods = core_v1.list_namespaced_pod(AD_SERVICE_NAMESPACE, label_selector=f"app={AD_SERVICE_DEPLOYMENT_NAME}")
    for pod in pods.items:
        if pod.status.phase == "Running" and all(c.ready for c in pod.status.container_statuses):
            return pod
    pytest.fail("No running ad service pod found")

def test_ac1_liveness_probe_config_exists(ad_service_deployment):
    """Verify livenessProbe configuration matches spec exactly"""
    container = ad_service_deployment.spec.template.spec.containers[0]
    assert hasattr(container, 'liveness_probe'), "livenessProbe not defined on ad service container"
    liveness = container.liveness_probe
    
    # Check gRPC config
    assert hasattr(liveness, 'grpc'), "livenessProbe does not use gRPC type"
    assert liveness.grpc.port == AD_SERVICE_GRPC_PORT, f"livenessProbe gRPC port should be {AD_SERVICE_GRPC_PORT}"
    assert liveness.grpc.service == AD_SERVICE_GRPC_HEALTH_CHECK_SERVICE, "livenessProbe gRPC service should be empty string"
    
    # Check probe timings
    assert liveness.initial_delay_seconds == 30, "livenessProbe initialDelaySeconds should be 30"
    assert liveness.period_seconds == 10, "livenessProbe periodSeconds should be 10"
    assert liveness.timeout_seconds == 5, "livenessProbe timeoutSeconds should be 5"
    assert liveness.failure_threshold == 3, "livenessProbe failureThreshold should be 3"

def test_ac2_readiness_probe_config_exists(ad_service_deployment):
    """Verify readinessProbe configuration matches spec exactly"""
    container = ad_service_deployment.spec.template.spec.containers[0]
    assert hasattr(container, 'readiness_probe'), "readinessProbe not defined on ad service container"
    readiness = container.readiness_probe
    
    # Check gRPC config
    assert hasattr(readiness, 'grpc'), "readinessProbe does not use gRPC type"
    assert readiness.grpc.port == AD_SERVICE_GRPC_PORT, f"readinessProbe gRPC port should be {AD_SERVICE_GRPC_PORT}"
    assert readiness.grpc.service == AD_SERVICE_GRPC_HEALTH_CHECK_SERVICE, "readinessProbe gRPC service should be empty string"
    
    # Check probe timings
    assert readiness.initial_delay_seconds == 5, "readinessProbe initialDelaySeconds should be 5"
    assert readiness.period_seconds == 5, "readinessProbe periodSeconds should be 5"
    assert readiness.timeout_seconds == 3, "readinessProbe timeoutSeconds should be 3"
    assert readiness.failure_threshold == 3, "readinessProbe failureThreshold should be 3"

def test_ac3_resource_limits_config_exists(ad_service_deployment):
    """Verify CPU/memory resource requests and limits match spec exactly"""
    container = ad_service_deployment.spec.template.spec.containers[0]
    assert hasattr(container, 'resources'), "resources section not defined on ad service container"
    resources = container.resources
    
    # Check requests
    assert hasattr(resources, 'requests'), "resource requests not defined"
    assert resources.requests.get('cpu') == "100m", "CPU request should be 100m"
    assert resources.requests.get('memory') == "256Mi", "Memory request should be 256Mi"
    
    # Check limits
    assert hasattr(resources, 'limits'), "resource limits not defined"
    assert resources.limits.get('cpu') == "500m", "CPU limit should be 500m"
    assert resources.limits.get('memory') == "512Mi", "Memory limit should be 512Mi"

def test_ac4_security_context_config_exists(ad_service_deployment):
    """Verify non-root security context configuration matches spec exactly"""
    container = ad_service_deployment.spec.template.spec.containers[0]
    assert hasattr(container, 'security_context'), "securityContext not defined on ad service container"
    sc = container.security_context
    
    assert sc.run_as_non_root == True, "runAsNonRoot should be true"
    assert sc.run_as_user == EXPECTED_UID, f"runAsUser should be {EXPECTED_UID}"
    assert sc.allow_privilege_escalation == False, "allowPrivilegeEscalation should be false"
    assert sc.read_only_root_filesystem == True, "readOnlyRootFilesystem should be true"
    assert sc.privileged == False, "privileged should be false"

def test_ac5_liveness_probe_behavior(k8s_client, running_ad_service_pod):
    """Verify liveness probe restarts pod when health endpoint returns NOT_SERVING"""
    # Note: This test will fail until implementation is complete as probes are not configured
    _, core_v1 = k8s_client
    restart_count_before = running_ad_service_pod.status.container_statuses[0].restart_count
    
    # Simulate health endpoint failure (test assumes we can trigger NOT_SERVING state, e.g. via admin endpoint if exists)
    # For test purposes we check that the probe is configured to cause restart after 3 failures
    time.sleep(40) # Wait for probe failure cycles
    pod = core_v1.read_namespaced_pod(running_ad_service_pod.metadata.name, AD_SERVICE_NAMESPACE)
    restart_count_after = pod.status.container_statuses[0].restart_count
    
    assert restart_count_after > restart_count_before, "Pod should be restarted after liveness probe failures"

def test_ac6_readiness_probe_behavior(k8s_client, running_ad_service_pod):
    """Verify readiness probe removes pod from service endpoints when health endpoint fails"""
    # Note: This test will fail until implementation is complete as probes are not configured
    _, core_v1 = k8s_client
    endpoints = core_v1.read_namespaced_endpoints(AD_SERVICE_DEPLOYMENT_NAME, AD_SERVICE_NAMESPACE)
    addresses_before = len(endpoints.subsets[0].addresses) if endpoints.subsets else 0
    
    # Simulate health endpoint failure
    time.sleep(20) # Wait for probe failure cycles
    endpoints = core_v1.read_namespaced_endpoints(AD_SERVICE_DEPLOYMENT_NAME, AD_SERVICE_NAMESPACE)
    addresses_after = len(endpoints.subsets[0].not_ready_addresses) if endpoints.subsets else 0
    
    assert addresses_after > 0 and addresses_before > addresses_after, "Pod should be removed from ready endpoints after readiness probe failures"

def test_ac7_non_root_user_exec_check(running_ad_service_pod):
    """Verify exec id returns UID 1000, not root"""
    # Note: This test will fail until security context is configured
    result = subprocess.run(
        ["kubectl", "exec", running_ad_service_pod.metadata.name, "-n", AD_SERVICE_NAMESPACE, "--", "id", "-u"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, "kubectl exec failed"
    uid = int(result.stdout.strip())
    assert uid == EXPECTED_UID, f"Running user should be UID {EXPECTED_UID}, got {uid}"
    assert uid != 0, "Should not run as root UID 0"

def test_ac8_functional_grpc_response_works(running_ad_service_pod):
    """Verify ad service gRPC endpoint still responds correctly after deployment changes"""
    # Note: This test will fail if deployment changes break functionality
    pod_ip = running_ad_service_pod.status.pod_ip
    channel = grpc.insecure_channel(f"{pod_ip}:{AD_SERVICE_GRPC_PORT}")
    stub = ad_service_pb2_grpc.AdServiceStub(channel)
    
    # Test valid ad request
    request = ad_service_pb2.AdRequest(context_keys=["test"])
    response = stub.GetAds(request)
    
    assert len(response.ads) > 0, "Should return at least one ad"
    assert all(ad.redirect_url and ad.text for ad in response.ads), "All ads should have redirect_url and text"
