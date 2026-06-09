import os
import time
import pytest
import grpc
from opentelemetry.proto.demo.recommendation.v1 import recommendation_pb2
from opentelemetry.proto.demo.recommendation.v1 import recommendation_pb2_grpc
import requests

# Test constants from spec
LIST_RECOMMENDATIONS_ENDPOINT = "/recommendation.v1.RecommendationService/ListRecommendations"
PER_ENDPOINT_RATE_LIMIT_ENV_VAR = "RECOMMENDATION_SERVICE_RATE_LIMIT_LIST_RECOMMENDATIONS_RPS"
DEFAULT_RATE_LIMIT_ENV_VAR = "RECOMMENDATION_SERVICE_RATE_LIMIT_DEFAULT_RPS"
LEGACY_RATE_LIMIT_ENV_VAR = "RECOMMENDATION_SERVICE_RATE_LIMIT_RPS"
RESOURCE_EXHAUSTED_STATUS_CODE = grpc.StatusCode.RESOURCE_EXHAUSTED
RATE_LIMIT_METRIC_NAME = "recommendation_service_rate_limited_requests_total"
PROMETHEUS_ENDPOINT = "http://localhost:8080/metrics"
DEFAULT_PER_ENDPOINT_RPS = 100

@pytest.fixture(autouse=True)
def setup_teardown():
    # Save original env vars
    original_env = {
        PER_ENDPOINT_RATE_LIMIT_ENV_VAR: os.environ.get(PER_ENDPOINT_RATE_LIMIT_ENV_VAR),
        DEFAULT_RATE_LIMIT_ENV_VAR: os.environ.get(DEFAULT_RATE_LIMIT_ENV_VAR),
        LEGACY_RATE_LIMIT_ENV_VAR: os.environ.get(LEGACY_RATE_LIMIT_ENV_VAR)
    }
    yield
    # Restore original env vars
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

def get_rate_limit_metric_value(endpoint: str) -> int:
    """Helper to get current rate limit counter value for a specific endpoint from Prometheus endpoint"""
    try:
        response = requests.get(PROMETHEUS_ENDPOINT, timeout=5)
        response.raise_for_status()
        for line in response.text.splitlines():
            if line.startswith(f"{RATE_LIMIT_METRIC_NAME}{{endpoint=\"{endpoint}\"}}"):
                return int(line.split()[-1])
        return 0
    except Exception:
        return 0

def test_ac1_per_endpoint_limit_enforced():
    """AC-1: When per-endpoint limit is set to 50, 51 requests/sec result in at least one RESOURCE_EXHAUSTED"""
    # Arrange
    rate_limit = 50
    os.environ[PER_ENDPOINT_RATE_LIMIT_ENV_VAR] = str(rate_limit)
    # Restart service to apply config (handled by test environment)
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])

    # Act: send 51 requests in <1 second to exceed 50 RPS limit
    responses = []
    start_time = time.time()
    for _ in range(51):
        try:
            resp = stub.ListRecommendations(request)
            responses.append((resp, None))
        except grpc.RpcError as e:
            responses.append((None, e))
    duration = time.time() - start_time
    assert duration < 1, f"Test took too long: {duration}s, expected <1s to exceed rate limit"

    # Assert
    rate_limited_count = sum(1 for _, err in responses if err and err.code() == RESOURCE_EXHAUSTED_STATUS_CODE)
    assert rate_limited_count >= 1, f"Expected at least 1 RESOURCE_EXHAUSTED response when exceeding 50 RPS limit, got {rate_limited_count}"

def test_ac2_fallback_to_default_limit_when_no_per_endpoint_config():
    """AC-2: No per-endpoint env var set, requests use DEFAULT_RPS value"""
    # Arrange
    default_limit = 10
    os.environ.pop(PER_ENDPOINT_RATE_LIMIT_ENV_VAR, None)
    os.environ[DEFAULT_RATE_LIMIT_ENV_VAR] = str(default_limit)
    # Restart service to apply config
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])

    # Act: send 11 requests in <1 second to exceed 10 RPS default limit
    responses = []
    start_time = time.time()
    for _ in range(11):
        try:
            resp = stub.ListRecommendations(request)
            responses.append((resp, None))
        except grpc.RpcError as e:
            responses.append((None, e))
    duration = time.time() - start_time
    assert duration < 1, f"Test took too long: {duration}s, expected <1s to exceed rate limit"

    # Assert
    success_count = sum(1 for resp, err in responses if resp is not None)
    rate_limited_count = sum(1 for _, err in responses if err and err.code() == RESOURCE_EXHAUSTED_STATUS_CODE)
    assert success_count <= default_limit, f"Expected at most {default_limit} successful requests with default limit, got {success_count}"
    assert rate_limited_count >= 1, f"Expected at least 1 RESOURCE_EXHAUSTED response when exceeding default limit, got {rate_limited_count}"

def test_ac3_backward_compatibility_no_new_env_vars():
    """AC-3: No new rate limit env vars set, behavior matches original implementation"""
    # Arrange: remove all new env vars, use legacy rate limit value
    os.environ.pop(PER_ENDPOINT_RATE_LIMIT_ENV_VAR, None)
    os.environ.pop(DEFAULT_RATE_LIMIT_ENV_VAR, None)
    legacy_limit = 10
    os.environ[LEGACY_RATE_LIMIT_ENV_VAR] = str(legacy_limit)
    # Restart service to apply config
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])

    # Act: send 11 requests in <1 second to exceed legacy limit
    responses = []
    start_time = time.time()
    for _ in range(11):
        try:
            resp = stub.ListRecommendations(request)
            responses.append((resp, None))
        except grpc.RpcError as e:
            responses.append((None, e))
    duration = time.time() - start_time
    assert duration < 1, f"Test took too long: {duration}s, expected <1s to exceed rate limit"

    # Assert
    success_count = sum(1 for resp, err in responses if resp is not None)
    rate_limited_count = sum(1 for _, err in responses if err and err.code() == RESOURCE_EXHAUSTED_STATUS_CODE)
    assert success_count <= legacy_limit, f"Expected at most {legacy_limit} successful requests with legacy limit, got {success_count}"
    assert rate_limited_count >= 1, f"Expected at least 1 RESOURCE_EXHAUSTED response when exceeding legacy limit, got {rate_limited_count}"

def test_ac4_rate_limit_error_message_includes_endpoint_and_limit():
    """AC-4: Rate limited request error message includes full endpoint name and allowed RPS limit"""
    # Arrange
    rate_limit = 1
    os.environ[PER_ENDPOINT_RATE_LIMIT_ENV_VAR] = str(rate_limit)
    expected_message_prefix = f"Rate limit exceeded for endpoint {LIST_RECOMMENDATIONS_ENDPOINT}: allowed {rate_limit} requests per second"
    # Restart service to apply config
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])

    # Act: send 2 requests, at least one should be rate limited
    errors = []
    for _ in range(2):
        try:
            stub.ListRecommendations(request)
        except grpc.RpcError as e:
            errors.append(e)

    # Assert
    rate_limited_errors = [e for e in errors if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE]
    assert len(rate_limited_errors) >= 1, "Expected at least one RESOURCE_EXHAUSTED error"
    for err in rate_limited_errors:
        assert expected_message_prefix in err.details(), f"Error message missing expected content: expected prefix '{expected_message_prefix}', got '{err.details()}'"

def test_ac5_rate_limited_metric_increments_correctly():
    """AC-5: Counter increments by exactly 1 for each rate limited request, endpoint label matches full method name"""
    # Arrange
    rate_limit = 2
    os.environ[PER_ENDPOINT_RATE_LIMIT_ENV_VAR] = str(rate_limit)
    # Restart service to apply config
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])
    initial_metric_value = get_rate_limit_metric_value(LIST_RECOMMENDATIONS_ENDPOINT)

    # Act: send 10 requests, count rate limited responses
    rate_limited_count = 0
    start_time = time.time()
    for _ in range(10):
        try:
            stub.ListRecommendations(request)
        except grpc.RpcError as e:
            if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE:
                rate_limited_count += 1
    duration = time.time() - start_time
    assert duration < 1, f"Test took too long: {duration}s, expected <1s to exceed rate limit"

    # Assert
    final_metric_value = get_rate_limit_metric_value(LIST_RECOMMENDATIONS_ENDPOINT)
    metric_increment = final_metric_value - initial_metric_value
    assert metric_increment == rate_limited_count, f"Expected metric to increment by {rate_limited_count}, got {metric_increment}"

def test_ac6_env_var_updates_apply_after_restart():
    """AC-6: Updating rate limit env var and restarting service applies new limit without code changes"""
    # Step 1: Initial limit of 1
    initial_limit = 1
    os.environ[PER_ENDPOINT_RATE_LIMIT_ENV_VAR] = str(initial_limit)
    # Restart service
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])

    # Test initial limit
    initial_rate_limited = 0
    for _ in range(3):
        try:
            stub.ListRecommendations(request)
        except grpc.RpcError as e:
            if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE:
                initial_rate_limited += 1
    assert initial_rate_limited >= 2, f"Expected at least 2 rate limited requests with limit 1, got {initial_rate_limited}"

    # Step 2: Update limit to 5
    new_limit = 5
    os.environ[PER_ENDPOINT_RATE_LIMIT_ENV_VAR] = str(new_limit)
    # Restart service to apply new limit
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)

    # Test new limit
    new_rate_limited = 0
    for _ in range(3):
        try:
            stub.ListRecommendations(request)
        except grpc.RpcError as e:
            if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE:
                new_rate_limited += 1

    # Assert no rate limited requests with higher limit
    assert new_rate_limited == 0, f"Expected 0 rate limited requests with limit 5 for 3 requests, got {new_rate_limited}"
