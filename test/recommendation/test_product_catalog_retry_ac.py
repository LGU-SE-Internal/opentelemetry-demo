import grpc
import os
import time
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.recommendation.recommendation_server import list_products_with_retry
from src.recommendation import demo_pb2, demo_pb2_grpc

# Eligible retry status codes from spec
ELIGIBLE_RETRY_CODES = [
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.INTERNAL,
]

# Non-eligible status codes from spec
NON_ELIGIBLE_CODES = [
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.ALREADY_EXISTS,
]

@pytest.fixture(autouse=True)
def reset_env_vars():
    """Reset environment variables before each test"""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def mock_stub():
    return Mock(spec=demo_pb2_grpc.ProductCatalogServiceStub)

@pytest.fixture
def mock_request():
    return demo_pb2.ListProductsRequest()

@pytest.fixture
def mock_metadata():
    return [("x-request-id", "test-123")]

@pytest.fixture
def mock_logger():
    with patch("src.recommendation.recommendation_server.logger") as mock_log:
        yield mock_log

@pytest.fixture
def mock_metrics_counter():
    with patch("src.recommendation.metrics.recommendation_service_product_catalog_retry_attempts") as mock_counter:
        yield mock_counter

def test_ac1_retry_on_eligible_status_codes_up_to_max_attempts(mock_stub, mock_request, mock_metadata):
    """AC-1: Retry eligible status codes up to max attempts before failing"""
    # Setup mock to return UNAVAILABLE every time
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    mock_stub.ListProducts.side_effect = error

    # Default max attempts is 3, so total calls = 3
    with pytest.raises(grpc.RpcError) as exc_info:
        list_products_with_retry(mock_stub, mock_request, mock_metadata)

    assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
    assert mock_stub.ListProducts.call_count == 3

def test_ac2_retry_max_attempts_configurable_via_env_var(mock_stub, mock_request):
    """AC-2: Max retry attempts configurable via environment variable"""
    # Set custom max attempts
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_MAX_ATTEMPTS"] = "5"
    
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.RESOURCE_EXHAUSTED
    mock_stub.ListProducts.side_effect = error

    with pytest.raises(grpc.RpcError):
        list_products_with_retry(mock_stub, mock_request)

    assert mock_stub.ListProducts.call_count == 5

def test_ac3_exponential_backoff_follows_configurable_parameters(mock_stub, mock_request):
    """AC-3: Exponential backoff follows configured parameters"""
    # Set custom backoff parameters
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_INITIAL_BACKOFF_MS"] = "200"
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_BACKOFF_MULTIPLIER"] = "3"
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_MAX_BACKOFF_MS"] = "1500"
    
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.INTERNAL
    mock_stub.ListProducts.side_effect = error

    start_time = time.time()
    with pytest.raises(grpc.RpcError):
        list_products_with_retry(mock_stub, mock_request)
    total_elapsed = time.time() - start_time

    # Expected backoff: 200ms + 600ms + 1500ms (capped) = 2300ms, allow 20% tolerance
    assert total_elapsed >= 1.84  # 2.3 * 0.8
    assert total_elapsed <= 2.8  # 2.3 * 1.2 + small overhead

def test_ac4_no_retry_on_non_eligible_status_codes(mock_stub, mock_request, mock_logger):
    """AC-4: No retries for non-eligible status codes"""
    for code in NON_ELIGIBLE_CODES:
        error = grpc.RpcError()
        error.code = lambda: code
        mock_stub.ListProducts.side_effect = error
        mock_stub.ListProducts.reset_mock()
        mock_logger.reset_mock()

        with pytest.raises(grpc.RpcError) as exc_info:
            list_products_with_retry(mock_stub, mock_request)

        assert exc_info.value.code() == code
        assert mock_stub.ListProducts.call_count == 1
        assert not any("retry attempt" in str(call) for call in mock_logger.info.call_args_list)

def test_ac5_retry_attempts_logged_with_correct_fields(mock_stub, mock_request, mock_logger):
    """AC-5: Each retry attempt logged at INFO level with required fields"""
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    mock_stub.ListProducts.side_effect = error

    with pytest.raises(grpc.RpcError):
        list_products_with_retry(mock_stub, mock_request)

    # 3 attempts = 2 retries, so 2 info logs
    assert mock_logger.info.call_count == 2
    for attempt_num, call in enumerate(mock_logger.info.call_args_list, start=1):
        log_msg = str(call.args[0])
        assert f"attempt {attempt_num}" in log_msg
        assert "UNAVAILABLE" in log_msg
        assert "backoff" in log_msg.lower()
        # Verify backoff is present and in expected range
        if attempt_num == 1:
            assert "100ms" in log_msg or "0.1s" in log_msg
        elif attempt_num == 2:
            assert "200ms" in log_msg or "0.2s" in log_msg

def test_ac6_retry_metric_incremented_with_correct_labels(mock_stub, mock_request, mock_metrics_counter):
    """AC-6: Retry counter metric incremented with correct labels"""
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.RESOURCE_EXHAUSTED
    mock_stub.ListProducts.side_effect = error

    with pytest.raises(grpc.RpcError):
        list_products_with_retry(mock_stub, mock_request)

    # 2 retries, so 2 increments
    assert mock_metrics_counter.add.call_count == 2
    # First retry: attempt_number=1, status_code=RESOURCE_EXHAUSTED
    mock_metrics_counter.add.assert_any_call(1, {"status_code": "RESOURCE_EXHAUSTED", "attempt_number": 1})
    # Second retry: attempt_number=2, status_code=RESOURCE_EXHAUSTED
    mock_metrics_counter.add.assert_any_call(1, {"status_code": "RESOURCE_EXHAUSTED", "attempt_number": 2})

def test_ac7_original_error_propagated_with_max_retries_context(mock_stub, mock_request):
    """AC-7: Original error propagated when max retries exhausted, with additional context"""
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.INTERNAL
    error.details = lambda: "Test internal error"
    mock_stub.ListProducts.side_effect = error

    with pytest.raises(grpc.RpcError) as exc_info:
        list_products_with_retry(mock_stub, mock_request)

    assert exc_info.value.code() == grpc.StatusCode.INTERNAL
    assert exc_info.value.details() == "Test internal error"
    assert "max retries exhausted" in str(exc_info.value).lower() or "max retries exceeded" in str(exc_info.value).lower()

def test_ac8_list_products_idempotent_no_side_effects(mock_stub, mock_request):
    """AC-8: ListProducts is idempotent, retries do not cause side effects"""
    # First call fails, second succeeds
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    success_response = demo_pb2.ListProductsResponse(products=[demo_pb2.Product(id="1", name="Test Product")])
    mock_stub.ListProducts.side_effect = [error, success_response]

    result = list_products_with_retry(mock_stub, mock_request)

    # Verify we got the correct response after retry
    assert mock_stub.ListProducts.call_count == 2
    assert len(result.products) == 1
    assert result.products[0].id == "1"
    # No side effects observed: only ListProducts calls made, no other service calls
    assert mock_stub.method_calls == [("ListProducts", (mock_request,), {}), ("ListProducts", (mock_request,), {})]
