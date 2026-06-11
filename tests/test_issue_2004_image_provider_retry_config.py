import pytest
import requests
import time
from testcontainers.compose import DockerCompose

@pytest.fixture(scope="module")
def compose():
    with DockerCompose(context="/workspace", services=["image-provider", "mock-upstream"], build_args=["--no-cache"]) as compose:
        yield compose

def test_ac1_retry_on_5xx_and_transient_errors(compose):
    """AC-1: Retry GET/HEAD requests on 5xx, connection timeout, reset up to 3 times"""
    endpoint = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-500.jpg"
    
    # Make GET request to upstream that returns 500
    start_time = time.time()
    response = requests.get(endpoint)
    duration = time.time() - start_time
    
    # Verify retries happened (total time > 700ms = 100+200+400ms backoff)
    assert duration > 0.7, "Retries did not occur, request failed too fast"
    
    # Check that upstream was called 4 times (1 original + 3 retries)
    upstream_calls = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls == 4, f"Expected 4 calls to upstream, got {upstream_calls}"
    
    # Same test for HEAD request
    start_time = time.time()
    response = requests.head(endpoint)
    duration = time.time() - start_time
    assert duration > 0.7, "HEAD retries did not occur"
    upstream_calls_head = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls_head == 8, f"Expected 8 total upstream calls, got {upstream_calls_head}"

def test_ac2_exponential_backoff_intervals(compose):
    """AC-2: Retry intervals use exponential backoff: 100ms, 200ms, 400ms"""
    endpoint = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-timeout.jpg"
    
    # Configure upstream to timeout first 3 requests
    requests.post(f"http://{compose.get_service_host('mock-upstream', 80)}/configure", json={"timeout_calls": 3})
    
    start_time = time.time()
    response = requests.get(endpoint)
    duration = time.time() - start_time
    
    # Total time should be ~ 0ms (first try) + 100ms (wait) + 0ms (second try) + 200ms (wait) + 0ms (third try) + 400ms (wait) + 0ms (fourth try) = 700ms minimum
    assert 0.7 < duration < 1.1, f"Expected total retry time ~700ms, got {duration*1000:.0f}ms"
    
    # Get individual call timestamps
    timestamps = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/timestamps").json()["timestamps"]
    assert len(timestamps) == 4, "Expected 4 request timestamps"
    
    # Calculate intervals between calls
    intervals = []
    for i in range(1, 4):
        intervals.append(timestamps[i] - timestamps[i-1])
    
    # Check intervals are ~100ms, 200ms, 400ms (allow 50ms tolerance)
    assert 0.05 < intervals[0] < 0.15, f"First retry interval expected ~100ms, got {intervals[0]*1000:.0f}ms"
    assert 0.15 < intervals[1] < 0.25, f"Second retry interval expected ~200ms, got {intervals[1]*1000:.0f}ms"
    assert 0.35 < intervals[2] < 0.45, f"Third retry interval expected ~400ms, got {intervals[2]*1000:.0f}ms"

def test_ac3_no_retry_on_non_allowed_4xx(compose):
    """AC-3: No retry for 4xx except 404 and 429"""
    # Test 400 Bad Request - should not retry
    endpoint_400 = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-400.jpg"
    start_time = time.time()
    response = requests.get(endpoint_400)
    duration = time.time() - start_time
    assert duration < 0.1, "400 request was retried incorrectly"
    upstream_calls = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls == 1, f"Expected 1 call for 400, got {upstream_calls}"
    
    # Test 401 Unauthorized - should not retry
    endpoint_401 = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-401.jpg"
    response = requests.get(endpoint_401)
    upstream_calls_401 = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls_401 == 2, f"Expected 2 total calls for 401, got {upstream_calls_401}"
    
    # Test 404 Not Found - should retry
    endpoint_404 = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-404.jpg"
    start_time = time.time()
    response = requests.get(endpoint_404)
    duration = time.time() - start_time
    assert duration > 0.7, "404 request was not retried"
    upstream_calls_404 = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls_404 == 6, f"Expected 6 total calls for 404 (1+3 retries), got {upstream_calls_404}"
    
    # Test 429 Too Many Requests - should retry
    endpoint_429 = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-429.jpg"
    start_time = time.time()
    response = requests.get(endpoint_429)
    duration = time.time() - start_time
    assert duration > 0.7, "429 request was not retried"
    upstream_calls_429 = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls_429 == 10, f"Expected 10 total calls for 429 (1+3 retries), got {upstream_calls_429}"

def test_ac4_no_retry_non_idempotent_methods(compose):
    """AC-4: Non-idempotent request methods (POST, PUT, DELETE, PATCH) are never retried"""
    endpoint = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-500.jpg"
    
    # Test POST
    response = requests.post(endpoint, json={"data": "test"})
    upstream_calls = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls == 1, f"POST request was retried incorrectly, got {upstream_calls} calls"
    
    # Test PUT
    response = requests.put(endpoint, json={"data": "test"})
    upstream_calls_put = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls_put == 2, f"PUT request was retried incorrectly, got {upstream_calls_put} calls"
    
    # Test DELETE
    response = requests.delete(endpoint)
    upstream_calls_delete = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls_delete == 3, f"DELETE request was retried incorrectly, got {upstream_calls_delete} calls"
    
    # Test PATCH
    response = requests.patch(endpoint, json={"data": "test"})
    upstream_calls_patch = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls_patch == 4, f"PATCH request was retried incorrectly, got {upstream_calls_patch} calls"

def test_ac5_max_3_retries(compose):
    """AC-5: Maximum 3 retries per request, no further attempts after"""
    endpoint = f"http://{compose.get_service_host('image-provider', 8080)}/images/test-503.jpg"
    
    # Make request that always returns 503
    response = requests.get(endpoint)
    
    # Check upstream calls: 1 original + 3 retries = 4 total
    upstream_calls = requests.get(f"http://{compose.get_service_host('mock-upstream', 80)}/call-count").json()["count"]
    assert upstream_calls == 4, f"Expected maximum 4 calls (1 + 3 retries), got {upstream_calls}"
    
    # Verify we get 503 response after all retries
    assert response.status_code == 503, f"Expected 503 status after retries, got {response.status_code}"

def test_ac6_retry_metrics_exposed(compose):
    """AC-6: Nginx exposes retry metrics via /nginx_status endpoint"""
    status_endpoint = f"http://{compose.get_service_host('image-provider', 8080)}/nginx_status"
    
    # Generate some retry traffic
    for _ in range(10):
        try:
            requests.get(f"http://{compose.get_service_host('image-provider', 8080)}/images/test-500.jpg", timeout=2)
        except:
            pass
    
    # Get metrics
    metrics = requests.get(status_endpoint).text
    
    # Check that required metrics exist
    assert "nginx_upstream_retry_total" in metrics, "nginx_upstream_retry_total metric missing"
    assert "nginx_upstream_retry_failed_total" in metrics, "nginx_upstream_retry_failed_total metric missing"
    assert "nginx_upstream_retry_success_rate" in metrics, "nginx_upstream_retry_success_rate metric missing"
    
    # Verify retry count is at least 10 (10 requests * 3 retries each = 30 expected, minimum 10)
    for line in metrics.split("\n"):
        if "nginx_upstream_retry_total" in line and not line.startswith("#"):
            retry_count = int(float(line.split()[1]))
            assert retry_count >= 10, f"Expected at least 10 retries, got {retry_count}"

def test_ac7_latency_increase_within_limit(compose):
    """AC-7: No more than 10% increase in average latency for successful requests at 1000 RPS"""
    import locust
    from locust import HttpUser, task, between
    from locust.env import Environment
    from locust.stats import stats_printer, stats_history
    import gevent
    
    class ImageUser(HttpUser):
        wait_time = between(0.001, 0.001)
        host = f"http://{compose.get_service_host('image-provider', 8080)}"
        
        @task
        def get_success_image(self):
            self.client.get("/images/test-success.jpg", timeout=5)
    
    # Setup Locust environment
    env = Environment(user_classes=[ImageUser])
    env.create_local_runner()
    
    # Start a greenlet that periodically outputs the current stats
    gevent.spawn(stats_printer(env.stats))
    gevent.spawn(stats_history, env.runner)
    
    # Start 10 users to simulate ~1000 RPS
    env.runner.start(10, spawn_rate=10)
    
    # Run for 10 seconds
    gevent.spawn_later(10, lambda: env.runner.quit())
    env.runner.greenlet.join()
    
    # Get average latency for successful requests
    avg_latency = env.stats.aggregated_stats("total").avg_response_time
    
    # Baseline latency for successful requests without retries should be < 50ms, 10% increase = <55ms
    assert avg_latency < 55, f"Average latency {avg_latency:.2f}ms exceeds 10% increase limit of 55ms"
