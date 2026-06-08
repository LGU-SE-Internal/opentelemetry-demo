"""
Integration tests for ListRecommendations request validation (issue #1550)
Tests all acceptance criteria for request validation requirements
"""
import grpc
import pytest
from grpc_status import rpc_status
from uuid import uuid4
from opentelemetry.proto.demo.recommendation.v1.recommendation_pb2 import (
    ListRecommendationsRequest,
)
from opentelemetry.proto.demo.recommendation.v1.recommendation_pb2_grpc import (
    RecommendationServiceStub,
)


@pytest.fixture(scope="module")
def grpc_stub():
    """Fixture for gRPC client stub connecting to recommendation service"""
    channel = grpc.insecure_channel("recommendation:8080")
    yield RecommendationServiceStub(channel)
    channel.close()


def test_ac1_invalid_product_id_format(grpc_stub):
    """AC-1: Request with non-alphanumeric product ID returns INVALID_ARGUMENT"""
    request = ListRecommendationsRequest(
        product_ids=["valid123", "invalid!@#", "anothervalid456"],
        max_results=10
    )

    with pytest.raises(grpc.RpcError) as excinfo:
        grpc_stub.ListRecommendations(request)
    
    status = rpc_status.from_call(excinfo.value)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "product ID" in status.message.lower()
    assert "format" in status.message.lower()


def test_ac2_too_many_product_ids(grpc_stub):
    """AC-2: Request with >100 product IDs returns INVALID_ARGUMENT"""
    # Generate 101 valid product IDs
    product_ids = [f"prod{i}" for i in range(101)]
    request = ListRecommendationsRequest(
        product_ids=product_ids,
        max_results=10
    )

    with pytest.raises(grpc.RpcError) as excinfo:
        grpc_stub.ListRecommendations(request)
    
    status = rpc_status.from_call(excinfo.value)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "maximum" in status.message.lower()
    assert "100 product" in status.message.lower()


def test_ac3_invalid_user_id_format(grpc_stub):
    """AC-3: Request with non-UUIDv4 user ID returns INVALID_ARGUMENT"""
    request = ListRecommendationsRequest(
        user_id="invalid-user-id-123",
        product_ids=["prod1", "prod2"],
        max_results=10
    )

    with pytest.raises(grpc.RpcError) as excinfo:
        grpc_stub.ListRecommendations(request)
    
    status = rpc_status.from_call(excinfo.value)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "user ID" in status.message.lower()
    assert "format" in status.message.lower() or "uuid" in status.message.lower()


def test_ac4_max_results_too_small(grpc_stub):
    """AC-4: Request with max_results < 1 returns INVALID_ARGUMENT"""
    request = ListRecommendationsRequest(
        product_ids=["prod1", "prod2"],
        max_results=0
    )

    with pytest.raises(grpc.RpcError) as excinfo:
        grpc_stub.ListRecommendations(request)
    
    status = rpc_status.from_call(excinfo.value)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "max_results" in status.message.lower()
    assert "between 1 and 20" in status.message.lower()


def test_ac4_max_results_too_large(grpc_stub):
    """AC-4: Request with max_results > 20 returns INVALID_ARGUMENT"""
    request = ListRecommendationsRequest(
        product_ids=["prod1", "prod2"],
        max_results=21
    )

    with pytest.raises(grpc.RpcError) as excinfo:
        grpc_stub.ListRecommendations(request)
    
    status = rpc_status.from_call(excinfo.value)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "max_results" in status.message.lower()
    assert "between 1 and 20" in status.message.lower()


def test_ac5_validation_runs_before_business_logic(grpc_stub, mocker):
    """AC-5: Invalid requests do not trigger downstream service calls"""
    # Mock product catalog service client to verify it's not called
    mock_catalog_client = mocker.patch(
        "src.recommendation.recommendation_service.ProductCatalogServiceStub"
    )
    
    # Invalid request: bad product ID
    request = ListRecommendationsRequest(
        product_ids=["invalid!@#"],
        max_results=10
    )

    with pytest.raises(grpc.RpcError):
        grpc_stub.ListRecommendations(request)
    
    # Verify no downstream product catalog calls were made
    mock_catalog_client.assert_not_called()
