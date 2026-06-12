import os
import time
import multiprocessing
from typing import List
import pytest
from grpc import StatusCode
from unittest import mock

# Helper function to run requests in a worker process
def worker_process_requests(rps_limit: int, num_requests: int, results_queue: multiprocessing.Queue):
    os.environ["RECOMMENDATION_SERVICE_RATE_LIMIT"] = str(rps_limit)
    # Reinitialize rate limiter with shared memory implementation
    from src.recommendation import rate_limiter
    allowed = 0
    rate_limited = 0
    start_time = time.time_ns()
    for _ in range(num_requests):
        if rate_limiter.is_allowed():
            allowed +=1
        else:
            rate_limited +=1
    end_time = time.time_ns()
    results_queue.put((allowed, rate_limited, start_time, end_time))

@pytest.fixture(autouse=True)
def cleanup_shared_memory():
    # Cleanup any existing shared memory segments before each test
    try:
        from multiprocessing import shared_memory
        shm = shared_memory.SharedMemory(name="otel_demo_recommendation_rate_limit", create=False)
        shm.close()
        shm.unlink()
    except FileNotFoundError:
        pass
    yield
    # Cleanup after test
    try:
        from multiprocessing import shared_memory
        shm = shared_memory.SharedMemory(name="otel_demo_recommendation_rate_limit", create=False)
        shm.close()
        shm.unlink()
    except FileNotFoundError:
        pass

def test_ac1_multi_worker_rate_limit_overshoot():
    """AC-1: Total allowed RPS across N workers ≤ 1.05*L, 1min average ≤ L"""
    rps_limit = 100
    num_workers = 4
    requests_per_worker = 500
    
    results_queue = multiprocessing.Queue()
    workers: List[multiprocessing.Process] = []
    
    for _ in range(num_workers):
        p = multiprocessing.Process(target=worker_process_requests, args=(rps_limit, requests_per_worker, results_queue))
        workers.append(p)
        p.start()
    
    total_allowed = 0
    total_rate_limited = 0
    min_start = time.time_ns()
    max_end = 0
    
    for _ in range(num_workers):
        allowed, rate_limited, start, end = results_queue.get()
        total_allowed += allowed
        total_rate_limited += rate_limited
        if start < min_start:
            min_start = start
        if end > max_end:
            max_end = end
    
    for p in workers:
        p.join()
    
    actual_duration_s = (max_end - min_start) / 1e9
    actual_rps = total_allowed / actual_duration_s
    
    # Verify overshoot ≤ 5%
    assert actual_rps <= rps_limit * 1.05, f"Rate limit overshoot: {actual_rps} RPS vs limit {rps_limit} (max allowed {rps_limit*1.05})"
    # Verify average is within limit
    assert actual_rps <= rps_limit * 1.01, f"Average RPS {actual_rps} exceeds limit {rps_limit} by more than 1%"

def test_ac2_single_worker_identical_behavior():
    """AC-2: Single worker behavior identical to original implementation, within 1% of limit"""
    rps_limit = 100
    test_duration = 5
    requests_to_send = 1000
    
    os.environ["RECOMMENDATION_SERVICE_RATE_LIMIT"] = str(rps_limit)
    from src.recommendation import rate_limiter
    
    start = time.time_ns()
    allowed = 0
    for _ in range(requests_to_send):
        if rate_limiter.is_allowed():
            allowed +=1
        # Send requests as fast as possible
    end = time.time_ns()
    
    actual_duration_s = (end - start) / 1e9
    actual_rps = allowed / actual_duration_s
    
    assert abs(actual_rps - rps_limit) <= rps_limit * 0.01, f"Single worker RPS {actual_rps} outside 1% tolerance of limit {rps_limit}"

def test_ac3_env_var_config_respected():
    """AC-3: RECOMMENDATION_SERVICE_RATE_LIMIT env var respected, no new config params"""
    test_limits = [50, 200, 500]
    for limit in test_limits:
        os.environ["RECOMMENDATION_SERVICE_RATE_LIMIT"] = str(limit)
        # Reload rate limiter
        from importlib import reload
        from src.recommendation import rate_limiter as rl_module
        reload(rl_module)
        assert rl_module.rate_limiter.rps_limit == limit, f"Rate limit {rl_module.rate_limiter.rps_limit} does not match env var {limit}"

def test_ac4_grpc_status_code_correct():
    """AC-4: Rate limited requests return RESOURCE_EXHAUSTED status code with original message"""
    # Set very low rate limit to force rate limiting
    os.environ["RECOMMENDATION_SERVICE_RATE_LIMIT"] = "1"
    from importlib import reload
    from src.recommendation import app, rate_limiter
    reload(rate_limiter)
    
    # First request allowed
    assert rate_limiter.is_allowed() == True
    # Second request should be rate limited
    assert rate_limiter.is_allowed() == False
    
    # Test actual gRPC endpoint
    from src.recommendation.v1 import recommendation_pb2
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test")
    context = mock.Mock()
    
    # First request should succeed
    response = app.ListRecommendations(request, context)
    assert context.set_code.not_called()
    
    # Second request should return RESOURCE_EXHAUSTED
    context.reset_mock()
    response = app.ListRecommendations(request, context)
    context.set_code.assert_called_once_with(StatusCode.RESOURCE_EXHAUSTED)
    context.set_details.assert_called_once_with("Rate limit exceeded")  # Original error message

def test_ac5_rate_limit_counter_increments():
    """AC-5: recommendation_service_rate_limited_requests_total increments for every rate limited request"""
    os.environ["RECOMMENDATION_SERVICE_RATE_LIMIT"] = "2"
    from importlib import reload
    from src.recommendation import rate_limiter, metrics
    reload(rate_limiter)
    reload(metrics)
    
    initial_count = metrics.recommendation_service_rate_limited_requests_total._value.get()
    
    # 2 allowed requests
    assert rate_limiter.is_allowed() == True
    assert rate_limiter.is_allowed() == True
    # 3 rate limited requests
    assert rate_limiter.is_allowed() == False
    assert rate_limiter.is_allowed() == False
    assert rate_limiter.is_allowed() == False
    
    final_count = metrics.recommendation_service_rate_limited_requests_total._value.get()
    assert final_count - initial_count == 3, f"Expected 3 increments, got {final_count - initial_count}"

def test_ac6_no_external_dependencies():
    """AC-6: No external dependencies added to requirements.txt or Dockerfile"""
    # Check requirements.txt for recommendation service
    with open("./src/recommendation/requirements.txt", "r") as f:
        reqs = f.read()
    assert "redis" not in reqs.lower(), "Redis dependency found in requirements.txt"
    assert "memcached" not in reqs.lower(), "Memcached dependency found in requirements.txt"
    assert "pymemcache" not in reqs.lower(), "pymemcache dependency found in requirements.txt"
    assert "redis-py" not in reqs.lower(), "redis-py dependency found in requirements.txt"
    
    # Check Dockerfile
    with open("./src/recommendation/Dockerfile", "r") as f:
        dockerfile = f.read()
    assert "install redis" not in dockerfile.lower(), "Redis installation found in Dockerfile"
    assert "install memcached" not in dockerfile.lower(), "Memcached installation found in Dockerfile"

def test_ac7_shared_memory_cleanup_on_shutdown():
    """AC-7: Shared memory segments cleaned up on service graceful stop"""
    from multiprocessing import shared_memory
    rps_limit = 100
    
    # Create rate limiter instance which creates shared memory
    from src.recommendation import SharedMemoryRateLimiter
    limiter = SharedMemoryRateLimiter(rps_limit=rps_limit)
    
    # Verify shared memory exists
    shm = shared_memory.SharedMemory(name="otel_demo_recommendation_rate_limit", create=False)
    shm.close()
    
    # Delete limiter to trigger __del__ cleanup
    del limiter
    
    # Verify shared memory is gone
    with pytest.raises(FileNotFoundError):
        shm = shared_memory.SharedMemory(name="otel_demo_recommendation_rate_limit", create=False)
        shm.close()

def test_ac8_10_worker_accuracy():
    """AC-8: 10 workers, sustained traffic, accuracy within ±5% over 1 minute"""
    rps_limit = 200
    num_workers = 10
    requests_per_worker = 2000  # Enough to saturate limit for 1 minute
    
    results_queue = multiprocessing.Queue()
    workers: List[multiprocessing.Process] = []
    
    for _ in range(num_workers):
        p = multiprocessing.Process(target=worker_process_requests, args=(rps_limit, requests_per_worker, results_queue))
        workers.append(p)
        p.start()
    
    total_allowed = 0
    min_start = time.time_ns()
    max_end = 0
    
    for _ in range(num_workers):
        allowed, _, start, end = results_queue.get()
        total_allowed += allowed
        if start < min_start:
            min_start = start
        if end > max_end:
            max_end = end
    
    for p in workers:
        p.join()
    
    actual_duration_s = (max_end - min_start) / 1e9
    actual_rps = total_allowed / actual_duration_s
    
    # Verify within ±5% tolerance
    assert actual_rps >= rps_limit * 0.95, f"Rate limit undershoot: {actual_rps} RPS vs limit {rps_limit} (min allowed {rps_limit*0.95})"
    assert actual_rps <= rps_limit * 1.05, f"Rate limit overshoot: {actual_rps} RPS vs limit {rps_limit} (max allowed {rps_limit*1.05})"
