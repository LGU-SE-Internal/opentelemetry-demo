#!/usr/bin/env python3
import pytest
import grpc
from unittest.mock import Mock, patch, MagicMock
from src.recommendation import recommendation_server
from src.recommendation import demo_pb2, demo_pb2_grpc

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

class TestListRecommendationsValidation:
    def test_ac1_product_ids_exceeds_max_100(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # Create request with 101 product IDs
        product_ids = [f"prod{i:03d}" for i in range(101)]
        request = demo_pb2.ListRecommendationsRequest(product_ids=product_ids)
        
        servicer.ListRecommendations(request, mock_context)
        
        # Verify abort called with correct status and message
        mock_context.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            "product_ids list exceeds maximum allowed size of 100"
        )
        # Verify processing did not proceed
        mock_get_product_list.assert_not_called()

    def test_ac2_product_ids_empty(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # Create request with empty product_ids list
        request = demo_pb2.ListRecommendationsRequest(product_ids=[])
        
        servicer.ListRecommendations(request, mock_context)
        
        # Verify abort called with correct status and message
        mock_context.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            "product_ids list cannot be empty"
        )
        # Verify processing did not proceed
        mock_get_product_list.assert_not_called()

    def test_ac3_product_id_too_short(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # Create request with a 2-character product ID at index 1
        request = demo_pb2.ListRecommendationsRequest(product_ids=["prod123", "ab", "prod456"])
        
        servicer.ListRecommendations(request, mock_context)
        
        # Verify abort called with correct status and message
        mock_context.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            "product ID at index 1: invalid format, must be alphanumeric 3-12 characters"
        )
        # Verify processing did not proceed
        mock_get_product_list.assert_not_called()

    def test_ac4_product_id_too_long(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # Create request with a 13-character product ID at index 2
        request = demo_pb2.ListRecommendationsRequest(product_ids=["prod123", "prod456", "abcdefghijklm"])
        
        servicer.ListRecommendations(request, mock_context)
        
        # Verify abort called with correct status and message
        mock_context.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            "product ID at index 2: invalid format, must be alphanumeric 3-12 characters"
        )
        # Verify processing did not proceed
        mock_get_product_list.assert_not_called()

    def test_ac5_product_id_non_alphanumeric(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # Test different non-alphanumeric cases
        test_cases = [
            (["prod123", "prod@123", "prod456"], 1),
            (["prod 123", "prod456"], 0),
            (["prod#123$", "prod456"], 0),
            (["prod-123", "prod456"], 0),
        ]
        
        for product_ids, expected_idx in test_cases:
            mock_context.reset_mock()
            request = demo_pb2.ListRecommendationsRequest(product_ids=product_ids)
            
            servicer.ListRecommendations(request, mock_context)
            
            mock_context.abort.assert_called_once_with(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"product ID at index {expected_idx}: invalid format, must be alphanumeric 3-12 characters"
            )
            mock_get_product_list.assert_not_called()

    def test_ac6_valid_request_processes_successfully(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # Valid request with 5 product IDs, all valid format
        product_ids = ["prod123", "PROD456", "prod789", "a1b2c3d4", "test12345678"]
        request = demo_pb2.ListRecommendationsRequest(product_ids=product_ids)
        
        response = servicer.ListRecommendations(request, mock_context)
        
        # Verify no abort called
        mock_context.abort.assert_not_called()
        # Verify processing proceeded
        mock_get_product_list.assert_called_once_with(product_ids)
        # Verify response is returned
        assert isinstance(response, demo_pb2.ListRecommendationsResponse)
        assert len(response.product_ids) == 2

    def test_ac7_edge_case_valid_inputs(self, servicer, mock_context, mock_trace, mock_get_product_list):
        # Test edge cases that should pass validation
        test_cases = [
            # Exactly 1 product ID
            ["prod123"],
            # Exactly 100 product IDs
            [f"prod{i:03d}" for i in range(100)],
            # Exactly 3 characters long
            ["abc"],
            # Exactly 12 characters long
            ["abcdefghijkl"],
            # Mixed case alphanumeric
            ["Prod123AbcXy"],
            # Only numbers
            ["1234567890"],
            # Only letters
            ["ABCDEFGHIJ"]
        ]
        
        for product_ids in test_cases:
            mock_context.reset_mock()
            mock_get_product_list.reset_mock()
            
            request = demo_pb2.ListRecommendationsRequest(product_ids=product_ids)
            servicer.ListRecommendations(request, mock_context)
            
            mock_context.abort.assert_not_called()
            mock_get_product_list.assert_called_once()
GRPC_TARGET = "localhost:8080"  # Default recommendation service gRPC port


@pytest.fixture(scope="module")
def grpc_channel():
    channel = grpc.insecure_channel(GRPC_TARGET)
    yield channel
    channel.close()


@pytest.fixture(scope="module")
def stub(grpc_channel):
    return RecommendationServiceStub(grpc_channel)


def test_ac1_product_ids_exceeds_max_100(stub):
    # AC-1: >100 entries returns INVALID_ARGUMENT with correct message
    product_ids = [f"prod{i}" for i in range(101)]
    request = ListRecommendationsRequest(product_ids=product_ids)
    
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ListRecommendations(request)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "product_ids list exceeds maximum allowed size of 100" in str(exc_info.value.details())


def test_ac2_product_ids_empty(stub):
    # AC-2: empty list returns INVALID_ARGUMENT with correct message
    request = ListRecommendationsRequest(product_ids=[])
    
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ListRecommendations(request)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "product_ids list cannot be empty" in str(exc_info.value.details())


def test_ac3_product_id_too_short(stub):
    # AC-3: product ID <3 chars returns correct error
    product_ids = ["ab", "prod1", "prod2"]
    request = ListRecommendationsRequest(product_ids=product_ids)
    
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ListRecommendations(request)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "product ID at index 0: invalid format, must be alphanumeric 3-12 characters" in str(exc_info.value.details())


def test_ac4_product_id_too_long(stub):
    # AC-4: product ID >12 chars returns correct error
    product_ids = ["prod1", "abcdefghijklm", "prod2"]
    request = ListRecommendationsRequest(product_ids=product_ids)
    
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ListRecommendations(request)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "product ID at index 1: invalid format, must be alphanumeric 3-12 characters" in str(exc_info.value.details())


def test_ac5_product_id_non_alphanumeric(stub):
    # AC-5: product ID with non-alphanumeric chars returns correct error
    test_cases = [
        (["prod1", "pro d2", "prod3"], 1),
        (["prod1", "prod@2", "prod3"], 1),
        (["prod1", "prod-2", "prod3"], 1),
        (["prod1", "prod_2", "prod3"], 1),
    ]
    
    for product_ids, invalid_idx in test_cases:
        request = ListRecommendationsRequest(product_ids=product_ids)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.ListRecommendations(request)
        
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert f"product ID at index {invalid_idx}: invalid format, must be alphanumeric 3-12 characters" in str(exc_info.value.details())


def test_ac6_valid_request_processes_successfully(stub):
    # AC-6: valid requests proceed without validation errors
    # Edge cases included: exactly 100 entries, 3-char ID, 12-char ID, mixed case
    product_ids = [
        "abc",  # 3 chars
        "ABCDEF123456",  # 12 chars
        "Prod123",  # mixed case
        *[f"prod{i}" for i in range(97)]  # total 100 entries
    ]
    request = ListRecommendationsRequest(product_ids=product_ids)
    
    # Should not raise any INVALID_ARGUMENT error
    response = stub.ListRecommendations(request)
    assert response is not None
    assert hasattr(response, "product_ids")
    assert isinstance(response.product_ids, list)


def test_ac7_edge_case_valid_inputs(stub):
    # AC-7: cover edge cases for valid inputs
    test_cases = [
        ["abc123"],  # 1 entry
        [f"p{i}" for i in range(100)],  # exactly 100 entries
        ["1234567890ab"],  # 12 chars all digits
        ["ABCDEFGHIJKL"],  # 12 chars all uppercase
        ["abcdefghijkl"],  # 12 chars all lowercase
        ["a1B2c3D4e5F6"],  # mixed case and digits
    ]
    
    for product_ids in test_cases:
        request = ListRecommendationsRequest(product_ids=product_ids)
        response = stub.ListRecommendations(request)
        assert response is not None
