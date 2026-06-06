import grpc
import pytest
from unittest.mock import Mock, patch
from src.recommendation.demo_pb2 import ListRecommendationsRequest
from src.recommendation.demo_pb2_grpc import RecommendationServiceStub

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
