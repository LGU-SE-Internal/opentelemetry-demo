import grpc
import pytest
from google.protobuf import empty_pb2

from pb.product_reviews_pb2 import (
    GetProductReviewsRequest,
    SubmitProductReviewRequest,
    ProductReview,
)
from pb.product_reviews_pb2_grpc import ProductReviewsStub


@pytest.fixture(scope="module")
def grpc_channel():
    # Channel pointing to local product-reviews service (standard demo port)
    channel = grpc.insecure_channel("localhost:8082")
    yield channel
    channel.close()


@pytest.fixture(scope="module")
def product_reviews_stub(grpc_channel):
    return ProductReviewsStub(channel)


class TestProductReviewsValidation:
    def test_ac1_empty_product_id_get_reviews(self, product_reviews_stub):
        """AC-1: Empty product_id in GetProductReviews returns INVALID_ARGUMENT"""
        request = GetProductReviewsRequest(product_id="", limit=20, offset=0)
        with pytest.raises(grpc.RpcError) as excinfo:
            product_reviews_stub.GetProductReviews(request)
        assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "product_id" in excinfo.value.details().lower()

    def test_ac1_empty_product_id_submit_review(self, product_reviews_stub):
        """AC-1: Empty product_id in SubmitProductReview returns INVALID_ARGUMENT"""
        review = ProductReview(rating=5, text="Great product")
        request = SubmitProductReviewRequest(product_id="", review=review)
        with pytest.raises(grpc.RpcError) as excinfo:
            product_reviews_stub.SubmitProductReview(request)
        assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "product_id" in excinfo.value.details().lower()

    def test_ac2_invalid_product_id_format_get_reviews(self, product_reviews_stub):
        """AC-2: Non-matching product_id format in GetProductReviews returns INVALID_ARGUMENT"""
        test_cases = [
            "invalid123",
            "OL123",
            "abc123DEF",
            "ol12345678",  # lowercase OL
            "OL12345G",    # 7 chars
            "OL123456789", # 9 chars
            "OL123456G",   # non-hex character
        ]
        for invalid_id in test_cases:
            request = GetProductReviewsRequest(product_id=invalid_id, limit=20, offset=0)
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.GetProductReviews(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "product_id" in excinfo.value.details().lower()

    def test_ac2_invalid_product_id_format_submit_review(self, product_reviews_stub):
        """AC-2: Non-matching product_id format in SubmitProductReview returns INVALID_ARGUMENT"""
        test_cases = [
            "invalid123",
            "OL123",
            "abc123DEF",
            "ol12345678",  # lowercase OL
            "OL12345G",    # 7 chars
            "OL123456789", # 9 chars
            "OL123456G",   # non-hex character
        ]
        review = ProductReview(rating=5, text="Great product")
        for invalid_id in test_cases:
            request = SubmitProductReviewRequest(product_id=invalid_id, review=review)
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.SubmitProductReview(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "product_id" in excinfo.value.details().lower()

    def test_ac3_invalid_limit_value(self, product_reviews_stub):
        """AC-3: Limit outside 1-100 range returns INVALID_ARGUMENT"""
        valid_product_id = "OL12345678"
        test_cases = [0, -1, 101, 200, 1000]
        for invalid_limit in test_cases:
            request = GetProductReviewsRequest(
                product_id=valid_product_id,
                limit=invalid_limit,
                offset=0
            )
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.GetProductReviews(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "limit" in excinfo.value.details().lower()

    def test_ac4_invalid_offset_value(self, product_reviews_stub):
        """AC-4: Offset <0 returns INVALID_ARGUMENT"""
        valid_product_id = "OL12345678"
        test_cases = [-1, -10, -100]
        for invalid_offset in test_cases:
            request = GetProductReviewsRequest(
                product_id=valid_product_id,
                limit=20,
                offset=invalid_offset
            )
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.GetProductReviews(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "offset" in excinfo.value.details().lower()

    def test_ac5_valid_request_get_reviews(self, product_reviews_stub):
        """AC-5: Valid GetProductReviews request proceeds normally"""
        valid_product_id = "OL12345678"
        request = GetProductReviewsRequest(product_id=valid_product_id, limit=50, offset=20)
        # Should not raise validation error (may return empty or data, no INVALID_ARGUMENT)
        try:
            response = product_reviews_stub.GetProductReviews(request)
            assert response is not None
        except grpc.RpcError as e:
            assert e.code() != grpc.StatusCode.INVALID_ARGUMENT

    def test_ac5_valid_request_submit_review(self, product_reviews_stub):
        """AC-5: Valid SubmitProductReview request proceeds normally"""
        valid_product_id = "OL12345678"
        review = ProductReview(rating=5, text="Great product", user_id="test-user-1")
        request = SubmitProductReviewRequest(product_id=valid_product_id, review=review)
        # Should not raise validation error
        try:
            response = product_reviews_stub.SubmitProductReview(request)
            assert response is not None
        except grpc.RpcError as e:
            assert e.code() != grpc.StatusCode.INVALID_ARGUMENT

    def test_ac6_no_unhandled_exceptions(self, product_reviews_stub):
        """AC-6: All invalid inputs return explicit INVALID_ARGUMENT, no unhandled exceptions"""
        # Test all edge cases ensure no 500/UNKNOWN errors
        invalid_requests = [
            GetProductReviewsRequest(product_id="", limit=-1, offset=-10),
            GetProductReviewsRequest(product_id="badid", limit=200, offset=5),
            SubmitProductReviewRequest(product_id="", review=ProductReview(rating=10, text="")),
            SubmitProductReviewRequest(product_id="invalid", review=ProductReview(rating=-1, text="bad")),
        ]
        for req in invalid_requests:
            try:
                if hasattr(req, 'review'):
                    product_reviews_stub.SubmitProductReview(req)
                else:
                    product_reviews_stub.GetProductReviews(req)
                pytest.fail("Expected RpcError for invalid request")
            except grpc.RpcError as e:
                assert e.code() in [grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND]
                assert e.code() != grpc.StatusCode.UNKNOWN
                assert e.code() != grpc.StatusCode.INTERNAL
