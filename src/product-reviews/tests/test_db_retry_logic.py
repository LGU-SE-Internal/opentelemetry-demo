import pytest
import logging
from unittest.mock import patch, Mock
from psycopg2 import OperationalError, IntegrityError, ProgrammingError, DataError
from psycopg2.errors import DeadlockDetected, TransactionRollbackError, TooManyConnections, LockNotAvailable

# Import the expected functions from the spec
try:
    from database import retry_postgres_transient, get_product_reviews, add_product_review, delete_product_review, list_reviews
except ImportError:
    # For test runs before implementation exists, define mock stubs to let import work
    retry_postgres_transient = lambda *args, **kwargs: lambda f: f
    def get_product_reviews(*args, **kwargs): pass
    def add_product_review(*args, **kwargs): pass
    def delete_product_review(*args, **kwargs): pass
    def list_reviews(*args, **kwargs): pass


def test_ac1_retry_transient_errors_up_to_3_times_succeeds_on_4th():
    """AC-1: Retry transient errors up to 3 times, success on 4th attempt"""
    # Mock db operation that fails first 3 times with OperationalError
    call_count = 0

    @retry_postgres_transient(max_retries=3, initial_backoff=0.1)
    def mock_db_operation():
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise OperationalError("Connection timeout")
        return "success"

    result = mock_db_operation()
    assert result == "success"
    assert call_count == 4


def test_ac2_non_retryable_errors_fail_immediately():
    """AC-2: Permanent non-retryable errors are not retried"""
    call_count = 0

    @retry_postgres_transient(max_retries=3)
    def mock_db_operation():
        nonlocal call_count
        call_count += 1
        raise IntegrityError("Duplicate key constraint violation")

    with pytest.raises(IntegrityError):
        mock_db_operation()

    assert call_count == 1


def test_ac3_exceed_max_retries_raises_original_error():
    """AC-3: Transient errors exceeding max retries fail after final attempt"""
    call_count = 0

    @retry_postgres_transient(max_retries=3)
    def mock_db_operation():
        nonlocal call_count
        call_count += 1
        raise DeadlockDetected("Deadlock found")

    with pytest.raises(DeadlockDetected):
        mock_db_operation()

    assert call_count == 4  # 1 initial + 3 retries


def test_ac4_retry_attempts_logged_with_correct_context(caplog):
    """AC-4: Retry attempts are logged with required fields at WARNING level"""
    caplog.set_level(logging.WARNING)
    call_count = 0

    @retry_postgres_transient(max_retries=3, initial_backoff=0.1)
    def test_operation():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TransactionRollbackError("Transaction rolled back")
        return "success"

    result = test_operation()
    assert result == "success"
    assert call_count == 2

    # Verify log entry exists
    assert len(caplog.records) == 1
    log_record = caplog.records[0]
    assert log_record.levelname == "WARNING"
    assert log_record.error_type == "TransactionRollbackError"
    assert hasattr(log_record, "error_code")
    assert log_record.retry_count == 1
    assert log_record.max_retries == 3
    assert log_record.operation == "test_operation"
    assert hasattr(log_record, "next_backoff")
    assert log_record.next_backoff > 0


def test_ac5_no_errors_no_retries_no_logs(caplog):
    """AC-5: No errors = no retries, no retry logs"""
    caplog.set_level(logging.WARNING)
    call_count = 0

    @retry_postgres_transient(max_retries=3)
    def mock_db_operation():
        nonlocal call_count
        call_count += 1
        return "expected_result"

    result = mock_db_operation()
    assert result == "expected_result"
    assert call_count == 1
    assert len([r for r in caplog.records if "retry" in r.message.lower()]) == 0


def test_ac6_exponential_backoff_with_jitter():
    """AC-6: Backoff intervals are exponential with ±50% jitter"""
    initial_backoff = 1.0
    expected_base_intervals = [
        initial_backoff * (2 ** 1),  # retry 1: 2s base
        initial_backoff * (2 ** 2),  # retry 2: 4s base
        initial_backoff * (2 ** 3),  # retry 3: 8s base
    ]

    captured_backoffs = []

    def mock_sleep(seconds):
        captured_backoffs.append(seconds)

    call_count = 0

    @retry_postgres_transient(max_retries=3, initial_backoff=initial_backoff)
    def mock_db_operation():
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise TooManyConnections("Too many connections")
        return "success"

    with patch("time.sleep", side_effect=mock_sleep):
        mock_db_operation()

    # Verify we have 3 backoff captures
    assert len(captured_backoffs) == 3
    for i, (actual, base) in enumerate(zip(captured_backoffs, expected_base_intervals)):
        min_expected = base * 0.5
        max_expected = base * 1.5
        assert min_expected <= actual <= max_expected, f"Retry {i+1} backoff {actual} not in [{min_expected}, {max_expected}]"

    # Verify exponential increase trend (allow minor variations from jitter)
    assert captured_backoffs[0] < captured_backoffs[1] * 1.5
    assert captured_backoffs[1] < captured_backoffs[2] * 1.5


def test_all_db_functions_have_retry_decorator():
    """Verify all public database functions are wrapped with retry decorator"""
    # Check that all expected functions have the retry attributes from tenacity
    for func in [get_product_reviews, add_product_review, delete_product_review, list_reviews]:
        assert hasattr(func, "retry"), f"Function {func.__name__} does not have retry decorator applied"
