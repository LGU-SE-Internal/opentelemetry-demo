import pytest
import grpc
from unittest.mock import Mock, patch
from typing import Any
from recommendationservice.retry import with_exponential_backoff_retry
from recommendationservice.metrics import recommendation_service_product_catalog_retry_attempts


def test_ac1_retry_on_unavailable_status():
    # AC-1: Retry up to 3 times on UNAVAILABLE status
    mock_call = Mock()
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    error.details = lambda: "Service unavailable"
    mock_call.side_effect = error
    
    with pytest.raises(grpc.RpcError) as exc_info:
        with_exponential_backoff_retry(mock_call, "test_arg1", key="test_val")
    
    assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
    assert mock_call.call_count == 4  # 1 initial + 3 retries
    with patch.object(recommendation_service_product_catalog_retry_attempts, 'labels') as mock_labels:
        try:
            with_exponential_backoff_retry(mock_call)
        except:
            pass
        mock_labels.assert_any_call(status="failure", grpc_status_code="UNAVAILABLE")
        assert mock_labels.return_value.inc.call_count == 3


def test_ac2_retry_on_deadline_exceeded_status():
    # AC-2: Retry up to 3 times on DEADLINE_EXCEEDED status
    mock_call = Mock()
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.DEADLINE_EXCEEDED
    error.details = lambda: "Deadline exceeded"
    mock_call.side_effect = error
    
    with pytest.raises(grpc.RpcError) as exc_info:
        with_exponential_backoff_retry(mock_call)
    
    assert exc_info.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    assert mock_call.call_count == 4
    with patch.object(recommendation_service_product_catalog_retry_attempts, 'labels') as mock_labels:
        try:
            with_exponential_backoff_retry(mock_call)
        except:
            pass
        mock_labels.assert_any_call(status="failure", grpc_status_code="DEADLINE_EXCEEDED")
        assert mock_labels.return_value.inc.call_count == 3


def test_ac3_retry_on_aborted_status():
    # AC-3: Retry up to 3 times on ABORTED status
    mock_call = Mock()
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.ABORTED
    error.details = lambda: "Request aborted"
    mock_call.side_effect = error
    
    with pytest.raises(grpc.RpcError) as exc_info:
        with_exponential_backoff_retry(mock_call)
    
    assert exc_info.value.code() == grpc.StatusCode.ABORTED
    assert mock_call.call_count == 4
    with patch.object(recommendation_service_product_catalog_retry_attempts, 'labels') as mock_labels:
        try:
            with_exponential_backoff_retry(mock_call)
        except:
            pass
        mock_labels.assert_any_call(status="failure", grpc_status_code="ABORTED")
        assert mock_labels.return_value.inc.call_count == 3


def test_ac4_no_retry_on_non_retryable_status():
    # AC-4: No retries for status codes other than UNAVAILABLE, DEADLINE_EXCEEDED, ABORTED
    non_retryable_statuses = [
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.FAILED_PRECONDITION,
        grpc.StatusCode.OUT_OF_RANGE,
        grpc.StatusCode.UNIMPLEMENTED,
        grpc.StatusCode.INTERNAL,
        grpc.StatusCode.DATA_LOSS
    ]
    
    for status in non_retryable_statuses:
        mock_call = Mock()
        error = grpc.RpcError()
        error.code = lambda: status
        error.details = lambda: f"Non-retryable error: {status.name}"
        mock_call.side_effect = error
        
        with pytest.raises(grpc.RpcError) as exc_info:
            with_exponential_backoff_retry(mock_call)
        
        assert exc_info.value.code() == status
        assert mock_call.call_count == 1  # No retries
        with patch.object(recommendation_service_product_catalog_retry_attempts, 'labels') as mock_labels:
            try:
                with_exponential_backoff_retry(mock_call)
            except:
                pass
            mock_labels.assert_not_called()


def test_ac5_exponential_backoff_with_jitter():
    # AC-5: Exponential backoff with ~100ms, ~200ms, ~400ms intervals ±25% jitter
    from tenacity.wait import wait_exponential_jitter
    
    mock_call = Mock()
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    mock_call.side_effect = error
    
    # Get underlying tenacity retry configuration
    retry_decorator = with_exponential_backoff_retry.retry
    assert hasattr(retry_decorator, 'wait')
    wait_policy = retry_decorator.wait
    assert isinstance(wait_policy, wait_exponential_jitter)
    assert wait_policy.multiplier == 0.1  # 100ms initial wait
    assert wait_policy.exp_base == 2  # Exponential growth factor
    assert wait_policy.max == 0.4  # Max wait 400ms
    assert abs(wait_policy.jitter - 0.25) < 0.01  # ±25% jitter


def test_ac6_retry_counter_increments_correctly():
    # AC-6: Counter increments correctly with right labels for success and failure
    # Test failure case first
    mock_call = Mock()
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    mock_call.side_effect = error
    
    with patch.object(recommendation_service_product_catalog_retry_attempts, 'labels') as mock_labels:
        try:
            with_exponential_backoff_retry(mock_call)
        except:
            pass
        mock_labels.assert_called_with(status="failure", grpc_status_code="UNAVAILABLE")
        assert mock_labels.return_value.inc.call_count == 3
    
    # Test success after retries
    mock_call.reset_mock()
    mock_call.side_effect = [
        error,
        error,
        "success_result"
    ]
    
    with patch.object(recommendation_service_product_catalog_retry_attempts, 'labels') as mock_labels:
        result = with_exponential_backoff_retry(mock_call)
        assert result == "success_result"
        assert mock_call.call_count == 3  # 1 initial + 2 retries
        mock_labels.assert_called_with(status="success", grpc_status_code="UNAVAILABLE")
        assert mock_labels.return_value.inc.call_count == 2
    

def test_ac7_original_error_propagated_after_retries():
    # AC-7: Original gRPC error is preserved after retries are exhausted
    expected_error = grpc.RpcError()
    expected_error.code = lambda: grpc.StatusCode.UNAVAILABLE
    expected_error.details = lambda: "Original error message"
    expected_error.trailing_metadata = lambda: (("test-metadata", "test-value"),)
    mock_call = Mock()
    mock_call.side_effect = expected_error
    
    with pytest.raises(grpc.RpcError) as exc_info:
        with_exponential_backoff_retry(mock_call)
    
    assert exc_info.value is expected_error
    assert exc_info.value.details() == "Original error message"
    assert exc_info.value.trailing_metadata() == (("test-metadata", "test-value"),)


def test_retry_wrapper_validates_grpc_stub():
    # Test that ValueError is raised if input call is not a gRPC stub method
    non_grpc_call = Mock()
    # Simulate non-gRPC stub (no method attribute expected by validation)
    if hasattr(non_grpc_call, '__grpc_stub_method__'):
        delattr(non_grpc_call, '__grpc_stub_method__')
    with pytest.raises(ValueError):
        with_exponential_backoff_retry(non_grpc_call)
