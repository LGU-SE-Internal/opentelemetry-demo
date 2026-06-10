import time
import requests
import docker
import pytest
from docker.errors import NotFound

client = docker.from_env()
IMAGE_NAME = "image-provider:test"
CONTAINER_NAME = "test-image-provider-shutdown"
HEALTH_ENDPOINT = "http://localhost:8080/health"
ASSET_ENDPOINT = "http://localhost:8080/assets/placeholder.jpg"
GRACE_PERIOD = 30

@pytest.fixture(scope="function")
def image_provider_container():
    # Build test image from source
    client.images.build(path="./src/image-provider", tag=IMAGE_NAME, rm=True)
    
    # Run container
    container = client.containers.run(
        IMAGE_NAME,
        name=CONTAINER_NAME,
        ports={"8080/tcp": 8080},
        detach=True,
        auto_remove=False
    )
    
    # Wait for service to become healthy
    for _ in range(10):
        try:
            resp = requests.get(HEALTH_ENDPOINT, timeout=1)
            if resp.status_code == 200:
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    else:
        container.stop()
        container.remove()
        pytest.fail("Service did not become healthy in time")
    
    yield container
    
    # Cleanup
    try:
        container.stop(timeout=1)
        container.remove()
    except NotFound:
        pass
    client.images.remove(IMAGE_NAME, force=True)

def test_ac1_sigterm_triggers_30s_grace_period(image_provider_container):
    """AC-1: SIGTERM/SIGINT enters 30s grace period instead of immediate termination"""
    # Send SIGTERM
    image_provider_container.kill(signal="SIGTERM")
    
    # Check container is still running after 5s
    time.sleep(5)
    container = client.containers.get(CONTAINER_NAME)
    assert container.status == "running", "Container terminated immediately after SIGTERM"
    
    # Check container stops after ~30s
    start = time.time()
    while time.time() - start < (GRACE_PERIOD + 5):
        container = client.containers.get(CONTAINER_NAME)
        if container.status != "running":
            break
        time.sleep(1)
    else:
        pytest.fail("Container did not terminate after 30s grace period")
    
    elapsed = time.time() - start
    assert elapsed >= GRACE_PERIOD - 2, f"Grace period was only {elapsed}s, expected ~{GRACE_PERIOD}s"

def test_ac2_new_asset_requests_return_503_during_grace_period(image_provider_container):
    """AC-2: New static asset requests get 503 during grace period"""
    # Send SIGTERM
    image_provider_container.kill(signal="SIGTERM")
    
    # Wait 2s to enter grace period
    time.sleep(2)
    
    # Request asset endpoint
    try:
        resp = requests.get(ASSET_ENDPOINT, timeout=5)
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code} for asset request during grace period"
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Request failed unexpectedly: {e}")

def test_ac3_health_endpoint_returns_shutting_down_during_grace_period(image_provider_container):
    """AC-3: Health endpoint returns 503 with shutting_down status during grace period"""
    # Send SIGTERM
    image_provider_container.kill(signal="SIGTERM")
    
    # Wait 2s to enter grace period
    time.sleep(2)
    
    # Request health endpoint
    try:
        resp = requests.get(HEALTH_ENDPOINT, timeout=5)
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code} for health request during grace period"
        resp_json = resp.json()
        assert resp_json.get("status") == "shutting_down", f"Expected status 'shutting_down', got {resp_json.get('status')}"
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Request failed unexpectedly: {e}")

def test_ac4_in_flight_requests_complete_during_grace_period(image_provider_container):
    """AC-4: In-flight requests complete successfully before termination"""
    # Start a long-running asset request (simulate slow connection)
    import threading
    request_result = {"completed": False, "status_code": None, "error": None}
    
    def slow_request():
        try:
            # Add server-side delay if supported, otherwise simulate client side delay
            resp = requests.get(f"{ASSET_ENDPOINT}?download=1", timeout=GRACE_PERIOD + 5)
            request_result["status_code"] = resp.status_code
            request_result["completed"] = True
        except requests.exceptions.RequestException as e:
            request_result["error"] = str(e)
    
    thread = threading.Thread(target=slow_request)
    thread.start()
    
    # Wait 1s for request to be in flight
    time.sleep(1)
    
    # Send SIGTERM
    image_provider_container.kill(signal="SIGTERM")
    
    # Wait for thread to complete
    thread.join(timeout=GRACE_PERIOD + 2)
    
    assert request_result["completed"], "In-flight request was interrupted"
    assert request_result["status_code"] == 200, f"In-flight request returned {request_result['status_code']} instead of 200"
    assert request_result["error"] is None, f"In-flight request failed with error: {request_result['error']}"

def test_ac5_requests_exceeding_grace_period_terminated(image_provider_container):
    """AC-5: Requests exceeding 30s grace period are terminated cleanly"""
    # Start a request that will take longer than 30s
    import threading
    request_result = {"completed": False, "error": None}
    
    def long_running_request():
        try:
            # Request with very long timeout
            resp = requests.get(f"{ASSET_ENDPOINT}?delay=40", timeout=60)
            request_result["completed"] = True
        except requests.exceptions.RequestException as e:
            request_result["error"] = str(e)
    
    thread = threading.Thread(target=long_running_request)
    thread.start()
    
    # Wait 1s for request to be in flight
    time.sleep(1)
    
    # Send SIGTERM
    image_provider_container.kill(signal="SIGTERM")
    
    # Wait for container to terminate
    time.sleep(GRACE_PERIOD + 2)
    
    # Check if request was terminated
    assert not request_result["completed"], "Long running request completed beyond grace period"
    assert "Connection reset" in str(request_result["error"]) or "Connection aborted" in str(request_result["error"]) or "timed out" in str(request_result["error"]), f"Unexpected error type: {request_result['error']}"

def test_ac6_health_endpoint_returns_healthy_normal_state(image_provider_container):
    """AC-6: Health endpoint returns 200 OK with status healthy when running normally"""
    try:
        resp = requests.get(HEALTH_ENDPOINT, timeout=5)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code} for health request in normal state"
        resp_json = resp.json()
        assert resp_json.get("status") == "healthy", f"Expected status 'healthy', got {resp_json.get('status')}"
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Request failed unexpectedly: {e}")

def test_ac7_rolling_restart_preserves_in_flight_requests(image_provider_container):
    """AC-7: Rolling restarts do not interrupt in-flight requests"""
    # This test simulates a rolling restart scenario with one instance
    # In real deployment, this would be multiple instances behind load balancer
    
    # Start two in-flight requests
    import threading
    results = []
    
    def run_request(index):
        try:
            resp = requests.get(f"{ASSET_ENDPOINT}?req={index}", timeout=GRACE_PERIOD + 5)
            results.append((index, resp.status_code, None))
        except requests.exceptions.RequestException as e:
            results.append((index, None, str(e)))
    
    threads = []
    for i in range(2):
        t = threading.Thread(target=run_request, args=(i,))
        threads.append(t)
        t.start()
    
    # Wait 1s for requests to be in flight
    time.sleep(1)
    
    # Stop container (simulate rolling restart of instance)
    image_provider_container.kill(signal="SIGTERM")
    
    # Wait for all threads to complete
    for t in threads:
        t.join(timeout=GRACE_PERIOD + 2)
    
    # Verify all in-flight requests completed successfully
    for idx, status, error in results:
        assert status == 200, f"Request {idx} failed with status {status}, error: {error}"
