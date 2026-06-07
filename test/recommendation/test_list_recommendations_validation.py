#!/usr/bin/env python3
import pytest
import grpc
import re
from src.recommendation import recommendation_server
from src.recommendation.demo_pb2 import ListRecommendationsRequest, ListRecommendationsResponse
from src.recommendation.demo_pb2_grpc import RecommendationServiceStub
from unittest.mock import Mock, patch

GRPC_TARGET = "localhost:8080"

@pytest.fixture
def servicer():
    return recommendation_server.RecommendationService()

@pytest.fixture
def mock_context():
    context = Mock()
    context.abort = Mock()
    return context

@pytest.fixture
def mock_trace():
    with patch('src.recommendation.recommendation_server.trace') as mock_trace:
        mock_span = Mock()
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value.trace_id = 0x123456789abcdef0
        mock_trace.get_current_span.return_value = mock_span
        yield mock_trace

@pytest.fixture
def mock_get_product_list():
    with patch('src.recommendation.recommendation_server.get_product_list') as mock:
        mock.return_value = ["prod123", "prod456"]
        yield mock

@pytest.fixture(scope="module")
def grpc_channel():
    channel = grpc.insecure_channel(GRPC_TARGET)
    yield channel
    channel.close()

@pytest.fixture(scope="module")
def stub(grpc_channel):
    return RecommendationServiceStub(grpc_channel)

# Unit tests (mock based)
class TestListRecommendationsValidationUnit:
    def test_ac1_missing_user_id(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # AC-1: Request with missing user_id parameter returns INVALID_ARGUMENT
        request = ListRecommendationsRequest()
        
        servicer.ListRecommendations(request, mock_context)
        
        mock_context.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            "user_id parameter is required"
        )
        mock_get_product_list.assert_not_called()

    def test_ac2_invalid_user_id_format(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # AC-2: user_id invalid format returns correct error
        invalid_cases = [
            "ab",  # <3 chars
            "a" * 37,  # >36 chars
            "user@123",  # non alphanumeric/-
            "user name",  # space
            "user#123",  # special char
        ]
        
        for user_id in invalid_cases:
            mock_context.reset_mock()
            request = ListRecommendationsRequest(user_id=user_id)
            
            servicer.ListRecommendations(request, mock_context)
            
            mock_context.abort.assert_called_once_with(
                grpc.StatusCode.INVALID_ARGUMENT,
                "user_id has invalid format: must be alphanumeric (including '-') between 3-36 characters"
            )
            mock_get_product_list.assert_not_called()

    def test_ac3_product_ids_exceeds_max_100(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # AC-3: product_ids list >100 items returns error
        product_ids = [f"prod{i:03d}" for i in range(101)]
        request = ListRecommendationsRequest(user_id="test-user-123", product_ids=product_ids)
        
        servicer.ListRecommendations(request, mock_context)
        
        mock_context.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            "product_ids list exceeds maximum allowed size of 100"
        )
        mock_get_product_list.assert_not_called()

    def test_ac4_invalid_product_id_format(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # AC-4: invalid product ID format returns error with index
        test_cases = [
            (["ab", "prod123"], 0, "too short"),
            (["prod123", "a" * 13, "prod456"], 1, "too long"),
            (["prod123", "prod@123"], 1, "special char"),
            (["prod 123", "prod456"], 0, "space"),
        ]
        
        for product_ids, expected_idx, _ in test_cases:
            mock_context.reset_mock()
            request = ListRecommendationsRequest(user_id="test-user-123", product_ids=product_ids)
            
            servicer.ListRecommendations(request, mock_context)
            
            mock_context.abort.assert_called_once_with(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"product ID at index {expected_idx}: invalid format, must be alphanumeric 3-12 characters"
            )
            mock_get_product_list.assert_not_called()

    def test_ac5_result_size_out_of_bounds(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # AC-5: result_size <1 or >20 returns error
        invalid_sizes = [0, -1, 21, 100]
        
        for size in invalid_sizes:
            mock_context.reset_mock()
            request = ListRecommendationsRequest(user_id="test-user-123", result_size=size)
            
            servicer.ListRecommendations(request, mock_context)
            
            mock_context.abort.assert_called_once_with(
                grpc.StatusCode.INVALID_ARGUMENT,
                "result_size must be between 1 and 20 (inclusive)"
            )
            mock_get_product_list.assert_not_called()

    def test_ac6_control_characters_stripped(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # AC-6: control characters are stripped from parameters
        request = ListRecommendationsRequest(
            user_id="test\x00user-123\n",
            product_ids=["prod\x01123", "prod\x7f456"],
            result_size=5
        )
        
        servicer.ListRecommendations(request, mock_context)
        
        mock_context.abort.assert_not_called()
        # Verify get_product_list was called with sanitized product IDs
        called_product_ids = mock_get_product_list.call_args[0][0]
        assert "\x01" not in called_product_ids[0]
        assert "\x7f" not in called_product_ids[1]
        # Verify user_id is sanitized
        assert "\x00" not in request.user_id
        assert "\n" not in request.user_id

    def test_ac7_validation_failures_logged(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # AC-7: validation failures are logged with trace_id, param name and details
        with patch('src.recommendation.recommendation_server.logger') as mock_logger:
            request = ListRecommendationsRequest(user_id="ab")
            
            servicer.ListRecommendations(request, mock_context)
            
            mock_logger.error.assert_called_once()
            log_args = mock_logger.error.call_args[0][0]
            assert "123456789abcdef0" in log_args  # trace_id
            assert "user_id" in log_args
            assert "invalid format" in log_args

# Integration tests (against running service)
def test_ac1_missing_user_id_integration(stub):
    request = ListRecommendationsRequest()
    
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ListRecommendations(request)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "user_id parameter is required" in str(exc_info.value.details())

def test_ac2_invalid_user_id_format_integration(stub):
    invalid_cases = [
        "ab",
        "a" * 37,
        "user@123",
        "user name"
    ]
    
    for user_id in invalid_cases:
        request = ListRecommendationsRequest(user_id=user_id)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.ListRecommendations(request)
        
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "user_id has invalid format" in str(exc_info.value.details())

def test_ac3_product_ids_exceeds_max_100_integration(stub):
    product_ids = [f"prod{i:03d}" for i in range(101)]
    request = ListRecommendationsRequest(user_id="test-user-123", product_ids=product_ids)
    
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ListRecommendations(request)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "product_ids list exceeds maximum allowed size of 100" in str(exc_info.value.details())

def test_ac4_invalid_product_id_format_integration(stub):
    test_cases = [
        (["ab", "prod123"], 0),
        (["prod123", "a" * 13, "prod456"], 1),
        (["prod123", "prod@123"], 1)
    ]
    
    for product_ids, idx in test_cases:
        request = ListRecommendationsRequest(user_id="test-user-123", product_ids=product_ids)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.ListRecommendations(request)
        
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert f"product ID at index {idx}" in str(exc_info.value.details())

def test_ac5_result_size_out_of_bounds_integration(stub):
    for size in [0, -1, 21, 100]:
        request = ListRecommendationsRequest(user_id="test-user-123", result_size=size)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.ListRecommendations(request)
        
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "result_size must be between 1 and 20" in str(exc_info.value.details())
