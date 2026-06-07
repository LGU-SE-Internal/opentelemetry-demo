import os
import time
from unittest.mock import Mock, patch
import grpc
import pytest
from tenacity import RetryError

# Import the actual method to test
from src.product_reviews.product_reviews_server import fetch_product_details

# Environment variable names from spec
RETRY_MAX_ATTEMPTS_ENV = "PRODUCT_CATALOG_RETRY_MAX_ATTEMPTS"
RETRY_INITIAL_BACKOFF_ENV = "PRODUCT_CATALOG_RETRY_INITIAL_BACKOFF_MS"
RETRY_MAX_BACKOFF_ENV = "PRODUCT_CATALOG_RETRY_MAX_BACKOFF_MS"

# Retryable status codes from spec
RETRYABLE_STATUSES = [
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.ABORTED,
]

# Non-retryable status codes from spec
NON_RETRYABLE_STATUSES = [
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.INTERNAL,
]

@pytest.fixture(autouse=True)
def reset_env_vars():
    """Reset environment variables before each test"""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def mock_product_catalog_stub():
    """Mock the product catalog gRPC stub"""
    with patch('src.product_reviews.product_reviews_server.product_catalog_stub') as mock_stub:
        yield mock_stub

@pytest.fixture
def mock_logger():
    """Mock the structured logger"""
    with patch('src.product_reviews.product_reviews_server.logger') as mock_log:
        yield mock_log

def test_ac1_retry_retryable_status_codes(mock_product_catalog_stub, mock_logger):
    """AC-1: Retry up to max attempts on retryable status codes before propagating error"""
    # Set retry attempts to 2 (so total 3 calls: 1 initial + 2 retries)
    os.environ[RETRY_MAX_ATTEMPTS_ENV] = "2"
    test_product_id = "test-product-123"
    
    # Make the stub raise UNAVAILABLE every time
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    error.details = lambda: "Service temporarily unavailable"
    error.trailing_metadata = lambda: ()
    mock_product_catalog_stub.GetProduct.side_effect = error
    
    # Expect the error to be raised after retries
    with pytest.raises(grpc.RpcError) as exc_info:
        fetch_product_details(test_product_id)
    
    # Verify we called GetProduct exactly 3 times (1 initial + 2 retries)
    assert mock_product_catalog_stub.GetProduct.call_count == 3
    # Verify the error is the same one we raised
    assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
    assert exc_info.value.details() == "Service temporarily unavailable"

def test_ac2_exponential_backoff_with_jitter(mock_product_catalog_stub, mock_logger):
    """AC-2: Retry intervals follow exponential backoff with jitter, capped at max backoff"""
    os.environ[RETRY_MAX_ATTEMPTS_ENV] = "3"
    os.environ[RETRY_INITIAL_BACKOFF_ENV] = "100"
    os.environ[RETRY_MAX_BACKOFF_ENV] = "1000"
    test_product_id = "test-product-123"
    
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    error.details = lambda: "Service temporarily unavailable"
    mock_product_catalog_stub.GetProduct.side_effect = error
    
    start_time = time.time()
    with pytest.raises(grpc.RpcError):
        fetch_product_details(test_product_id)
    total_time = time.time() - start_time
    
    # Expected minimum total backoff: 100ms + 200ms + 400ms = 700ms = 0.7s
    # Expected maximum total backoff: 1000ms + 1000ms + 1000ms = 3000ms = 3s
    assert total_time > 0.6, f"Total time {total_time}s is too short for exponential backoff"
    assert total_time < 4.0, f"Total time {total_time}s is too long (capped at 1s per retry)"
    
    # Check call count
    assert mock_product_catalog_stub.GetProduct.call_count == 4  # 1 initial + 3 retries

def test_ac3_configurable_retry_parameters(mock_product_catalog_stub, mock_logger):
    """AC-3: Environment variables correctly configure retry parameters at startup"""
    # Test with custom values
    os.environ[RETRY_MAX_ATTEMPTS_ENV] = "5"
    os.environ[RETRY_INITIAL_BACKOFF_ENV] = "50"
    os.environ[RETRY_MAX_BACKOFF_ENV] = "500"
    test_product_id = "test-product-123"
    
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.UNAVAILABLE
    mock_product_catalog_stub.GetProduct.side_effect = error
    
    with pytest.raises(grpc.RpcError):
        fetch_product_details(test_product_id)
    
    # 1 initial + 5 retries = 6 calls
    assert mock_product_catalog_stub.GetProduct.call_count == 6

def test_ac4_retry_logging(mock_product_catalog_stub, mock_logger):
    """AC-4: Every retry attempt logs INFO level message with attempt number, product ID, and status code"""
    os.environ[RETRY_MAX_ATTEMPTS_ENV] = "2"
    test_product_id = "test-product-123"
    
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.RESOURCE_EXHAUSTED
    error.details = lambda: "Quota exceeded"
    mock_product_catalog_stub.GetProduct.side_effect = error
    
    with pytest.raises(grpc.RpcError):
        fetch_product_details(test_product_id)
    
    # Verify we have 2 info log entries for retries
    assert mock_logger.info.call_count == 2
    
    # Check first retry log entry
    first_call_args = mock_logger.info.call_args_list[0][0][0]
    assert "retry attempt 1" in first_call_args
    assert test_product_id in first_call_args
    assert "RESOURCE_EXHAUSTED" in first_call_args or str(grpc.StatusCode.RESOURCE_EXHAUSTED.value[0]) in first_call_args
    
    # Check second retry log entry
    second_call_args = mock_logger.info.call_args_list[1][0][0]
    assert "retry attempt 2" in second_call_args
    assert test_product_id in second_call_args
    assert "RESOURCE_EXHAUSTED" in second_call_args or str(grpc.StatusCode.RESOURCE_EXHAUSTED.value[0]) in second_call_args

def test_ac5_error_preserved_after_exhausted_retries(mock_product_catalog_stub, mock_logger):
    """AC-5: Error preserves status code, details, metadata after retries are exhausted"""
    os.environ[RETRY_MAX_ATTEMPTS_ENV] = "1"
    test_product_id = "test-product-123"
    test_metadata = (("x-test-metadata", "test-value"),)
    
    # Create a test error with all properties
    error = grpc.RpcError()
    error.code = lambda: grpc.StatusCode.ABORTED
    error.details = lambda: "Concurrency conflict, please retry"
    error.trailing_metadata = lambda: test_metadata
    mock_product_catalog_stub.GetProduct.side_effect = error
    
    with pytest.raises(grpc.RpcError) as exc_info:
        fetch_product_details(test_product_id)
    
    # Verify all error properties are preserved
    assert exc_info.value.code() == grpc.StatusCode.ABORTED
    assert exc_info.value.details() == "Concurrency conflict, please retry"
    assert exc_info.value.trailing_metadata() == test_metadata

@pytest.mark.parametrize("status_code", NON_RETRYABLE_STATUSES)
def test_ac6_no_retry_non_retryable_status_codes(status_code, mock_product_catalog_stub, mock_logger):
    """AC-6: Non-retryable status codes propagate immediately without retries"""
    os.environ[RETRY_MAX_ATTEMPTS_ENV] = "3"  # Even with high max retries
    test_product_id = "test-product-123"
    
    error = grpc.RpcError()
    error.code = lambda: status_code
    error.details = lambda: f"{status_code.name} error"
    mock_product_catalog_stub.GetProduct.side_effect = error
    
    start_time = time.time()
    with pytest.raises(grpc.RpcError) as exc_info:
        fetch_product_details(test_product_id)
    total_time = time.time() - start_time
    
    # Only 1 call, no retries
    assert mock_product_catalog_stub.GetProduct.call_count == 1
    # No retry logs
    assert mock_logger.info.call_count == 0
    # Error propagates immediately
    assert total_time < 0.1, f"Non-retryable error took {total_time}s, should propagate immediately"
    assert exc_info.value.code() == status_code

def test_ac7_success_no_retry_no_logging(mock_product_catalog_stub, mock_logger):
    """AC-7: Successful initial call returns immediately with no retries or retry logging"""
    test_product_id = "test-product-123"
    mock_response = Mock()
    mock_response.id = test_product_id
    mock_response.name = "Test Product"
    mock_response.description = "A test product"
    mock_product_catalog_stub.GetProduct.return_value = mock_response
    
    result = fetch_product_details(test_product_id)
    
    # Verify only 1 call
    assert mock_product_catalog_stub.GetProduct.call_count == 1
    # No retry logs
    assert mock_logger.info.call_count == 0
    # Returns the correct product
    assert result.id == test_product_id
    assert result.name == "Test Product"
