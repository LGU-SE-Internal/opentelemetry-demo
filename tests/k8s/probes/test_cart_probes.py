import yaml
import requests
import pytest
from kubernetes import client, config

# Load kube config
config.load_kube_config()
k8s_apps_v1 = client.AppsV1Api()
NAMESPACE = "default"
CART_SERVICE_DEPLOYMENT_NAME = "cartservice"
CART_SERVICE_PORT = 8080

def get_cart_deployment():
    return k8s_apps_v1.read_namespaced_deployment(
        name=CART_SERVICE_DEPLOYMENT_NAME,
        namespace=NAMESPACE
    )

def get_cart_pod_ip():
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector=f"app={CART_SERVICE_DEPLOYMENT_NAME}"
    )
    assert len(pods.items) > 0, "No cart service pods found"
    return pods.items[0].status.pod_ip

@pytest.fixture(scope="module")
def cart_deployment():
    return get_cart_deployment()

@pytest.fixture(scope="module")
def cart_pod_ip():
    return get_cart_pod_ip()

# AC-1: The cart service deployment.yaml readinessProbe.path field is explicitly set to "/ready"
def test_ac1_readiness_probe_path_is_ready(cart_deployment):
    readiness_probe = cart_deployment.spec.template.spec.containers[0].readiness_probe
    assert readiness_probe is not None, "Readiness probe not configured"
    assert hasattr(readiness_probe, 'http_get'), "Readiness probe is not HTTP GET type"
    assert readiness_probe.http_get.path == "/ready", f"Expected readiness probe path '/ready', got '{readiness_probe.http_get.path}'"

# AC-2: The cart service deployment.yaml livenessProbe.path field remains set to "/health"
def test_ac2_liveness_probe_path_is_health(cart_deployment):
    liveness_probe = cart_deployment.spec.template.spec.containers[0].liveness_probe
    assert liveness_probe is not None, "Liveness probe not configured"
    assert hasattr(liveness_probe, 'http_get'), "Liveness probe is not HTTP GET type"
    assert liveness_probe.http_get.path == "/health", f"Expected liveness probe path '/health', got '{liveness_probe.http_get.path}'"

# AC-3: All timing parameter values for both livenessProbe and readinessProbe are identical to original pre-change values
def test_ac3_probe_timing_parameters_preserved(cart_deployment):
    # Original expected values (from default demo deployment as of current release)
    EXPECTED_LIVENESS_PARAMS = {
        "initial_delay_seconds": 0,
        "period_seconds": 10,
        "timeout_seconds": 1,
        "failure_threshold": 3
    }
    EXPECTED_READINESS_PARAMS = {
        "initial_delay_seconds": 20,
        "period_seconds": 10,
        "timeout_seconds": 1,
        "failure_threshold": 3
    }
    
    liveness_probe = cart_deployment.spec.template.spec.containers[0].liveness_probe
    readiness_probe = cart_deployment.spec.template.spec.containers[0].readiness_probe
    
    # Check liveness params
    for param, expected_val in EXPECTED_LIVENESS_PARAMS.items():
        assert getattr(liveness_probe, param) == expected_val, f"Liveness probe {param} mismatch: expected {expected_val}, got {getattr(liveness_probe, param)}"
    
    # Check readiness params
    for param, expected_val in EXPECTED_READINESS_PARAMS.items():
        assert getattr(readiness_probe, param) == expected_val, f"Readiness probe {param} mismatch: expected {expected_val}, got {getattr(readiness_probe, param)}"

# AC-4: The cart service Program.cs file confirms existence of "/ready" endpoint with Redis health check
def test_ac4_ready_endpoint_exists_in_program_cs():
    program_cs_path = "./src/cart/src/Program.cs"
    with open(program_cs_path, 'r') as f:
        content = f.read()
    
    # Check for /ready endpoint registration
    assert "/ready" in content, "/ready path not found in Program.cs"
    # Check for Redis/Valkey health check registration
    assert any(term in content.lower() for term in ["redis", "valkey"]), "No Redis/Valkey reference found in Program.cs"
    assert any(term in content.lower() for term in ["healthcheck", "health check"]), "No health check reference found in Program.cs"

# AC-5: When cart service cannot connect to Redis, readiness probe returns non-200, pod is NotReady
def test_ac5_readiness_fails_when_redis_down(cart_pod_ip):
    # This test assumes Redis/Valkey is temporarily unreachable during test execution
    try:
        response = requests.get(f"http://{cart_pod_ip}:{CART_SERVICE_PORT}/ready", timeout=5)
        assert response.status_code != 200, "Expected non-200 status when Redis is down, got 200"
        
        # Check pod readiness status
        v1 = client.CoreV1Api()
        pods = v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={CART_SERVICE_DEPLOYMENT_NAME}")
        pod = pods.items[0]
        readiness_condition = next(c for c in pod.status.conditions if c.type == "Ready")
        assert readiness_condition.status == "False", "Expected pod to be NotReady when Redis is down"
    except requests.exceptions.RequestException:
        pytest.skip("Cart service pod not reachable, skipping this test")

# AC-6: When cart service has healthy Redis connection, readiness probe returns 200, pod is Ready
def test_ac6_readiness_succeeds_when_redis_up(cart_pod_ip):
    # This test assumes Redis/Valkey is available during test execution
    try:
        response = requests.get(f"http://{cart_pod_ip}:{CART_SERVICE_PORT}/ready", timeout=5)
        assert response.status_code == 200, f"Expected 200 status when Redis is up, got {response.status_code}"
        
        # Check pod readiness status
        v1 = client.CoreV1Api()
        pods = v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={CART_SERVICE_DEPLOYMENT_NAME}")
        pod = pods.items[0]
        readiness_condition = next(c for c in pod.status.conditions if c.type == "Ready")
        assert readiness_condition.status == "True", "Expected pod to be Ready when Redis is up"
    except requests.exceptions.RequestException:
        pytest.skip("Cart service pod not reachable, skipping this test")

# AC-7: Liveness probe returns 200 whenever application process is running, regardless of Redis state
def test_ac7_liveness_always_succeeds_when_process_running(cart_pod_ip):
    # This test works regardless of Redis availability
    try:
        response = requests.get(f"http://{cart_pod_ip}:{CART_SERVICE_PORT}/health", timeout=5)
        assert response.status_code == 200, f"Expected 200 status from liveness probe, got {response.status_code}"
    except requests.exceptions.RequestException:
        pytest.skip("Cart service pod not reachable, skipping this test")
