import pytest
import yaml
import requests
from time import sleep
from kubernetes.client import V1Deployment, V1Container

# Helper function to get frontend-proxy container from deployment
def get_frontend_proxy_container(deployment: V1Deployment) -> V1Container:
    for container in deployment.spec.template.spec.containers:
        if container.name == "frontend-proxy":
            return container
    pytest.fail("frontend-proxy container not found in deployment")

# Helper function to fetch Envoy config dump from admin port
def get_envoy_config_dump(pod_ip: str, admin_port: int = 9901):
    try:
        resp = requests.get(f"http://{pod_ip}:{admin_port}/config_dump", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        pytest.fail(f"Failed to fetch Envoy config dump: {str(e)}")

# Helper function to check if outlier detection is configured for a cluster
def cluster_has_outlier_detection(config_dump, cluster_name: str) -> bool:
    for config in config_dump.get("configs", []):
        if "bootstrap" in config:
            clusters = config["bootstrap"].get("static_resources", {}).get("clusters", [])
            for cluster in clusters:
                if cluster.get("name") == cluster_name:
                    return "outlier_detection" in cluster
    return False

def test_ac1_outlier_detection_enabled_default(frontend_proxy_pod, frontend_proxy_deployment):
    """AC-1: When ENVOY_OUTLIER_DETECTION_ENABLED is set to true (default), Envoy config includes valid outlier detection blocks for all HTTP and gRPC upstream clusters"""
    # Verify environment variable is set to true by default
    container = get_frontend_proxy_container(frontend_proxy_deployment)
    env_vars = {e.name: e.value for e in container.env}
    assert env_vars.get("ENVOY_OUTLIER_DETECTION_ENABLED", "true") == "true", "Outlier detection should be enabled by default"
    
    # Fetch config dump
    config_dump = get_envoy_config_dump(frontend_proxy_pod.status.pod_ip)
    
    # Check HTTP clusters (example names: frontend, productcatalogservice, etc.)
    http_clusters = ["frontend", "cartservice", "checkoutservice", "productcatalogservice", "recommendationservice", "cartservice"]
    for cluster in http_clusters:
        assert cluster_has_outlier_detection(config_dump, cluster), f"HTTP cluster {cluster} missing outlier detection config"
    
    # Check gRPC clusters (example names: shippingservice, paymentservice, etc.)
    grpc_clusters = ["shippingservice", "paymentservice", "currencyservice"]
    for cluster in grpc_clusters:
        assert cluster_has_outlier_detection(config_dump, cluster), f"gRPC cluster {cluster} missing outlier detection config"

def test_ac2_consecutive_5xx_ejection_default(frontend_proxy_upstream_fail_scenario):
    """AC-2: When an upstream instance returns 5 consecutive (default value) 5xx error responses, it is ejected from the load balancing pool for the base ejection time (default 30s)"""
    proxy_ip, healthy_backend, unhealthy_backend = frontend_proxy_upstream_fail_scenario
    
    # Send 5 consecutive 5xx producing requests to unhealthy backend
    for _ in range(5):
        try:
            requests.get(f"http://{proxy_ip}/api/faulty", timeout=2)
        except requests.exceptions.HTTPError:
            pass
    
    # Verify unhealthy backend is ejected (requests go to healthy backend only)
    healthy_count = 0
    for _ in range(10):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        if resp.text == healthy_backend.id:
            healthy_count += 1
    assert healthy_count == 10, "Unhealthy backend was not ejected after 5 consecutive 5xx errors"
    
    # Wait ejection time and verify it's added back
    sleep(35)
    backend_count = {}
    for _ in range(20):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        backend_id = resp.text
        backend_count[backend_id] = backend_count.get(backend_id, 0) + 1
    assert unhealthy_backend.id in backend_count, "Unhealthy backend was not added back after ejection time"

def test_ac3_consecutive_5xx_override(frontend_proxy_deployment_custom_consecutive, frontend_proxy_upstream_fail_scenario):
    """AC-3: When ENVOY_OUTLIER_DETECTION_CONSECUTIVE_5XX is overridden to value N, an upstream instance is ejected after N consecutive 5xx errors"""
    custom_threshold = 10
    proxy_ip, healthy_backend, unhealthy_backend = frontend_proxy_upstream_fail_scenario
    
    # Send 9 requests (should NOT eject)
    for _ in range(9):
        try:
            requests.get(f"http://{proxy_ip}/api/faulty", timeout=2)
        except requests.exceptions.HTTPError:
            pass
    
    # Verify both backends are still receiving traffic
    backend_count = {}
    for _ in range(20):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        backend_id = resp.text
        backend_count[backend_id] = backend_count.get(backend_id, 0) + 1
    assert len(backend_count) == 2, "Backend ejected before threshold hit"
    
    # Send 1 more request (total 10)
    try:
        requests.get(f"http://{proxy_ip}/api/faulty", timeout=2)
    except requests.exceptions.HTTPError:
        pass
    
    # Verify unhealthy backend is now ejected
    healthy_count = 0
    for _ in range(10):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        if resp.text == healthy_backend.id:
            healthy_count += 1
    assert healthy_count == 10, "Unhealthy backend was not ejected after custom threshold of 10 errors"

def test_ac4_ejection_time_override(frontend_proxy_deployment_custom_ejection_time, frontend_proxy_upstream_fail_scenario):
    """AC-4: When ENVOY_OUTLIER_DETECTION_BASE_EJECTION_TIME is overridden to "60s", ejected instances remain out of the pool for 60 seconds"""
    proxy_ip, healthy_backend, unhealthy_backend = frontend_proxy_upstream_fail_scenario
    
    # Eject backend
    for _ in range(5):
        try:
            requests.get(f"http://{proxy_ip}/api/faulty", timeout=2)
        except requests.exceptions.HTTPError:
            pass
    
    # Check still ejected after 35s (default would have been added back)
    sleep(35)
    healthy_count = 0
    for _ in range(10):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        if resp.text == healthy_backend.id:
            healthy_count += 1
    assert healthy_count == 10, "Unhealthy backend added back before custom ejection time of 60s"
    
    # Check added back after 65s
    sleep(30)
    backend_count = {}
    for _ in range(20):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        backend_id = resp.text
        backend_count[backend_id] = backend_count.get(backend_id, 0) + 1
    assert unhealthy_backend.id in backend_count, "Unhealthy backend was not added back after custom ejection time"

def test_ac5_max_ejection_percent_override(frontend_proxy_deployment_max_ejection_20, multi_backend_scenario):
    """AC-5: When ENVOY_OUTLIER_DETECTION_MAX_EJECTION_PERCENT is set to 20, no more than 20% of upstream instances for any cluster are ejected at any time"""
    proxy_ip, backends = multi_backend_scenario
    total_backends = len(backends)
    max_ejected = int(total_backends * 0.2)
    
    # Make 40% of backends return 5xx
    faulty_count = int(total_backends * 0.4)
    for i in range(faulty_count):
        backends[i].set_faulty(True)
    
    # Trigger 5 errors per faulty backend
    for backend in backends[:faulty_count]:
        for _ in range(5):
            try:
                requests.get(f"http://{proxy_ip}/api/backend/{backend.id}/faulty", timeout=2)
            except requests.exceptions.HTTPError:
                pass
    
    # Count how many backends are still receiving traffic
    active_backends = set()
    for _ in range(100):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        active_backends.add(resp.text)
    
    ejected_count = total_backends - len(active_backends)
    assert ejected_count <= max_ejected, f"Ejected {ejected_count} backends, expected max {max_ejected} (20% of {total_backends})"

def test_ac6_outlier_detection_disabled(frontend_proxy_deployment_od_disabled, frontend_proxy_upstream_fail_scenario):
    """AC-6: When ENVOY_OUTLIER_DETECTION_ENABLED is set to false, no outlier detection blocks are present in the Envoy config, and all traffic is routed as before without ejection logic"""
    pod, deployment = frontend_proxy_deployment_od_disabled
    proxy_ip, healthy_backend, unhealthy_backend = frontend_proxy_upstream_fail_scenario
    
    # Verify environment variable is false
    container = get_frontend_proxy_container(deployment)
    env_vars = {e.name: e.value for e in container.env}
    assert env_vars.get("ENVOY_OUTLIER_DETECTION_ENABLED") == "false", "Outlier detection should be disabled"
    
    # Verify no outlier detection blocks in config
    config_dump = get_envoy_config_dump(pod.status.pod_ip)
    for cluster in ["frontend", "shippingservice", "productcatalogservice"]:
        assert not cluster_has_outlier_detection(config_dump, cluster), f"Outlier detection present in disabled mode for cluster {cluster}"
    
    # Verify even after 10 5xx errors, traffic still goes to both backends
    for _ in range(10):
        try:
            requests.get(f"http://{proxy_ip}/api/faulty", timeout=2)
        except requests.exceptions.HTTPError:
            pass
    
    backend_count = {}
    for _ in range(20):
        resp = requests.get(f"http://{proxy_ip}/api/health", timeout=2)
        backend_id = resp.text
        backend_count[backend_id] = backend_count.get(backend_id, 0) + 1
    assert len(backend_count) == 2, "Unhealthy backend was ejected even with outlier detection disabled"

def test_ac7_circuit_breaker_unchanged(frontend_proxy_deployment, frontend_proxy_pod):
    """AC-7: Existing circuit breaker configuration remains unchanged and functional when outlier detection is enabled"""
    config_dump = get_envoy_config_dump(frontend_proxy_pod.status.pod_ip)
    
    # Verify circuit breakers still exist for clusters (check sample values from existing config)
    for config in config_dump.get("configs", []):
        if "bootstrap" in config:
            clusters = config["bootstrap"].get("static_resources", {}).get("clusters", [])
            for cluster in clusters:
                assert "circuit_breakers" in cluster, f"Circuit breakers missing from cluster {cluster['name']}"
                # Verify default circuit breaker values are still present
                thresholds = cluster["circuit_breakers"].get("thresholds", [])
                assert len(thresholds) > 0, f"No circuit breaker thresholds for cluster {cluster['name']}"
                assert thresholds[0].get("max_connections") >= 1024, f"Circuit breaker max connections modified"
                assert thresholds[0].get("max_pending_requests") >= 1024, f"Circuit breaker max pending requests modified"
                assert thresholds[0].get("max_requests") >= 1024, f"Circuit breaker max requests modified"

def test_ac8_grpc_cluster_outlier_detection(grpc_upstream_fail_scenario):
    """AC-8: gRPC upstream clusters enforce identical outlier detection rules as HTTP clusters based on gRPC error codes mapped to 5xx equivalents"""
    proxy_ip, healthy_grpc_backend, unhealthy_grpc_backend = grpc_upstream_fail_scenario
    
    # Send 5 consecutive gRPC INTERNAL (code 13, mapped to 500) errors
    for _ in range(5):
        try:
            # Call gRPC method that returns error
            requests.post(f"http://{proxy_ip}/grpc/faulty", timeout=2)
        except Exception:
            pass
    
    # Verify unhealthy gRPC backend is ejected
    healthy_count = 0
    for _ in range(10):
        resp = requests.post(f"http://{proxy_ip}/grpc/health", timeout=2)
        if resp.text == healthy_grpc_backend.id:
            healthy_count += 1
    assert healthy_count == 10, "Unhealthy gRPC backend was not ejected after 5 consecutive error codes"

def test_ac9_env_vars_parsing_correct(frontend_proxy_deployment_all_overrides, frontend_proxy_pod):
    """AC-9: All environment variable overrides are correctly parsed and applied to the Envoy configuration at startup time"""
    container = get_frontend_proxy_container(frontend_proxy_deployment_all_overrides)
    env_vars = {e.name: e.value for e in container.env}
    
    # Verify all env vars are set
    assert env_vars["ENVOY_OUTLIER_DETECTION_CONSECUTIVE_5XX"] == "7"
    assert env_vars["ENVOY_OUTLIER_DETECTION_BASE_EJECTION_TIME"] == "45s"
    assert env_vars["ENVOY_OUTLIER_DETECTION_MAX_EJECTION_PERCENT"] == "15"
    assert env_vars["ENVOY_OUTLIER_DETECTION_ENFORCING_CONSECUTIVE_5XX"] == "80"
    assert env_vars["ENVOY_OUTLIER_DETECTION_EJECTION_TIME_MS"] == "45000"
    
    # Verify values are applied in config
    config_dump = get_envoy_config_dump(frontend_proxy_pod.status.pod_ip)
    for config in config_dump.get("configs", []):
        if "bootstrap" in config:
            clusters = config["bootstrap"].get("static_resources", {}).get("clusters", [])
            for cluster in clusters:
                if "outlier_detection" in cluster:
                    od_config = cluster["outlier_detection"]
                    assert od_config["consecutive_5xx"] == 7, f"Consecutive 5xx not applied correctly"
                    assert od_config["base_ejection_time"] == "45s", f"Base ejection time not applied correctly"
                    assert od_config["max_ejection_percent"] == 15, f"Max ejection percent not applied correctly"
                    assert od_config["enforcing_consecutive_5xx"] == 80, f"Enforcing percent not applied correctly"
