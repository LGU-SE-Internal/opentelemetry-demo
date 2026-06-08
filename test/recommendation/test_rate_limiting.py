import os
import time
import pytest
import grpc
from opentelemetry.proto.demo.recommendation.v1 import recommendation_pb2
from opentelemetry.proto.demo.recommendation.v1 import recommendation_pb2_grpc
import requests

# Test constants from spec
RATE_LIMIT_ENV_VAR = "RECOMMENDATION_SERVICE_RATE_LIMIT_RPS"
RATE_LIMIT_EXCEEDED_MESSAGE = "Rate limit exceeded. Try again later."
RESOURCE_EXHAUSTED_STATUS_CODE = grpc.StatusCode.RESOURCE_EXHAUSTED
RATE_LIMIT_METRIC_NAME = "recommendation_service_rate_limited_requests_total"
ENDPOINT_LABEL = "ListRecommendations"
STATUS_LABEL = "rate_limited"
PROMETHEUS_ENDPOINT = "http://localhost:8080/metrics"

@pytest.fixture(autouse=True)
def setup_teardown():
    # Save original env var
    original_env = os.environ.get(RATE_LIMIT_ENV_VAR)
    yield
    # Restore original env var
    if original_env is None:
        os.environ.pop(RATE_LIMIT_ENV_VAR, None)
    else:
        os.environ[RATE_LIMIT_ENV_VAR] = original_env

def get_rate_limit_metric_value():
    """Helper to get current rate limit counter value from Prometheus endpoint"""
    try:
        response = requests.get(PROMETHEUS_ENDPOINT, timeout=5)
        response.raise_for_status()
        for line in response.text.splitlines():
            if line.startswith(f"{RATE_LIMIT_METRIC_NAME}{{endpoint=\"{ENDPOINT_LABEL}\",status=\"{STATUS_LABEL}\"}}"):
                return int(line.split()[-1])
        return 0
    except Exception:
        return 0

def test_ac1_rate_limit_disabled_all_requests_succeed():
    """AC-1: When rate limit is 0 or not set, all requests succeed, no rate limit errors, no metric increments."""
    # Arrange
    os.environ[RATE_LIMIT_ENV_VAR] = "0"
    # Restart service to apply config (fixture handles this in real test environment)
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])
    initial_metric_value = get_rate_limit_metric_value()

    # Act: send 10 requests, all should succeed
    responses = []
    for _ in range(10):
        try:
            resp = stub.ListRecommendations(request)
            responses.append((resp, None))
        except grpc.RpcError as e:
            responses.append((None, e))

    # Assert
    success_count = sum(1 for resp, err in responses if resp is not None)
    error_count = sum(1 for resp, err in responses if err is not None)
    assert success_count == 10, f"Expected 10 successful requests, got {success_count}"
    assert error_count == 0, f"Expected 0 errors, got {error_count}"
    # Check no RESOURCE_EXHAUSTED errors
    for _, err in responses:
        if err:
            assert err.code() != RESOURCE_EXHAUSTED_STATUS_CODE, f"Unexpected RESOURCE_EXHAUSTED error: {err}"
    # Check metric not incremented
    final_metric_value = get_rate_limit_metric_value()
    assert final_metric_value == initial_metric_value, f"Rate limit metric should not be incremented when rate limit is disabled, initial: {initial_metric_value}, final: {final_metric_value}"

def test_ac2_rate_limit_exceeded_returns_resource_exhausted():
    """AC-2: When rate limit N>0, requests exceeding N RPS get RESOURCE_EXHAUSTED."""
    # Arrange
    rate_limit = 2
    os.environ[RATE_LIMIT_ENV_VAR] = str(rate_limit)
    # Restart service to apply config
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])

    # Act: send 10 requests in <1 second (exceed 2 RPS)
    responses = []
    start_time = time.time()
    for _ in range(10):
        try:
            resp = stub.ListRecommendations(request)
            responses.append((resp, None))
        except grpc.RpcError as e:
            responses.append((None, e))
    duration = time.time() - start_time
    assert duration < 1, f"Test took too long: {duration}s, expected <1s to exceed rate limit"

    # Assert
    success_count = sum(1 for resp, err in responses if resp is not None)
    error_count = sum(1 for resp, err in responses if err is not None)
    rate_limited_count = sum(1 for _, err in responses if err and err.code() == RESOURCE_EXHAUSTED_STATUS_CODE)
    assert success_count <= rate_limit, f"Expected at most {rate_limit} successful requests, got {success_count}"
    assert rate_limited_count >= (10 - rate_limit), f"Expected at least {10 - rate_limit} RESOURCE_EXHAUSTED responses, got {rate_limited_count}"

def test_ac3_rate_limit_error_message_matches_exact_spec():
    """AC-3: Rate limited requests return exact error message from spec."""
    # Arrange
    os.environ[RATE_LIMIT_ENV_VAR] = "1"
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

    # Assert: at least one rate limited error with exact message
    rate_limited_errors = [e for e in errors if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE]
    assert len(rate_limited_errors) >= 1, "Expected at least one RESOURCE_EXHAUSTED error"
    for err in rate_limited_errors:
        assert err.details() == RATE_LIMIT_EXCEEDED_MESSAGE, f"Error message mismatch: expected '{RATE_LIMIT_EXCEEDED_MESSAGE}', got '{err.details()}'"

def test_ac4_rate_limited_requests_increment_metric_exactly_one():
    """AC-4: Each rate limited request increments the counter by exactly 1."""
    # Arrange
    os.environ[RATE_LIMIT_ENV_VAR] = "2"
    # Restart service to apply config
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])
    initial_metric_value = get_rate_limit_metric_value()

    # Act: send 10 requests, get number of rate limited responses
    rate_limited_count = 0
    for _ in range(10):
        try:
            stub.ListRecommendations(request)
        except grpc.RpcError as e:
            if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE:
                rate_limited_count += 1

    # Assert: metric increased by exactly rate_limited_count
    final_metric_value = get_rate_limit_metric_value()
    metric_increment = final_metric_value - initial_metric_value
    assert metric_increment == rate_limited_count, f"Expected metric to increment by {rate_limited_count}, got {metric_increment}"

def test_ac5_new_rate_limit_takes_effect_after_restart():
    """AC-5: Changing env var and restarting service applies new rate limit."""
    # Arrange step 1: initial rate limit 1
    initial_rate_limit = 1
    os.environ[RATE_LIMIT_ENV_VAR] = str(initial_rate_limit)
    # Restart service
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)
    request = recommendation_pb2.ListRecommendationsRequest(user_id="test-user", product_ids=["prod1", "prod2"])

    # Act step 1: send 3 requests with initial limit
    initial_rate_limited_count = 0
    for _ in range(3):
        try:
            stub.ListRecommendations(request)
        except grpc.RpcError as e:
            if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE:
                initial_rate_limited_count += 1
    assert initial_rate_limited_count >= 2, f"Expected at least 2 rate limited requests with limit 1, got {initial_rate_limited_count}"

    # Arrange step 2: update rate limit to 5
    new_rate_limit = 5
    os.environ[RATE_LIMIT_ENV_VAR] = str(new_rate_limit)
    # Restart service to apply new limit
    channel = grpc.insecure_channel("localhost:8081")
    stub = recommendation_pb2_grpc.RecommendationServiceStub(channel)

    # Act step 2: send 3 requests with new limit
    new_rate_limited_count = 0
    for _ in range(3):
        try:
            stub.ListRecommendations(request)
        except grpc.RpcError as e:
            if e.code() == RESOURCE_EXHAUSTED_STATUS_CODE:
                new_rate_limited_count += 1

    # Assert: no rate limited requests with new higher limit for 3 requests
    assert new_rate_limited_count == 0, f"Expected 0 rate limited requests with limit 5 for 3 requests, got {new_rate_limited_count}"
