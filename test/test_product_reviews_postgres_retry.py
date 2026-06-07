import pytest
from unittest.mock import Mock, patch
from psycopg2 import OperationalError
from psycopg2 import errors
from tenacity import RetryError
import logging
import json
from src.product_reviews.database import postgres_retry  # Import from spec definition

# Mock database operations
@postgres_retry(operation_type="read")
def mock_read_operation(should_fail: int = 0, error_type: Exception = None):
    if should_fail > 0:
        should_fail -= 1
        raise error_type if error_type else OperationalError("Connection failed")
    return {"success": True, "data": "test_data"}

@postgres_retry(operation_type="write")
def mock_write_operation(should_fail: int = 0, error_type: Exception = None, idempotency_key: str = "test_key"):
    if should_fail > 0:
        should_fail -= 1
        raise error_type if error_type else OperationalError("Connection failed")
    return {"success": True, "review_id": "test_review_id"}

# Test AC-1: Read operations retry transient errors up to 3 attempts with exponential backoff
def test_ac1_read_retry_transient_error():
    # Test that read operation retries 2 times (total 3 attempts)
    with patch("time.sleep") as mock_sleep:
        result = mock_read_operation(should_fail=2, error_type=OperationalError("Connection drop"))
        assert result["success"] == True
        # Check sleep delays: 100ms, 200ms (with jitter, approximate values)
        assert mock_sleep.call_count == 2
        # Check approximate delay values (allowing for jitter)
        assert 0.08 <= mock_sleep.call_args_list[0][0][0] <= 0.12
        assert 0.18 <= mock_sleep.call_args_list[1][0][0] <= 0.22

    # Test that read operation fails after 3 attempts
    with pytest.raises(OperationalError):
        mock_read_operation(should_fail=3, error_type=OperationalError("Connection drop"))

# Test AC-2: Write operations retry transient errors when idempotent
def test_ac2_write_retry_idempotent_transient_error():
    with patch("time.sleep") as mock_sleep:
        result = mock_write_operation(should_fail=2, error_type=errors.DeadlockDetected("Deadlock detected"))
        assert result["success"] == True
        assert mock_sleep.call_count == 2

    # Test idempotency: same idempotency key returns same result even after retry
    with patch("src.product_reviews.database.check_existing_idempotency_key", return_value=True):
        result1 = mock_write_operation(should_fail=1, idempotency_key="test_key_123")
        result2 = mock_write_operation(should_fail=0, idempotency_key="test_key_123")
        assert result1["review_id"] == result2["review_id"]

# Test AC-3: Non-transient errors are not retried
def test_ac3_non_transient_errors_not_retried():
    non_transient_errors = [
        errors.UniqueViolation("Unique constraint violation"),
        errors.SyntaxError("Invalid SQL syntax"),
        errors.InsufficientPrivilege("Permission denied"),
        errors.IntegrityConstraintViolation("Data integrity error"),
    ]

    for error in non_transient_errors:
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(type(error)):
                mock_read_operation(should_fail=1, error_type=error)
            # No retries should occur
            assert mock_sleep.call_count == 0

# Test AC-4: Retry attempts emit structured logs with all required fields
def test_ac4_retry_structured_logging(caplog):
    caplog.set_level(logging.INFO)

    mock_read_operation(should_fail=1, error_type=OperationalError("Test connection error"))

    # Find retry log entry
    retry_log = None
    for record in caplog.records:
        if hasattr(record, 'event') and record.event == "postgres_retry_attempt":
            retry_log = json.loads(record.getMessage())
            break

    assert retry_log is not None
    assert retry_log["service"] == "product-reviews"
    assert retry_log["event"] == "postgres_retry_attempt"
    assert retry_log["operation_type"] == "read"
    assert retry_log["operation_desc"] == "mock_read_operation"
    assert retry_log["attempt_number"] == 1
    assert retry_log["max_attempts"] == 3
    assert retry_log["error_type"] == "OperationalError"
    assert "Test connection error" in retry_log["error_message"]
    assert "retry_delay_ms" in retry_log
    assert isinstance(retry_log["retry_delay_ms"], int)
    assert 80 <= retry_log["retry_delay_ms"] <= 120

# Test AC-5: Idempotency prevents duplicate reviews with same idempotency key
def test_ac5_idempotency_prevents_duplicate_reviews():
    with patch("src.product_reviews.database.insert_product_review") as mock_insert:
        mock_insert.return_value = {"review_id": "test_123"}
        # First call, fails once then succeeds
        result1 = mock_write_operation(should_fail=1, idempotency_key="key_abc123")
        # Second call with same key, should not insert new record
        result2 = mock_write_operation(should_fail=0, idempotency_key="key_abc123")

        assert result1["review_id"] == "test_123"
        assert result2["review_id"] == "test_123"
        # Insert should only be called once
        assert mock_insert.call_count == 1

# Test AC-6: Max attempts exhausted propagates original error
def test_ac6_max_attempts_exhausted_propagates_error():
    original_error = OperationalError("Permanent connection failure")
    with pytest.raises(OperationalError) as exc_info:
        mock_read_operation(should_fail=3, error_type=original_error)
    
    assert exc_info.value == original_error
    assert "Permanent connection failure" in str(exc_info.value)

# Test AC-7: Deadlock errors are retried for read and write operations
def test_ac7_deadlock_errors_retried():
    # Test read operation deadlock retry
    with patch("time.sleep") as mock_sleep:
        result = mock_read_operation(should_fail=1, error_type=errors.DeadlockDetected("Deadlock"))
        assert result["success"] == True
        assert mock_sleep.call_count == 1

    # Test write operation deadlock retry
    with patch("time.sleep") as mock_sleep:
        result = mock_write_operation(should_fail=1, error_type=errors.DeadlockDetected("Deadlock"))
        assert result["success"] == True
        assert mock_sleep.call_count == 1
