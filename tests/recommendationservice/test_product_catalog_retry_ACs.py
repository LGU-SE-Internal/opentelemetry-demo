import os
import pytest
from unittest.mock import Mock, patch
import grpc
from opentelemetry.metrics import Counter
from recommendation.recommendation_service import retry_product_catalog_call

# Idempotent methods per spec
IDEMPOTENT_METHODS = ["GetProduct", "ListProducts", "SearchProducts"]
# Transient error codes per spec
TRANSIENT_ERROR_CODES = [
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.INTERNAL,
    grpc.StatusCode.RESOURCE_EXHAUSTED
]
# Non-transient error codes per spec
NON_TRANSIENT_ERROR_CODES = [
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.ALREADY_EXISTS,
    grpc.StatusCode.FAILED_PRECONDITION
]

@pytest.fixture(autouse=True)
def reset_env_vars():
    """Reset environment variables before each test"""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def mock_metrics():
    """Mock the metrics counters"""
    with patch("recommendation.recommendation_service.retry_attempts_counter") as mock_attempts:
        with patch("recommendation.recommendation_service.retry_failures_counter") as mock_failures:
            yield mock_attempts, mock_failures

@pytest.fixture
def mock_logger():
    """Mock the structured logger"""
    with patch("recommendation.recommendation_service.logger") as mock_log:
        yield mock_log

def test_ac1_retry_transient_errors_up_to_max_attempts():
    """AC-1: Retry transient errors up to configured max attempts with exponential backoff"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "3"
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_INITIAL_RETRY_BACKOFF_MS"] = "100"
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_BACKOFF_MS"] = "2000"
    
    # Mock gRPC method that always raises UNAVAILABLE
    mock_method = Mock()
    mock_method.__name__ = IDEMPOTENT_METHODS[0]
    mock_method.side_effect = grpc.RpcError("test error")
    mock_method.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)
    
    with pytest.raises(grpc.RpcError):
        retry_product_catalog_call(mock_method, "test_product_id")
    
    # Should have been called 1 + max_attempts times (initial + 3 retries = 4 total calls)
    assert mock_method.call_count == 4

def test_ac2_no_retry_non_transient_errors():
    """AC-2: No retries for non-transient error types"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "3"
    
    for error_code in NON_TRANSIENT_ERROR_CODES:
        mock_method = Mock()
        mock_method.__name__ = IDEMPOTENT_METHODS[0]
        mock_method.side_effect = grpc.RpcError("test error")
        mock_method.code = Mock(return_value=error_code)
        
        with pytest.raises(grpc.RpcError):
            retry_product_catalog_call(mock_method, "test_product_id")
        
        # Should only be called once, no retries
        assert mock_method.call_count == 1

def test_ac3_exponential_backoff_capped_at_max():
    """AC-3: Backoff doubles each attempt, capped at max backoff"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "4"
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_INITIAL_RETRY_BACKOFF_MS"] = "100"
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_BACKOFF_MS"] = "500"
    
    mock_method = Mock()
    mock_method.__name__ = IDEMPOTENT_METHODS[0]
    mock_method.side_effect = grpc.RpcError("test error")
    mock_method.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)
    
    with patch("time.sleep") as mock_sleep:
        with pytest.raises(grpc.RpcError):
            retry_product_catalog_call(mock_method, "test_product_id")
        
        # Expected sleep durations: 100ms, 200ms, 400ms, 500ms (capped)
        expected_sleeps = [0.1, 0.2, 0.4, 0.5]
        actual_sleeps = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_sleeps == expected_sleeps

def test_ac4_zero_max_attempts_no_retries():
    """AC-4: No retries when max retry attempts is set to 0"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "0"
    
    mock_method = Mock()
    mock_method.__name__ = IDEMPOTENT_METHODS[0]
    mock_method.side_effect = grpc.RpcError("test error")
    mock_method.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)
    
    with pytest.raises(grpc.RpcError):
        retry_product_catalog_call(mock_method, "test_product_id")
    
    # Only called once, no retries
    assert mock_method.call_count == 1

def test_ac5_retry_attempts_metric_incremented(mock_metrics):
    """AC-5: Retry attempts metric is incremented on each retry with correct labels"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "2"
    
    mock_attempts, _ = mock_metrics
    test_method_name = IDEMPOTENT_METHODS[1]
    test_error_code = grpc.StatusCode.DEADLINE_EXCEEDED
    
    mock_method = Mock()
    mock_method.__name__ = test_method_name
    mock_method.side_effect = grpc.RpcError("test error")
    mock_method.code = Mock(return_value=test_error_code)
    
    with pytest.raises(grpc.RpcError):
        retry_product_catalog_call(mock_method, "test")
    
    # Should have 2 retry attempts (2 increments)
    assert mock_attempts.add.call_count == 2
    # Check labels for first attempt
    first_call_labels = mock_attempts.add.call_args_list[0].kwargs["labels"]
    assert first_call_labels["method"] == test_method_name
    assert first_call_labels["error_type"] == test_error_code.name

def test_ac6_retry_failures_metric_incremented_on_exhaustion(mock_metrics):
    """AC-6: Retry failures metric incremented when all retries are exhausted"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "3"
    
    _, mock_failures = mock_metrics
    test_method_name = IDEMPOTENT_METHODS[2]
    test_error_code = grpc.StatusCode.INTERNAL
    
    mock_method = Mock()
    mock_method.__name__ = test_method_name
    mock_method.side_effect = grpc.RpcError("test error")
    mock_method.code = Mock(return_value=test_error_code)
    
    with pytest.raises(grpc.RpcError) as exc_info:
        retry_product_catalog_call(mock_method, "test")
    
    # Verify original error is raised
    assert isinstance(exc_info.value, grpc.RpcError)
    # Verify failure counter is incremented once with correct labels
    assert mock_failures.add.call_count == 1
    failure_labels = mock_failures.add.call_args.kwargs["labels"]
    assert failure_labels["method"] == test_method_name
    assert failure_labels["error_type"] == test_error_code.name

def test_ac7_retry_events_produce_structured_logs(mock_logger):
    """AC-7: Retry attempts and exhaustion events produce structured logs with all required fields"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "1"
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_INITIAL_RETRY_BACKOFF_MS"] = "100"
    
    test_method_name = IDEMPOTENT_METHODS[0]
    test_error_code = grpc.StatusCode.RESOURCE_EXHAUSTED
    
    mock_method = Mock()
    mock_method.__name__ = test_method_name
    mock_method.side_effect = grpc.RpcError("test error")
    mock_method.code = Mock(return_value=test_error_code)
    
    with pytest.raises(grpc.RpcError):
        retry_product_catalog_call(mock_method, "test")
    
    # Check for retry attempt log
    retry_attempt_logs = [call for call in mock_logger.info.call_args_list if "product_catalog_retry_attempt" in str(call.args)]
    assert len(retry_attempt_logs) == 1
    attempt_log_fields = retry_attempt_logs[0].kwargs["extra"]
    assert attempt_log_fields["event"] == "product_catalog_retry_attempt"
    assert attempt_log_fields["method"] == test_method_name
    assert attempt_log_fields["error_type"] == test_error_code.name
    assert attempt_log_fields["attempt_number"] == 1
    assert "backoff_duration_ms" in attempt_log_fields
    
    # Check for retry exhausted log
    exhausted_logs = [call for call in mock_logger.error.call_args_list if "product_catalog_retry_exhausted" in str(call.args)]
    assert len(exhausted_logs) == 1
    exhausted_log_fields = exhausted_logs[0].kwargs["extra"]
    assert exhausted_log_fields["event"] == "product_catalog_retry_exhausted"
    assert exhausted_log_fields["method"] == test_method_name
    assert exhausted_log_fields["error_type"] == test_error_code.name
    assert exhausted_log_fields["total_attempts"] == 2  # Initial + 1 retry

def test_ac8_only_retry_idempotent_methods():
    """AC-8: Only retry idempotent methods, no retries for non-idempotent methods"""
    os.environ["RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS"] = "3"
    
    # Test non-idempotent method (CreateProduct is non-idempotent)
    mock_method = Mock()
    mock_method.__name__ = "CreateProduct"
    mock_method.side_effect = grpc.RpcError("test error")
    mock_method.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)  # Even transient error
    
    with pytest.raises(grpc.RpcError):
        retry_product_catalog_call(mock_method, {})
    
    # Only called once, no retries even for transient error
    assert mock_method.call_count == 1
    
    # Verify idempotent method still retries
    mock_method_idem = Mock()
    mock_method_idem.__name__ = IDEMPOTENT_METHODS[0]
    mock_method_idem.side_effect = grpc.RpcError("test error")
    mock_method_idem.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)
    
    with pytest.raises(grpc.RpcError):
        retry_product_catalog_call(mock_method_idem, "test")
    
    assert mock_method_idem.call_count == 4  # 1 initial + 3 retries
