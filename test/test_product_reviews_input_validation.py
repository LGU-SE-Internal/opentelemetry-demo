import grpc
import pytest
import uuid
from demo_pb2 import (
    ListProductReviewsRequest,
    CreateProductReviewRequest,
)
from demo_pb2_grpc import ProductReviewsStub


@pytest.fixture(scope="module")
def grpc_channel():
    # Channel pointing to local product-reviews service (standard demo port)
    channel = grpc.insecure_channel("localhost:8082")
    yield channel
    channel.close()


@pytest.fixture(scope="module")
def product_reviews_stub(grpc_channel):
    return ProductReviewsStub(channel)


class TestProductReviewsInputValidation:
    def test_ac1_empty_product_id_list_reviews(self, product_reviews_stub):
        """AC-1: Empty product_id in ListProductReviews returns INVALID_ARGUMENT"""
        request = ListProductReviewsRequest(product_id="")
        with pytest.raises(grpc.RpcError) as excinfo:
            product_reviews_stub.ListProductReviews(request)
        assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "product_id is required" in excinfo.value.details()

    def test_ac1_empty_product_id_create_review(self, product_reviews_stub):
        """AC-1: Empty product_id in CreateProductReview returns INVALID_ARGUMENT"""
        request = CreateProductReviewRequest(
            product_id="",
            rating=5,
            review_text="Great product"
        )
        with pytest.raises(grpc.RpcError) as excinfo:
            product_reviews_stub.CreateProductReview(request)
        assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "product_id is required" in excinfo.value.details()

    def test_ac2_invalid_uuid_product_id_list_reviews(self, product_reviews_stub):
        """AC-2: Non-UUID product_id in ListProductReviews returns INVALID_ARGUMENT"""
        invalid_ids = [
            "invalid-uuid",
            "123e4567-e89b-12d3-a456-42661417400",  # Too short
            "123e4567-e89b-12d3-a456-4266141740000", # Too long
            "g23e4567-e89b-12d3-a456-426614174000",  # Invalid character 'g'
            "123e4567-e89b-12d3-a456-42661417400z",  # Invalid character 'z'
            "OL12345678",  # Old format, not UUID
        ]
        for invalid_id in invalid_ids:
            request = ListProductReviewsRequest(product_id=invalid_id)
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.ListProductReviews(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "product_id is not a valid UUID" in excinfo.value.details()

    def test_ac2_invalid_uuid_product_id_create_review(self, product_reviews_stub):
        """AC-2: Non-UUID product_id in CreateProductReview returns INVALID_ARGUMENT"""
        invalid_ids = [
            "invalid-uuid",
            "123e4567-e89b-12d3-a456-42661417400",  # Too short
            "123e4567-e89b-12d3-a456-4266141740000", # Too long
            "g23e4567-e89b-12d3-a456-426614174000",  # Invalid character 'g'
            "123e4567-e89b-12d3-a456-42661417400z",  # Invalid character 'z'
            "OL12345678",  # Old format, not UUID
        ]
        for invalid_id in invalid_ids:
            request = CreateProductReviewRequest(
                product_id=invalid_id,
                rating=5,
                review_text="Great product"
            )
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.CreateProductReview(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "product_id is not a valid UUID" in excinfo.value.details()

    def test_ac3_rating_less_than_1(self, product_reviews_stub):
        """AC-3: CreateProductReview with rating < 1 returns INVALID_ARGUMENT"""
        test_cases = [0, -1, -5, -100]
        valid_product_id = str(uuid.uuid4())
        for invalid_rating in test_cases:
            request = CreateProductReviewRequest(
                product_id=valid_product_id,
                rating=invalid_rating,
                review_text="Bad product"
            )
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.CreateProductReview(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "rating must be at least 1" in excinfo.value.details()

    def test_ac4_rating_greater_than_5(self, product_reviews_stub):
        """AC-4: CreateProductReview with rating > 5 returns INVALID_ARGUMENT"""
        test_cases = [6, 10, 100, 999]
        valid_product_id = str(uuid.uuid4())
        for invalid_rating in test_cases:
            request = CreateProductReviewRequest(
                product_id=valid_product_id,
                rating=invalid_rating,
                review_text="Too good to be true"
            )
            with pytest.raises(grpc.RpcError) as excinfo:
                product_reviews_stub.CreateProductReview(request)
            assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "rating must be at most 5" in excinfo.value.details()

    def test_ac5_non_integer_rating(self, product_reviews_stub):
        """AC-5: CreateProductReview with non-integer rating returns INVALID_ARGUMENT"""
        valid_product_id = str(uuid.uuid4())
        # gRPC will reject non-integer values at client level, so we test edge cases that might pass through
        # e.g. if value is parsed incorrectly
        request = CreateProductReviewRequest(
            product_id=valid_product_id,
            rating=3.5,  # This should be coerced to int by protobuf, but test if server validates integer type
            review_text="Okay product"
        )
        with pytest.raises(grpc.RpcError) as excinfo:
            product_reviews_stub.CreateProductReview(request)
        assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "rating must be an integer" in excinfo.value.details()

    def test_ac6_review_text_exceeds_2000_chars(self, product_reviews_stub):
        """AC-6: CreateProductReview with review_text > 2000 chars returns INVALID_ARGUMENT"""
        valid_product_id = str(uuid.uuid4())
        long_text = "a" * 2001
        request = CreateProductReviewRequest(
            product_id=valid_product_id,
            rating=5,
            review_text=long_text
        )
        with pytest.raises(grpc.RpcError) as excinfo:
            product_reviews_stub.CreateProductReview(request)
        assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "review text exceeds 2000 character limit" in excinfo.value.details()

    def test_ac7_valid_list_request(self, product_reviews_stub):
        """AC-7: Valid ListProductReviews request processes normally"""
        valid_product_id = str(uuid.uuid4())
        request = ListProductReviewsRequest(product_id=valid_product_id)
        try:
            response = product_reviews_stub.ListProductReviews(request)
            assert response is not None
        except grpc.RpcError as e:
            # Should not return invalid argument, may return not found or other valid error
            assert e.code() != grpc.StatusCode.INVALID_ARGUMENT

    def test_ac7_valid_create_request(self, product_reviews_stub):
        """AC-7: Valid CreateProductReview request processes normally"""
        valid_product_id = str(uuid.uuid4())
        valid_review_text = "a" * 2000
        request = CreateProductReviewRequest(
            product_id=valid_product_id,
            rating=3,
            review_text=valid_review_text
        )
        try:
            response = product_reviews_stub.CreateProductReview(request)
            assert response is not None
        except grpc.RpcError as e:
            # Should not return invalid argument, may return other valid errors
            assert e.code() != grpc.StatusCode.INVALID_ARGUMENT
