import pytest
import logging
from unittest.mock import patch, Mock
from src.product_reviews.database import get_product_reviews, get_review_summary
import psycopg2

# Mock transient Postgres errors
TRANSIENT_ERRORS = [
    psycopg2.OperationalError("Connection refused"),
    psycopg2.errors.DeadlockDetected("deadlock detected"),
    psycopg2.errors.SerializationFailure("could not serialize access"),
    psycopg2.errors.TransactionRollbackError("transaction was aborted"),
]

# Mock non-transient Postgres errors
NON_TRANSIENT_ERRORS = [
    psycopg2.errors.SyntaxError("syntax error at or near"),
    psycopg2.errors.UniqueViolation("duplicate key value violates unique constraint"),
    psycopg2.errors.UndefinedTable("relation does not exist"),
    psycopg2.errors.InvalidTextRepresentation("invalid input syntax for type uuid"),
]

def test_ac1_transient_connection_error_get_product_reviews_retries_3_times():
    """AC-1: get_product_reviews retries up to 3 times on transient connection errors"""
    with patch('src.product_reviews.database.execute_query') as mock_exec:
        mock_exec.side_effect = psycopg2.OperationalError("Connection blip")
        
        with pytest.raises(psycopg2.OperationalError):
            get_product_reviews("test-product-id", 10, 0)
        
        # Should be called 4 times: 1 initial + 3 retries = 4 attempts
        assert mock_exec.call_count == 4

def test_ac2_transient_deadlock_get_review_summary_retries_3_times():
    """AC-2: get_review_summary retries up to 3 times on transient deadlock errors"""
    with patch('src.product_reviews.database.execute_query') as mock_exec:
        mock_exec.side_effect = psycopg2.errors.DeadlockDetected("deadlock detected")
        
        with pytest.raises(psycopg2.errors.DeadlockDetected):
            get_review_summary("test-product-id")
        
        # Should be called 4 times: 1 initial + 3 retries = 4 attempts
        assert mock_exec.call_count == 4

def test_ac3_retry_attempts_log_correct_details(caplog):
    """AC-3: Retry attempts log operation type, function name, attempt number, and error details"""
    caplog.set_level(logging.INFO)
    
    with patch('src.product_reviews.database.execute_query') as mock_exec:
        mock_exec.side_effect = psycopg2.OperationalError("Connection error")
        
        try:
            get_product_reviews("test-product-id")
        except psycopg2.OperationalError:
            pass
        
        # Check logs for each retry attempt
        retry_logs = [record for record in caplog.records if "retry" in record.message.lower()]
        
        assert len(retry_logs) == 3  # 3 retry attempts
        
        for i, log in enumerate(retry_logs):
            assert "read" in log.message
            assert "get_product_reviews" in log.message
            assert str(i+1) in log.message  # attempt number
            assert "Connection error" in log.message

def test_ac4_successful_retry_returns_expected_result():
    """AC-4: Operation succeeds on retry returns correct result with no error"""
    expected_result = [{"id": 1, "rating": 5, "text": "Great product"}]
    
    with patch('src.product_reviews.database.execute_query') as mock_exec:
        # Fail first 2 times, succeed on 3rd
        mock_exec.side_effect = [
            psycopg2.OperationalError("Connection error"),
            psycopg2.OperationalError("Connection error"),
            expected_result
        ]
        
        result = get_product_reviews("test-product-id")
        
        assert result == expected_result
        assert mock_exec.call_count == 3  # 2 fails + 1 success = 3 attempts total

def test_ac5_non_transient_errors_not_retried():
    """AC-5: Non-transient errors fail immediately with no retries"""
    with patch('src.product_reviews.database.execute_query') as mock_exec:
        mock_exec.side_effect = psycopg2.errors.SyntaxError("invalid query")
        
        with pytest.raises(psycopg2.errors.SyntaxError):
            get_product_reviews("test-product-id")
        
        # Should only be called once: no retries
        assert mock_exec.call_count == 1

def test_ac6_retries_have_no_side_effects_read_only():
    """AC-6: Retries for read operations do not perform any database writes"""
    with patch('src.product_reviews.database.execute_query') as mock_exec:
        mock_exec.side_effect = [
            psycopg2.OperationalError("Connection error"),
            []
        ]
        
        get_product_reviews("test-product-id")
        
        # Verify all calls to execute_query are read operations (SELECT)
        for call in mock_exec.call_args_list:
            query = call[0][0].lower()
            assert query.startswith("select")
            assert "insert" not in query
            assert "update" not in query
            assert "delete" not in query
            assert "create" not in query
            assert "drop" not in query
