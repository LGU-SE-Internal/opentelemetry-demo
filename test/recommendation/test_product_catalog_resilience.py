import grpc
import time
import pytest
from unittest.mock import Mock, patch
from src.recommendation import recommendation_server
from src.recommendation import demo_pb2, demo_pb2_grpc

# Retryable status codes from spec
RETRYABLE_CODES = [
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.ABORTED,
    grpc.StatusCode.INTERNAL,
    grpc.StatusCode.DEADLINE_EXCEEDED,
]

# Non-retryable status codes from spec
NON_RETRYABLE_CODES = [
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.ALREADY_EXISTS,
    grpc.StatusCode.FAILED_PRECONDITION,
    grpc.StatusCode.OUT_OF_RANGE,
    grpc.StatusCode.UNIMPLEMENTED,
    grpc.StatusCode.UNAUTHENTICATED,
]

SERVICE_CONFIG = {
    "methodConfig": [
        {
            "name": [
                {"service": "oteldemo.ProductCatalogService", "method": "ListProducts"}
            ],
            "timeout": "10s",
            "retryPolicy": {
                "maxAttempts": 3,
                "initialBackoff": "0.1s",
                "maxBackoff": "1s",
                "backoffMultiplier": 2,
                "retryableStatusCodes": [code.name for code in RETRYABLE_CODES],
            },
        }
    ]
}


class TestProductCatalogResilience:
    @pytest.fixture
    def mock_product_catalog_stub(self):
        with patch("src.recommendation.recommendation_server.demo_pb2_grpc.ProductCatalogServiceStub") as mock_stub:
            yield mock_stub.return_value

    @pytest.fixture
    def mock_logger(self):
        with patch("src.recommendation.recommendation_server.logger") as mock_log:
            yield mock_log

    def test_ac1_successful_response_no_retries(self, mock_product_catalog_stub, mock_logger):
        """AC-1: Successful response within 5s: no retries, no warning logs"""
        # Setup mock success response
        mock_product_catalog_stub.ListProducts.return_value = demo_pb2.ListProductsResponse(products=[])
        
        # Invoke ListProducts
        start_time = time.time()
        result = recommendation_server.get_product_list()
        elapsed = time.time() - start_time
        
        # Verify
        assert elapsed < 5.0
        assert mock_product_catalog_stub.ListProducts.call_count == 1
        assert not any("product_catalog_retry_attempt" in str(call) for call in mock_logger.warning.call_args_list)
        assert result is not None

    @pytest.mark.parametrize("retryable_code", RETRYABLE_CODES)
    def test_ac2_retry_on_transient_errors_up_to_3_attempts(self, retryable_code, mock_product_catalog_stub, mock_logger):
        """AC-2: Retry up to 3 times on retryable status codes before propagating error"""
        # Setup mock to return retryable error every time
        mock_product_catalog_stub.ListProducts.side_effect = grpc.RpcError()
        mock_product_catalog_stub.ListProducts.side_effect.code = lambda: retryable_code
        
        # Invoke and expect error
        with pytest.raises(grpc.RpcError) as exc_info:
            recommendation_server.get_product_list()
        
        # Verify
        assert exc_info.value.code() == retryable_code
        assert mock_product_catalog_stub.ListProducts.call_count == 3  # Max attempts from spec

    def test_ac3_exponential_backoff_jitter(self, mock_product_catalog_stub):
        """AC-3: Exponential backoff with jitter: ~100ms, ~200ms, ~400ms delays between retries"""
        # Setup mock to return retryable error every time
        mock_product_catalog_stub.ListProducts.side_effect = grpc.RpcError()
        mock_product_catalog_stub.ListProducts.side_effect.code = lambda: grpc.StatusCode.UNAVAILABLE
        
        # Measure time for full request with retries
        start_time = time.time()
        with pytest.raises(grpc.RpcError):
            recommendation_server.get_product_list()
        total_elapsed = time.time() - start_time
        
        # Expected total backoff: 0.1 + 0.2 + 0.4 = 0.7s ±20% jitter
        # Allow for small overhead of gRPC processing
        assert total_elapsed >= 0.56  # 0.7 * 0.8
        assert total_elapsed <= 1.0  # 0.7 * 1.2 + small overhead

    def test_ac4_total_request_timeout_10s(self, mock_product_catalog_stub):
        """AC-4: Total elapsed time including all retries never exceeds 10s"""
        # Setup mock to hang/return DEADLINE_EXCEEDED every time
        mock_product_catalog_stub.ListProducts.side_effect = grpc.RpcError()
        mock_product_catalog_stub.ListProducts.side_effect.code = lambda: grpc.StatusCode.DEADLINE_EXCEEDED
        
        # Measure time for full request
        start_time = time.time()
        with pytest.raises(grpc.RpcError):
            recommendation_server.get_product_list()
        total_elapsed = time.time() - start_time
        
        # Verify total time does not exceed 10s + small overhead
        assert total_elapsed <= 10.5

    def test_ac5_retry_logs_emitted_with_all_fields(self, mock_product_catalog_stub, mock_logger):
        """AC-5: Each retry emits warning log with all required structured fields"""
        # Setup mock to return UNAVAILABLE error every time
        mock_product_catalog_stub.ListProducts.side_effect = grpc.RpcError()
        mock_product_catalog_stub.ListProducts.side_effect.code = lambda: grpc.StatusCode.UNAVAILABLE
        mock_product_catalog_stub.ListProducts.side_effect.details = lambda: "Service temporarily unavailable"
        
        # Invoke
        with pytest.raises(grpc.RpcError):
            recommendation_server.get_product_list()
        
        # Verify 2 retry logs (3 attempts total = 2 retries)
        assert mock_logger.warning.call_count == 2
        for idx, call in enumerate(mock_logger.warning.call_args_list, start=1):
            log_kwargs = call.kwargs
            assert log_kwargs["extra"]["event"] == "product_catalog_retry_attempt"
            assert log_kwargs["extra"]["attempt_number"] == idx
            assert log_kwargs["extra"]["error_code"] == "UNAVAILABLE"
            assert log_kwargs["extra"]["error_message"] == "Service temporarily unavailable"
            assert isinstance(log_kwargs["extra"]["backoff_delay_ms"], int)
            # First retry ~100ms, second ~200ms
            expected_backoff = 100 * (2 ** (idx -1))
            assert log_kwargs["extra"]["backoff_delay_ms"] >= expected_backoff * 0.8
            assert log_kwargs["extra"]["backoff_delay_ms"] <= expected_backoff * 1.2
            assert "request_id" in log_kwargs["extra"]

    @pytest.mark.parametrize("non_retryable_code", NON_RETRYABLE_CODES)
    def test_ac6_no_retry_on_non_retryable_errors(self, non_retryable_code, mock_product_catalog_stub, mock_logger):
        """AC-6: Non-retryable status codes result in immediate failure with no retries"""
        # Setup mock to return non-retryable error
        mock_product_catalog_stub.ListProducts.side_effect = grpc.RpcError()
        mock_product_catalog_stub.ListProducts.side_effect.code = lambda: non_retryable_code
        
        # Invoke
        with pytest.raises(grpc.RpcError) as exc_info:
            recommendation_server.get_product_list()
        
        # Verify
        assert exc_info.value.code() == non_retryable_code
        assert mock_product_catalog_stub.ListProducts.call_count == 1
        assert mock_logger.warning.call_count == 0

    def test_ac7_retry_on_attempt_timeout(self, mock_product_catalog_stub):
        """AC-7: Attempts taking longer than 5s time out, retried if within total 10s budget"""
        # Setup mock to take 6s per call (over per-attempt 5s timeout)
        def slow_response(*args, **kwargs):
            time.sleep(6)
            return demo_pb2.ListProductsResponse(products=[])
        
        mock_product_catalog_stub.ListProducts.side_effect = slow_response
        
        # Invoke
        start_time = time.time()
        with pytest.raises(grpc.RpcError) as exc_info:
            recommendation_server.get_product_list()
        total_elapsed = time.time() - start_time
        
        # Verify we got DEADLINE_EXCEEDED, and we retried 1 more time (total 2 attempts: 5s + 5s = 10s total budget)
        assert exc_info.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
        assert mock_product_catalog_stub.ListProducts.call_count == 2
        assert total_elapsed <= 10.5
