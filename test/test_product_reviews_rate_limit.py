#!/usr/bin/env python3
"""Integration tests for product-reviews service rate limiting feature (issue #1778)"""

import os
import grpc
import pytest
from unittest.mock import MagicMock, patch
from prometheus_client import REGISTRY, Counter

# Import from product-reviews service
from src.product_reviews.product_reviews_server import (
    TokenBucketRateLimiter,
    RateLimitInterceptor,
)
from src.product_reviews import demo_pb2, demo_pb2_grpc

# Test constants matching spec
ENDPOINTS = {
    "ListProductReviews": "/opentelemetry.demo.DemoService/ListProductReviews",
    "CreateProductReview": "/opentelemetry.demo.DemoService/CreateProductReview",
    "GetProductReviewSummary": "/opentelemetry.demo.DemoService/GetProductReviewSummary",
    "DeleteProductReview": "/opentelemetry.demo.DemoService/DeleteProductReview",
}
RATE_EXCEEDED_STATUS_CODE = grpc.StatusCode.RESOURCE_EXHAUSTED
RATE_EXCEEDED_MESSAGE = "Rate limit exceeded. Try again later."
METRIC_NAME = "product_reviews_rate_limited_requests_total"

@pytest.fixture(autouse=True)
def reset_env_vars_and_metrics():
    """Reset environment variables and Prometheus metrics before each test"""
    original_env = os.environ.copy()
    # Clear existing metrics
    if METRIC_NAME in REGISTRY._names_to_collectors:
        del REGISTRY._names_to_collectors[METRIC_NAME]
    yield
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def mock_metrics_client():
    mock = MagicMock()
    mock.product_reviews_rate_limited_requests_total = Counter(
        METRIC_NAME,
        "Total number of rate limited requests",
        ["endpoint"]
    )
    return mock

@pytest.fixture
def mock_logger():
    return MagicMock()

@pytest.fixture
def mock_grpc_handler():
    def mock_handler(request, context):
        return demo_pb2.CreateProductReviewResponse(review_id="test-123")
    return mock_handler

class TestRateLimitAC:
    def test_ac1_create_review_rate_limit_exceeded(self, mock_metrics_client, mock_logger, mock_grpc_handler):
        """AC-1: CREATE_PRODUCT_REVIEW limit 10, 11th request in 60s returns RESOURCE_EXHAUSTED"""
        os.environ["PRODUCT_REVIEWS_RATE_LIMIT_CREATE_PRODUCT_REVIEW"] = "10"
        
        endpoint_limits = {
            ENDPOINTS["CreateProductReview"]: TokenBucketRateLimiter(
                capacity=10,
                refill_rate_per_minute=10
            )
        }
        interceptor = RateLimitInterceptor(endpoint_limits, mock_metrics_client, mock_logger)
        
        # Send 10 allowed requests
        for i in range(10):
            context = MagicMock()
            response = interceptor.intercept_service(
                mock_grpc_handler,
                MagicMock(method=ENDPOINTS["CreateProductReview"]),
                context
            )
            assert isinstance(response, demo_pb2.CreateProductReviewResponse)
            context.set_code.assert_not_called()
        
        # 11th request should be rejected
        context = MagicMock()
        response = interceptor.intercept_service(
            mock_grpc_handler,
            MagicMock(method=ENDPOINTS["CreateProductReview"]),
            context
        )
        context.set_code.assert_called_once_with(RATE_EXCEEDED_STATUS_CODE)
        context.set_details.assert_called_once_with(RATE_EXCEEDED_MESSAGE)

    def test_ac2_default_rate_limit_applied(self, mock_metrics_client, mock_logger, mock_grpc_handler):
        """AC-2: Default limit 5 applies to endpoints without explicit config"""
        os.environ["PRODUCT_REVIEWS_RATE_LIMIT_DEFAULT"] = "5"
        
        endpoint_limits = {
            ENDPOINTS["ListProductReviews"]: TokenBucketRateLimiter(
                capacity=5,
                refill_rate_per_minute=5
            ),
            ENDPOINTS["GetProductReviewSummary"]: TokenBucketRateLimiter(
                capacity=5,
                refill_rate_per_minute=5
            )
        }
        interceptor = RateLimitInterceptor(endpoint_limits, mock_metrics_client, mock_logger)
        
        # Test ListProductReviews
        for i in range(5):
            context = MagicMock()
            interceptor.intercept_service(
                mock_grpc_handler,
                MagicMock(method=ENDPOINTS["ListProductReviews"]),
                context
            )
            context.set_code.assert_not_called()
        
        context = MagicMock()
        interceptor.intercept_service(
            mock_grpc_handler,
            MagicMock(method=ENDPOINTS["ListProductReviews"]),
            context
        )
        context.set_code.assert_called_once_with(RATE_EXCEEDED_STATUS_CODE)
        
        # Test GetProductReviewSummary also uses default limit
        for i in range(5):
            context = MagicMock()
            interceptor.intercept_service(
                mock_grpc_handler,
                MagicMock(method=ENDPOINTS["GetProductReviewSummary"]),
                context
            )
            context.set_code.assert_not_called()
        
        context = MagicMock()
        interceptor.intercept_service(
            mock_grpc_handler,
            MagicMock(method=ENDPOINTS["GetProductReviewSummary"]),
            context
        )
        context.set_code.assert_called_once_with(RATE_EXCEEDED_STATUS_CODE)

    def test_ac3_zero_limit_allows_all_requests(self, mock_metrics_client, mock_logger, mock_grpc_handler):
        """AC-3: Rate limit 0 = unlimited, all requests allowed regardless of volume"""
        os.environ["PRODUCT_REVIEWS_RATE_LIMIT_DELETE_PRODUCT_REVIEW"] = "0"
        
        endpoint_limits = {
            ENDPOINTS["DeleteProductReview"]: TokenBucketRateLimiter(
                capacity=0,
                refill_rate_per_minute=0
            )
        }
        interceptor = RateLimitInterceptor(endpoint_limits, mock_metrics_client, mock_logger)
        
        # Send 100 requests, all should pass
        for i in range(100):
            context = MagicMock()
            interceptor.intercept_service(
                mock_grpc_handler,
                MagicMock(method=ENDPOINTS["DeleteProductReview"]),
                context
            )
            context.set_code.assert_not_called()
        # No metrics incremented
        assert mock_metrics_client.product_reviews_rate_limited_requests_total.labels(
            endpoint=ENDPOINTS["DeleteProductReview"]
        )._value.get() == 0

    def test_ac4_rate_limited_request_increments_metric(self, mock_metrics_client, mock_logger, mock_grpc_handler):
        """AC-4: Rejected requests increment Prometheus counter with correct endpoint label"""
        endpoint_limits = {
            ENDPOINTS["CreateProductReview"]: TokenBucketRateLimiter(
                capacity=1,
                refill_rate_per_minute=1
            )
        }
        interceptor = RateLimitInterceptor(endpoint_limits, mock_metrics_client, mock_logger)
        
        # First request allowed
        context = MagicMock()
        interceptor.intercept_service(
            mock_grpc_handler,
            MagicMock(method=ENDPOINTS["CreateProductReview"]),
            context
        )
        assert mock_metrics_client.product_reviews_rate_limited_requests_total.labels(
            endpoint=ENDPOINTS["CreateProductReview"]
        )._value.get() == 0
        
        # Second request rejected, metric increments
        context = MagicMock()
        interceptor.intercept_service(
            mock_grpc_handler,
            MagicMock(method=ENDPOINTS["CreateProductReview"]),
            context
        )
        assert mock_metrics_client.product_reviews_rate_limited_requests_total.labels(
            endpoint=ENDPOINTS["CreateProductReview"]
        )._value.get() == 1

    def test_ac5_rate_limit_violation_emits_structured_log(self, mock_metrics_client, mock_logger, mock_grpc_handler):
        """AC-5: Rate limit violations emit structured log with all required fields"""
        endpoint_limits = {
            ENDPOINTS["ListProductReviews"]: TokenBucketRateLimiter(
                capacity=1,
                refill_rate_per_minute=1
            )
        }
        interceptor = RateLimitInterceptor(endpoint_limits, mock_metrics_client, mock_logger)
        test_client_ip = "192.168.1.100"
        
        # First request allowed
        context = MagicMock()
        mock_call = MagicMock(
            method=ENDPOINTS["ListProductReviews"],
            peer=lambda: f"ipv4:{test_client_ip}:12345"
        )
        interceptor.intercept_service(mock_grpc_handler, mock_call, context)
        mock_logger.info.assert_not_called()
        
        # Second request rejected, log emitted
        context = MagicMock()
        interceptor.intercept_service(mock_grpc_handler, mock_call, context)
        mock_logger.info.assert_called_once()
        log_args = mock_logger.info.call_args[0][0]
        assert "timestamp" in log_args
        assert log_args["endpoint"] == ENDPOINTS["ListProductReviews"]
        assert log_args["remote_addr"] == test_client_ip
        assert log_args["rate_limit"] == 1
        assert log_args["status"] == "rejected"

    def test_ac6_allowed_requests_passed_unchanged(self, mock_metrics_client, mock_logger):
        """AC-6: Allowed requests are passed to handler unmodified, response unchanged"""
        expected_request = demo_pb2.CreateProductReviewRequest(
            product_id="test-prod-123",
            user_id="user-456",
            rating=5,
            text="Great product!"
        )
        expected_response = demo_pb2.CreateProductReviewResponse(
            review_id="rev-789"
        )
        
        def test_handler(request, context):
            assert request == expected_request
            return expected_response
        
        endpoint_limits = {
            ENDPOINTS["CreateProductReview"]: TokenBucketRateLimiter(
                capacity=10,
                refill_rate_per_minute=10
            )
        }
        interceptor = RateLimitInterceptor(endpoint_limits, mock_metrics_client, mock_logger)
        
        mock_call = MagicMock(
            method=ENDPOINTS["CreateProductReview"],
            request=expected_request
        )
        context = MagicMock()
        response = interceptor.intercept_service(test_handler, mock_call, context)
        
        assert response == expected_response
        context.set_code.assert_not_called()

    def test_ac7_rate_limits_isolated_per_endpoint(self, mock_metrics_client, mock_logger, mock_grpc_handler):
        """AC-7: Rate limits are per endpoint, one endpoint hitting limit doesn't affect others"""
        endpoint_limits = {
            ENDPOINTS["CreateProductReview"]: TokenBucketRateLimiter(
                capacity=1,
                refill_rate_per_minute=1
            ),
            ENDPOINTS["ListProductReviews"]: TokenBucketRateLimiter(
                capacity=10,
                refill_rate_per_minute=10
            )
        }
        interceptor = RateLimitInterceptor(endpoint_limits, mock_metrics_client, mock_logger)
        
        # Hit limit on CreateProductReview
        context1 = MagicMock()
        interceptor.intercept_service(
            mock_grpc_handler,
            MagicMock(method=ENDPOINTS["CreateProductReview"]),
            context1
        )
        context2 = MagicMock()
        interceptor.intercept_service(
            mock_grpc_handler,
            MagicMock(method=ENDPOINTS["CreateProductReview"]),
            context2
        )
        context2.set_code.assert_called_once_with(RATE_EXCEEDED_STATUS_CODE)
        
        # ListProductReviews should still work
        for i in range(10):
            context = MagicMock()
            interceptor.intercept_service(
                mock_grpc_handler,
                MagicMock(method=ENDPOINTS["ListProductReviews"]),
                context
            )
            context.set_code.assert_not_called()
        # Metric for ListProductReviews is still 0
        assert mock_metrics_client.product_reviews_rate_limited_requests_total.labels(
            endpoint=ENDPOINTS["ListProductReviews"]
        )._value.get() == 0
