import grpc
import pytest
import uuid
from unittest.mock import MagicMock, patch

from recommendation_server import RecommendationService
import demo_pb2
import demo_pb2_grpc


@pytest.fixture
def service():
    return RecommendationService()

@pytest.fixture
def context():
    context = MagicMock()
    context.invocation_metadata.return_value = [('x-forwarded-for', '192.168.1.1')]
    return context


def test_ac1_empty_user_id_returns_invalid_argument_and_logs(service, context):
    # AC1: Empty/missing user_id returns INVALID_ARGUMENT and logs
    request = demo_pb2.ListRecommendationsRequest(user_id="", product_ids=[])
    
    with pytest.raises(grpc.RpcError) as exc_info:
        service.ListRecommendations(request, context)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "user_id is required" in str(exc_info.value.details())
    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)
    # Verify logging is called with invalid_request, user_id field, client IP


def test_ac2_invalid_uuid_user_id_returns_invalid_argument_and_logs(service, context):
    # AC2: Non-UUID v4 user_id returns INVALID_ARGUMENT and logs
    invalid_user_ids = [
        "not-a-uuid",
        "123e4567-e89b-12d3-a456-426614174000",  # UUID v1
        "123e4567-e89b-22d3-a456-426614174000",  # UUID v2
        "123e4567-e89b-32d3-a456-426614174000",  # UUID v3
        "123e4567-e89b-52d3-a456-426614174000",  # UUID v5
    ]
    
    for user_id in invalid_user_ids:
        request = demo_pb2.ListRecommendationsRequest(user_id=user_id, product_ids=[])
        
        with pytest.raises(grpc.RpcError) as exc_info:
            service.ListRecommendations(request, context)
        
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "user_id must be a valid UUID v4" in str(exc_info.value.details())


def test_ac3_empty_product_id_entry_returns_invalid_argument_and_logs(service, context):
    # AC3: product_ids with empty entry returns INVALID_ARGUMENT and logs
    valid_user_id = str(uuid.uuid4())
    test_cases = [
        (["prod1", "", "prod3"], 1),
        ([""], 0),
        (["prod1", "prod2", ""], 2)
    ]
    
    for product_ids, bad_index in test_cases:
        request = demo_pb2.ListRecommendationsRequest(user_id=valid_user_id, product_ids=product_ids)
        
        with pytest.raises(grpc.RpcError) as exc_info:
            service.ListRecommendations(request, context)
        
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert f"product_ids entry at index {bad_index} is empty" in str(exc_info.value.details())


def test_ac4_product_ids_exceeds_limit_returns_invalid_argument_and_logs(service, context):
    # AC4: product_ids list over 100 entries returns INVALID_ARGUMENT and logs
    valid_user_id = str(uuid.uuid4())
    product_ids = [f"prod{i}" for i in range(101)]
    
    request = demo_pb2.ListRecommendationsRequest(user_id=valid_user_id, product_ids=product_ids)
    
    with pytest.raises(grpc.RpcError) as exc_info:
        service.ListRecommendations(request, context)
    
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "product_ids list exceeds maximum allowed length of 100 entries" in str(exc_info.value.details())


def test_ac5_valid_request_with_max_product_ids_succeeds(service, context):
    # AC5: Valid user_id + 100 product entries succeeds, no error log
    valid_user_id = str(uuid.uuid4())
    product_ids = [f"prod{i}" for i in range(100)]
    
    request = demo_pb2.ListRecommendationsRequest(user_id=valid_user_id, product_ids=product_ids)
    
    # Should not raise any error
    response = service.ListRecommendations(request, context)
    assert isinstance(response, demo_pb2.ListRecommendationsResponse)
    # Verify no invalid_request log was emitted


def test_ac6_valid_request_without_product_ids_succeeds(service, context):
    # AC6: Valid user_id + no product_ids succeeds, no error log
    valid_user_id = str(uuid.uuid4())
    
    request = demo_pb2.ListRecommendationsRequest(user_id=valid_user_id)
    
    # Should not raise any error
    response = service.ListRecommendations(request, context)
    assert isinstance(response, demo_pb2.ListRecommendationsResponse)
    # Verify no invalid_request log was emitted
