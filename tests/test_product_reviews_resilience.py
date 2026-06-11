import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src/product-reviews'))

import time
from unittest.mock import patch, MagicMock
import pytest
import psycopg2
import pybreaker
from opentelemetry.metrics import get_meter

# Import functions from spec (will fail until implementation exists)
from database import (
    fetch_product_reviews,
    fetch_average_review_score,
    create_review,
)

TEST_PRODUCT_ID = "test-product-123"
TEST_USER_ID = "test-user-456"
TRANSIENT_ERRORS = [
    psycopg2.OperationalError("Connection failed"),
    psycopg2.ConnectionError("Connection refused"),
    TimeoutError("Socket timed out"),
]
NON_TRANSIENT_ERRORS = [
    psycopg2.IntegrityError("Duplicate key"),
    psycopg2.ProgrammingError("Invalid query syntax"),
    ValueError("Validation failed"),
]


def test_ac1_read_operations_retry_on_transient_errors():
    """Test AC1: read operations retry up to 3 times on transient errors with exponential backoff"""
    # Test for fetch_product_reviews
    with patch("src.product_reviews.db.execute_query") as mock_query:
        # First 3 calls fail with transient error, 4th succeeds
        mock_query.side_effect = [
            psycopg2.OperationalError("Connection failed"),
            psycopg2.OperationalError("Connection failed"),
            psycopg2.OperationalError("Connection failed"),
            [{"id": 1, "rating": 5, "comment": "Great"}],
        ]
        start_time = time.time()
        result = fetch_product_reviews(TEST_PRODUCT_ID)
        total_time = time.time() - start_time

        # Should have retried 3 times (4 calls total)
        assert mock_query.call_count == 4
        # Should return successful result
        assert len(result) == 1
        assert result[0]["rating"] == 5
        # Exponential backoff: 100ms + 200ms + 400ms = ~700ms total delay
        assert 0.6 < total_time < 1.0  # Allow small buffer for execution overhead

    # Test for fetch_average_review_score
    with patch("src.product_reviews.db.execute_query") as mock_query:
        mock_query.side_effect = [
            TimeoutError("Socket timed out"),
            TimeoutError("Socket timed out"),
            TimeoutError("Socket timed out"),
            TimeoutError("Socket timed out"),  # All 4 calls fail
        ]

        with pytest.raises(TimeoutError):
            fetch_average_review_score(TEST_PRODUCT_ID)
        # Only 4 attempts (initial + 3 retries)
        assert mock_query.call_count == 4


def test_ac2_non_transient_errors_not_retried():
    """Test AC2: non-transient errors are not retried, fail immediately"""
    for error in NON_TRANSIENT_ERRORS:
        with patch("src.product_reviews.db.execute_query") as mock_query:
            mock_query.side_effect = error

            # Test fetch_product_reviews
            with pytest.raises(type(error)):
                fetch_product_reviews(TEST_PRODUCT_ID)
            assert mock_query.call_count == 1  # No retries

            # Test fetch_average_review_score
            mock_query.reset_mock()
            mock_query.side_effect = error
            with pytest.raises(type(error)):
                fetch_average_review_score(TEST_PRODUCT_ID)
            assert mock_query.call_count == 1  # No retries

            # Test create_review
            mock_query.reset_mock()
            mock_query.side_effect = error
            with pytest.raises(type(error)):
                create_review(TEST_PRODUCT_ID, TEST_USER_ID, 4, "Good")
            assert mock_query.call_count == 1  # No retries


def test_ac3_retry_metrics_incremented_correctly():
    """Test AC3: retry count metric is incremented with correct labels on each retry"""
    meter = get_meter("product-reviews")
    mock_counter = MagicMock()
    with patch.object(meter, "create_counter", return_value=mock_counter):
        # Re-import to get the mocked counter
        from importlib import reload
        import src.product_reviews.db
        reload(src.product_reviews.db)
        from src.product_reviews.db import fetch_product_reviews

        with patch("src.product_reviews.db.execute_query") as mock_query:
            mock_query.side_effect = [
                psycopg2.OperationalError("Connection failed"),
                psycopg2.ConnectionError("Connection refused"),
                [{"id": 1, "rating": 5}],
            ]

            result = fetch_product_reviews(TEST_PRODUCT_ID)
            assert mock_query.call_count == 3

            # Should have 2 retry events (attempt 1 and 2)
            assert mock_counter.add.call_count == 2
            # First retry: attempt 1, error OperationalError
            mock_counter.add.assert_any_call(1, {
                "operation_name": "fetch_product_reviews",
                "error_type": "OperationalError",
                "retry_attempt": 1,
            })
            # Second retry: attempt 2, error ConnectionError
            mock_counter.add.assert_any_call(1, {
                "operation_name": "fetch_product_reviews",
                "error_type": "ConnectionError",
                "retry_attempt": 2,
            })


def test_ac4_create_review_circuit_breaker_config():
    """Test AC4: create_review has correct circuit breaker configuration"""
    # Access the circuit breaker instance on the create_review function
    assert hasattr(create_review, "__wrapped__")
    assert hasattr(create_review, "_breaker")
    breaker = create_review._breaker

    # Verify config: failure threshold 5 in 10s, open duration 30s, 1 half-open request
    assert isinstance(breaker, pybreaker.CircuitBreaker)
    assert breaker.fail_max == 5
    assert breaker.reset_timeout == 30
    assert breaker.expected_exception == Exception
    assert breaker.state == pybreaker.STATE_CLOSED


def test_ac5_circuit_open_returns_503_fallback():
    """Test AC5: open circuit breaker returns 503 response with correct error payload"""
    # Force circuit to open state
    breaker = create_review._breaker
    breaker.open()

    with patch("src.product_reviews.db.execute_query") as mock_query:
        response = create_review(TEST_PRODUCT_ID, TEST_USER_ID, 3, "Okay")
        # Should not have called database at all
        assert mock_query.call_count == 0
        # Should return 503 response
        assert response.status_code == 503
        assert response.json == {"error": "Product review service is temporarily unavailable, please try again later"}

    # Reset circuit
    breaker.close()


def test_ac6_circuit_breaker_state_metrics_emitted():
    """Test AC6: circuit breaker state transitions emit correct metrics"""
    meter = get_meter("product-reviews")
    mock_counter = MagicMock()
    with patch.object(meter, "create_counter", return_value=mock_counter):
        from importlib import reload
        import src.product_reviews.db
        reload(src.product_reviews.db)
        from src.product_reviews.db import create_review

        breaker = create_review._breaker
        # Transition from closed to open
        breaker.open()
        mock_counter.add.assert_called_with(1, {
            "operation_name": "create_review",
            "state": "open",
        })
        # Transition from open to half-open
        mock_counter.reset_mock()
        breaker.half_open()
        mock_counter.add.assert_called_with(1, {
            "operation_name": "create_review",
            "state": "half_open",
        })
        # Transition from half-open to closed
        mock_counter.reset_mock()
        breaker.close()
        mock_counter.add.assert_called_with(1, {
            "operation_name": "create_review",
            "state": "closed",
        })


def test_ac7_successful_operations_return_original_payload():
    """Test AC7: successful operations return same payload as original implementation, no breaking changes"""
    expected_reviews = [
        {"id": 1, "user_id": "user1", "rating": 5, "comment": "Excellent product", "created_at": "2024-01-01T00:00:00Z"},
        {"id": 2, "user_id": "user2", "rating": 4, "comment": "Good value", "created_at": "2024-01-02T00:00:00Z"},
    ]
    with patch("src.product_reviews.db.execute_query", return_value=expected_reviews):
        result = fetch_product_reviews(TEST_PRODUCT_ID, page=1, limit=20)
        assert result == expected_reviews

    expected_average = 4.5
    with patch("src.product_reviews.db.execute_query", return_value=expected_average):
        result = fetch_average_review_score(TEST_PRODUCT_ID)
        assert result == expected_average

    expected_create_response = {
        "id": 3,
        "product_id": TEST_PRODUCT_ID,
        "user_id": TEST_USER_ID,
        "rating": 4,
        "comment": "Works well",
        "created_at": "2024-01-03T00:00:00Z",
    }
    with patch("src.product_reviews.db.execute_query", return_value=expected_create_response):
        result = create_review(TEST_PRODUCT_ID, TEST_USER_ID, 4, "Works well")
        assert result == expected_create_response
