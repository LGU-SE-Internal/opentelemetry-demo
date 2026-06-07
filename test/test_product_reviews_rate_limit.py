#!/usr/bin/env python3
"""Integration tests for product-reviews service rate limiting feature (issue #1349)"""

import os
import time
import grpc
import pytest
from typing import Tuple
from unittest.mock import patch, MagicMock
from src.product_reviews import demo_pb2, demo_pb2_grpc

# Test constants matching spec
RATE_LIMIT_ENABLED_ENV = "PRODUCT_REVIEWS_RATE_LIMIT_ENABLED"
RATE_LIMIT_REQUESTS_ENV = "PRODUCT_REVIEWS_RATE_LIMIT_REQUESTS_PER_MINUTE"
DEFAULT_RATE_LIMIT = 100
RATE_LIMIT_WINDOW_SEC = 60
RATE_EXCEEDED_STATUS_CODE = grpc.StatusCode.RESOURCE_EXHAUSTED
RATE_EXCEEDED_MESSAGE = "Rate limit exceeded. Try again later."

# Test client IPs
TEST_IP_1 = "192.168.1.100"
TEST_IP_2 = "192.168.1.101"

@pytest.fixture(autouse=True)
def reset_env_vars():
    """Reset environment variables before each test"""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def grpc_stub() -> Tuple[demo_pb2_grpc.ProductReviewsServiceStub, grpc.Server]:
    """Create an in-memory gRPC server and stub for testing"""
    from src.product_reviews.product_reviews_server import create_server
    
    server = create_server(testing=True)
    server.add_insecure_port("[::]:0")
    server.start()
    
    channel = grpc.insecure_channel(f"localhost:{server.server_port()}")
    stub = demo_pb2_grpc.ProductReviewsServiceStub(channel)
    
    yield stub, server
    
    server.stop(0)

def add_client_ip_metadata(client_ip: str, method, request, target, options, channel_credentials, call_credentials, compression, wait_for_ready, timeout):
    """Interceptor to add x-forwarded-for header for client IP simulation"""
    metadata = (("x-forwarded-for", client_ip),)
    if options is None:
        options = []
    options.append(("grpc.primary_user_agent", "test-client"))
    return method(request, target, options, channel_credentials, call_credentials, compression, wait_for_ready, timeout)

def test_ac1_below_rate_limit_all_requests_succeed(grpc_stub):
    """AC-1: ≤ 100 requests per minute per client succeed for both endpoints"""
    stub, server = grpc_stub
    
    # Intercept calls to add test client IP
    mock_interceptor = MagicMock()
    mock_interceptor.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_1, *args, **kwargs)
    intercept_channel = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor)
    stub = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel)
    
    # Send 100 requests to GetProductReviews, all should succeed
    for _ in range(DEFAULT_RATE_LIMIT):
        resp = stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
        assert resp is not None
        assert resp.product_id == "1"
    
    # Send 100 requests to SubmitProductReview, all should succeed
    for _ in range(DEFAULT_RATE_LIMIT):
        resp = stub.SubmitProductReview(demo_pb2.SubmitProductReviewRequest(
            product_id="1",
            user_id="u1",
            rating=5,
            text="Great product"
        ))
        assert resp is not None
        assert resp.success == True

def test_ac2_above_rate_limit_returns_resource_exhausted(grpc_stub):
    """AC-2: >100 requests per minute return RESOURCE_EXHAUSTED"""
    stub, server = grpc_stub
    
    mock_interceptor = MagicMock()
    mock_interceptor.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_1, *args, **kwargs)
    intercept_channel = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor)
    stub = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel)
    
    # Send 100 successful requests
    for _ in range(DEFAULT_RATE_LIMIT):
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    # Next request should fail with rate limit
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    assert excinfo.value.code() == RATE_EXCEEDED_STATUS_CODE
    assert RATE_EXCEEDED_MESSAGE in str(excinfo.value.details())
    
    # Test SubmitProductReview endpoint too
    for _ in range(DEFAULT_RATE_LIMIT):
        stub.SubmitProductReview(demo_pb2.SubmitProductReviewRequest(
            product_id="1", user_id="u1", rating=5, text="test"
        ))
    
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.SubmitProductReview(demo_pb2.SubmitProductReviewRequest(
            product_id="1", user_id="u1", rating=5, text="test"
        ))
    
    assert excinfo.value.code() == RATE_EXCEEDED_STATUS_CODE
    assert RATE_EXCEEDED_MESSAGE in str(excinfo.value.details())

def test_ac3_custom_rate_limit_configured_through_env(grpc_stub):
    """AC-3: Custom rate limit value from env is applied"""
    custom_limit = 200
    os.environ[RATE_LIMIT_REQUESTS_ENV] = str(custom_limit)
    
    # Restart server with new env var
    from src.product_reviews.product_reviews_server import create_server
    server = create_server(testing=True)
    server.add_insecure_port("[::]:0")
    server.start()
    
    mock_interceptor = MagicMock()
    mock_interceptor.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_1, *args, **kwargs)
    intercept_channel = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor)
    stub = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel)
    
    # Send custom_limit requests, all should succeed
    for _ in range(custom_limit):
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    # Next request should fail
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    assert excinfo.value.code() == RATE_EXCEEDED_STATUS_CODE
    server.stop(0)

def test_ac4_rate_limit_disabled_via_env_no_limiting(grpc_stub):
    """AC-4: Rate limit disabled when env var is set to false"""
    os.environ[RATE_LIMIT_ENABLED_ENV] = "false"
    
    # Restart server
    from src.product_reviews.product_reviews_server import create_server
    server = create_server(testing=True)
    server.add_insecure_port("[::]:0")
    server.start()
    
    mock_interceptor = MagicMock()
    mock_interceptor.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_1, *args, **kwargs)
    intercept_channel = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor)
    stub = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel)
    
    # Send way more than default limit, all should succeed
    for _ in range(DEFAULT_RATE_LIMIT * 2):
        resp = stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
        assert resp is not None
    
    server.stop(0)

def test_ac5_rate_limit_violations_logged_with_correct_fields(grpc_stub, caplog):
    """AC-5: Rate limit violations are logged with required fields"""
    stub, server = grpc_stub
    caplog.set_level("INFO")
    
    mock_interceptor = MagicMock()
    mock_interceptor.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_1, *args, **kwargs)
    intercept_channel = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor)
    stub = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel)
    
    # Exceed rate limit
    for _ in range(DEFAULT_RATE_LIMIT):
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    with pytest.raises(grpc.RpcError):
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    # Check logs
    rate_limit_logs = [record for record in caplog.records if "Rate limit exceeded" in record.message]
    assert len(rate_limit_logs) == 1
    
    log_record = rate_limit_logs[0]
    assert hasattr(log_record, "client_ip") and log_record.client_ip == TEST_IP_1
    assert hasattr(log_record, "endpoint") and log_record.endpoint == "GetProductReviews"
    assert hasattr(log_record, "request_count") and log_record.request_count == DEFAULT_RATE_LIMIT + 1
    assert hasattr(log_record, "rate_limit") and log_record.rate_limit == DEFAULT_RATE_LIMIT
    assert hasattr(log_record, "timestamp")

def test_ac6_rate_limit_per_client_independent(grpc_stub):
    """AC-6: Rate limits are independent per client IP"""
    stub, server = grpc_stub
    
    # Client 1 exceeds rate limit
    mock_interceptor1 = MagicMock()
    mock_interceptor1.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_1, *args, **kwargs)
    intercept_channel1 = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor1)
    stub1 = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel1)
    
    for _ in range(DEFAULT_RATE_LIMIT):
        stub1.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    with pytest.raises(grpc.RpcError) as excinfo:
        stub1.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    assert excinfo.value.code() == RATE_EXCEEDED_STATUS_CODE
    
    # Client 2 should still be able to send requests
    mock_interceptor2 = MagicMock()
    mock_interceptor2.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_2, *args, **kwargs)
    intercept_channel2 = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor2)
    stub2 = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel2)
    
    # All requests from client 2 succeed
    for _ in range(DEFAULT_RATE_LIMIT):
        resp = stub2.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
        assert resp is not None

def test_ac7_rate_limit_counters_reset_every_minute(grpc_stub):
    """AC-7: Rate limit counters reset after 60 second window"""
    stub, server = grpc_stub
    
    mock_interceptor = MagicMock()
    mock_interceptor.unary_unary = lambda *args, **kwargs: add_client_ip_metadata(TEST_IP_1, *args, **kwargs)
    intercept_channel = grpc.intercept_channel(grpc.insecure_channel(f"localhost:{server.server_port()}"), mock_interceptor)
    stub = demo_pb2_grpc.ProductReviewsServiceStub(intercept_channel)
    
    # Exceed limit in first window
    for _ in range(DEFAULT_RATE_LIMIT):
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    with pytest.raises(grpc.RpcError):
        stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
    
    # Fast forward time to next window
    with patch('time.time', return_value=time.time() + RATE_LIMIT_WINDOW_SEC + 1):
        # Requests should succeed again
        for _ in range(DEFAULT_RATE_LIMIT):
            resp = stub.GetProductReviews(demo_pb2.GetProductReviewsRequest(product_id="1"))
            assert resp is not None
