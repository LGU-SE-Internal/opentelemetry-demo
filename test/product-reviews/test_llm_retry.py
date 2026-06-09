import os
import time
import uuid
from unittest.mock import Mock, patch
import pytest
import openai
from requests.exceptions import RequestException, Timeout, ConnectionError

# Import the decorator from the spec interface
from src.product_reviews.product_reviews_server import with_llm_retry

TEST_MAX_ATTEMPTS = 3
TEST_INITIAL_BACKOFF = 1.0
TEST_MAX_BACKOFF = 30.0

@pytest.fixture(autouse=True)
def setup_env_vars():
    """Set up test environment variables before each test."""
    original_env = os.environ.copy()
    os.environ["LLM_RETRY_MAX_ATTEMPTS"] = str(TEST_MAX_ATTEMPTS)
    os.environ["LLM_RETRY_INITIAL_BACKOFF_SEC"] = str(TEST_INITIAL_BACKOFF)
    os.environ["LLM_RETRY_MAX_BACKOFF_SEC"] = str(TEST_MAX_BACKOFF)
    yield
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def mock_logger():
    """Mock the structured logger to capture log output."""
    with patch("src.product_reviews.product_reviews_server.logger") as mock:
        yield mock

def test_ac1_retry_on_network_errors():
    """AC-1: Retry on network errors up to max attempts before raising."""
    call_count = 0
    
    @with_llm_retry
    def flaky_network_call():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("Connection reset by peer")
    
    with pytest.raises(ConnectionError):
        flaky_network_call()
    
    # Should be called 1 initial + 2 retries = 3 total for max_attempts=3
    assert call_count == TEST_MAX_ATTEMPTS

def test_ac2_retry_on_5xx_errors():
    """AC-2: Retry on 5xx status codes up to max attempts before raising."""
    call_count = 0
    
    @with_llm_retry
    def flaky_5xx_call():
        nonlocal call_count
        call_count += 1
        raise openai.InternalServerError(
            message="Server error",
            response=Mock(status_code=503),
            body=None
        )
    
    with pytest.raises(openai.InternalServerError):
        flaky_5xx_call()
    
    assert call_count == TEST_MAX_ATTEMPTS

def test_ac3_retry_after_header_429():
    """AC-3: Respect Retry-After header on 429 responses."""
    retry_after = 5
    call_count = 0
    start_time = None
    
    @with_llm_retry
    def rate_limited_call():
        nonlocal call_count, start_time
        call_count += 1
        if call_count == 1:
            start_time = time.time()
            raise openai.RateLimitError(
                message="Rate limited",
                response=Mock(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)}
                ),
                body=None
            )
        return "success"
    
    result = rate_limited_call()
    total_time = time.time() - start_time
    
    assert result == "success"
    assert call_count == 2
    # Should have waited at least retry_after seconds
    assert total_time >= retry_after * 0.95  # Allow small tolerance

def test_ac4_exponential_backoff_with_jitter(mock_logger):
    """AC-4: Exponential backoff with jitter when no Retry-After header."""
    call_count = 0
    delays = []
    
    def mock_sleep(seconds):
        delays.append(seconds)
    
    @with_llm_retry
    def flaky_call():
        nonlocal call_count
        call_count += 1
        if call_count < TEST_MAX_ATTEMPTS:
            raise openai.APIConnectionError("Network error")
        return "success"
    
    with patch("time.sleep", side_effect=mock_sleep):
        result = flaky_call()
    
    assert result == "success"
    assert call_count == TEST_MAX_ATTEMPTS
    # Should have TEST_MAX_ATTEMPTS - 1 delays
    assert len(delays) == TEST_MAX_ATTEMPTS - 1
    
    # Check exponential backoff pattern with jitter
    for i, delay in enumerate(delays):
        attempt = i + 2  # retry attempt number
        expected_base = TEST_INITIAL_BACKOFF * (2 ** (attempt - 1))
        min_expected = expected_base * 0.5
        max_expected = min(expected_base * 1.5, TEST_MAX_BACKOFF)
        assert min_expected <= delay <= max_expected

def test_ac5_structured_retry_logging(mock_logger):
    """AC-5: All retry attempts produce structured logs matching schema."""
    call_count = 0
    
    @with_llm_retry
    def flaky_call():
        nonlocal call_count
        call_count += 1
        if call_count < TEST_MAX_ATTEMPTS:
            raise openai.RateLimitError(
                message="Rate limited",
                response=Mock(status_code=429, headers={"Retry-After": "2"}),
                body=None
            )
        return "success"
    
    result = flaky_call()
    
    # Check we have TEST_MAX_ATTEMPTS - 1 retry logs
    assert mock_logger.info.call_count == TEST_MAX_ATTEMPTS - 1
    
    for attempt in range(1, TEST_MAX_ATTEMPTS):
        log_call = mock_logger.info.call_args_list[attempt - 1]
        log_data = log_call[0][0] if isinstance(log_call[0][0], dict) else log_call.kwargs.get("extra", {})
        
        # Verify all required fields exist
        assert log_data.get("event") == "llm_api_retry"
        assert log_data.get("attempt_number") == attempt + 1
        assert log_data.get("max_attempts") == TEST_MAX_ATTEMPTS
        assert isinstance(log_data.get("backoff_delay_sec"), (int, float))
        assert log_data.get("error_type") == "RateLimitError"
        assert "Rate limited" in log_data.get("error_message", "")
        assert log_data.get("retry_after_header") == 2

def test_ac6_retry_parameters_from_env():
    """AC-6: Retry parameters are read from environment variables with defaults."""
    # Override env vars for this test
    custom_max_attempts = 5
    custom_initial_backoff = 0.5
    custom_max_backoff = 10.0
    os.environ["LLM_RETRY_MAX_ATTEMPTS"] = str(custom_max_attempts)
    os.environ["LLM_RETRY_INITIAL_BACKOFF_SEC"] = str(custom_initial_backoff)
    os.environ["LLM_RETRY_MAX_BACKOFF_SEC"] = str(custom_max_backoff)
    
    call_count = 0
    
    @with_llm_retry
    def flaky_call():
        nonlocal call_count
        call_count += 1
        raise openai.APIError("Server error")
    
    with pytest.raises(openai.APIError):
        flaky_call()
    
    assert call_count == custom_max_attempts

def test_ac7_idempotency_key_reused_across_retries():
    """AC-7: Same idempotency key used for all retries of the same request."""
    used_keys = set()
    call_count = 0
    
    def mock_openai_create(**kwargs):
        nonlocal call_count
        call_count += 1
        used_keys.add(kwargs.get("headers", {}).get("X-Idempotency-Key"))
        if call_count < 3:
            raise openai.APIConnectionError("Network error")
        return "success"
    
    @with_llm_retry
    def llm_call_with_idempotency():
        return openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "test"}]
        )
    
    with patch("openai.ChatCompletion.create", side_effect=mock_openai_create):
        result = llm_call_with_idempotency()
    
    assert result == "success"
    assert call_count == 3
    # All retries should use the same idempotency key
    assert len(used_keys) == 1
    # Key should be a valid UUID v4
    key = next(iter(used_keys))
    assert uuid.UUID(key, version=4) is not None

def test_ac8_original_error_raised_after_max_retries():
    """AC-8: Original error is raised unchanged after all retries are exhausted."""
    original_error = ConnectionError("Custom connection failure message")
    call_count = 0
    
    @with_llm_retry
    def failing_call():
        nonlocal call_count
        call_count += 1
        raise original_error
    
    with pytest.raises(ConnectionError) as exc_info:
        failing_call()
    
    assert exc_info.value is original_error
    assert str(exc_info.value) == "Custom connection failure message"
    assert call_count == TEST_MAX_ATTEMPTS
