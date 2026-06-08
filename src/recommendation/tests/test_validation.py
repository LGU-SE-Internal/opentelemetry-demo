#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for request validation in recommendation service."""

import unittest
import uuid
import grpc
from unittest.mock import Mock, MagicMock

from recommendation_server import RecommendationServiceServicer
import demo_pb2


class TestRequestValidation(unittest.TestCase):
    """Test validation of ListRecommendations request parameters."""

    def setUp(self):
        """Set up test fixtures."""
        self.servicer = RecommendationServiceServicer()
        self.context = Mock()
        self.context.abort = MagicMock()

        # Mock product catalog client to avoid actual network calls
        self.servicer.product_catalog_client = Mock()
        mock_response = Mock()
        mock_response.products = []
        self.servicer.product_catalog_client.ListProducts = Mock(return_value=mock_response)

    def test_valid_request_no_user_id(self):
        """Test valid request with no user_id passes validation."""
        request = demo_pb2.ListRecommendationsRequest(
            product_ids=["abc123", "DEF456", "789xyz"],
            max_results=10
        )
        self.servicer.ListRecommendations(request, self.context)
        self.context.abort.assert_not_called()

    def test_valid_request_with_valid_user_id(self):
        """Test valid request with valid UUID v4 user_id passes validation."""
        valid_user_id = str(uuid.uuid4())
        request = demo_pb2.ListRecommendationsRequest(
            user_id=valid_user_id,
            product_ids=["abc123", "DEF456", "789xyz"],
            max_results=10
        )
        self.servicer.ListRecommendations(request, self.context)
        self.context.abort.assert_not_called()

    def test_invalid_user_id_format(self):
        """AC-3: Test non-UUIDv4 user_id returns INVALID_ARGUMENT."""
        invalid_user_ids = [
            "invalid-uuid",
            "123e4567-e89b-12d3-a456-42661417400",  # too short
            "123e4567-e89b-12d3-a456-4266141740000",  # too long
            "123e4567-e89b-12d3-a456-42661417400g",  # invalid character g
            "not-a-uuid-at-all",
        ]

        for user_id in invalid_user_ids:
            with self.subTest(user_id=user_id):
                self.context.abort.reset_mock()
                request = demo_pb2.ListRecommendationsRequest(
                    user_id=user_id,
                    product_ids=["abc123"],
                    max_results=10
                )
                self.servicer.ListRecommendations(request, self.context)
                self.context.abort.assert_called_once_with(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "user_id has invalid format: must be UUID v4"
                )

    def test_product_id_non_alphanumeric(self):
        """AC-1: Test product ID with non-alphanumeric characters returns INVALID_ARGUMENT."""
        invalid_product_ids = [
            "abc-123",  # hyphen
            "abc 123",  # space
            "abc_123",  # underscore
            "abc@123",  # @ symbol
            "abc#123",  # hash
            "abc/123",  # slash
            "abc.123",  # dot
        ]

        for product_id in invalid_product_ids:
            with self.subTest(product_id=product_id):
                self.context.abort.reset_mock()
                request = demo_pb2.ListRecommendationsRequest(
                    product_ids=["abc123", product_id, "def456"],
                    max_results=10
                )
                self.servicer.ListRecommendations(request, self.context)
                self.context.abort.assert_called_once()
                self.assertEqual(self.context.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)
                self.assertIn("invalid format, must be alphanumeric only", self.context.abort.call_args[0][1])

    def test_too_many_product_ids(self):
        """AC-2: Test more than 100 product IDs returns INVALID_ARGUMENT."""
        product_ids = [f"pid{i}" for i in range(101)]
        request = demo_pb2.ListRecommendationsRequest(
            product_ids=product_ids,
            max_results=10
        )
        self.servicer.ListRecommendations(request, self.context)
        self.context.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            "product_ids list exceeds maximum allowed size of 100"
        )

    def test_max_results_lower_bound(self):
        """AC-4: Test max_results < 1 returns INVALID_ARGUMENT."""
        invalid_values = [0, -1, -10, -100]
        for value in invalid_values:
            with self.subTest(value=value):
                self.context.abort.reset_mock()
                request = demo_pb2.ListRecommendationsRequest(
                    product_ids=["abc123"],
                    max_results=value
                )
                self.servicer.ListRecommendations(request, self.context)
                self.context.abort.assert_called_once_with(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "max_results must be between 1 and 20 (inclusive)"
                )

    def test_max_results_upper_bound(self):
        """AC-4: Test max_results > 20 returns INVALID_ARGUMENT."""
        invalid_values = [21, 100, 200, 1000]
        for value in invalid_values:
            with self.subTest(value=value):
                self.context.abort.reset_mock()
                request = demo_pb2.ListRecommendationsRequest(
                    product_ids=["abc123"],
                    max_results=value
                )
                self.servicer.ListRecommendations(request, self.context)
                self.context.abort.assert_called_once_with(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "max_results must be between 1 and 20 (inclusive)"
                )

    def test_max_results_valid_values(self):
        """Test valid max_results values pass validation."""
        valid_values = [1, 5, 10, 15, 20]
        for value in valid_values:
            with self.subTest(value=value):
                self.context.abort.reset_mock()
                request = demo_pb2.ListRecommendationsRequest(
                    product_ids=["abc123"],
                    max_results=value
                )
                self.servicer.ListRecommendations(request, self.context)
                self.context.abort.assert_not_called()


if __name__ == "__main__":
    unittest.main()
