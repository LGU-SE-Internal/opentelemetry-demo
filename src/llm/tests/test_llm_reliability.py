import pytest
from unittest.mock import patch, MagicMock
from typing import Optional
import pybreaker
from tenacity import RetryError

# Import from the module under test
from app import (
    RETRY_MAX_ATTEMPTS,
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    llm_circuit_breaker,
    get_product_review_summary,
    call_llm_api,
    get_precomputed_product_summary,
)


def test_ac1_retry_transient_errors():
    """AC-1: Retry transient errors up to 3 times with exponential backoff."""
    # Reset circuit breaker state before test
    llm_circuit_breaker.close()

    # Mock LLM API to fail 3 times then succeed
    with patch("app.call_llm_api") as mock_llm:
        transient_error = Exception("Transient network error")
        mock_llm.side_effect = [transient_error, transient_error, transient_error, "Generated summary"]

        result = get_product_review_summary("test-product-1", "Generate summary for test product")

        # Verify retries happened exactly 3 times before succeeding
        assert mock_llm.call_count == 4  # Initial call + 3 retries
        assert result == "Generated summary"


def test_ac1_retry_exact_delays():
    """AC-1: Verify exponential backoff delays are 1s, 2s, 4s."""
    llm_circuit_breaker.close()

    with patch("app.call_llm_api") as mock_llm, \
         patch("tenacity.nap.time.sleep") as mock_sleep:
        transient_error = Exception("Rate limit 429")
        mock_llm.side_effect = transient_error

        with pytest.raises(Exception):
            get_product_review_summary("test-product-2", "Generate summary")

        # Verify sleep was called with correct exponential delays
        assert mock_sleep.call_count == RETRY_MAX_ATTEMPTS
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)
        mock_sleep.assert_any_call(4.0)


def test_ac2_fallback_to_precomputed_on_retry_exhausted():
    """AC-2: Fallback to precomputed summary when retries are exhausted."""
    llm_circuit_breaker.close()

    with patch("app.call_llm_api") as mock_llm, \
         patch("app.get_precomputed_product_summary") as mock_fallback:
        mock_llm.side_effect = Exception("Permanent LLM failure")
        mock_fallback.return_value = "Precomputed summary for test product 3"

        result = get_product_review_summary("test-product-3", "Generate summary")

        assert mock_llm.call_count == RETRY_MAX_ATTEMPTS + 1
        mock_fallback.assert_called_once_with("test-product-3")
        assert result == "Precomputed summary for test product 3"


def test_ac3_circuit_breaker_opens_after_5_failures():
    """AC-3: Circuit breaker opens after 5 consecutive failures, uses fallback directly."""
    llm_circuit_breaker.close()
    llm_circuit_breaker.fail_counter = 0

    with patch("app.call_llm_api") as mock_llm, \
         patch("app.get_precomputed_product_summary") as mock_fallback:
        mock_llm.side_effect = Exception("LLM API failure")
        mock_fallback.return_value = "Precomputed fallback summary"

        # First 5 calls should fail and trip the circuit
        for i in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            try:
                call_llm_api("test prompt", "gpt-3.5-turbo")
            except Exception:
                pass

        # Verify circuit is now open
        assert llm_circuit_breaker.current_state == "open"

        # Next call should skip retries and use fallback immediately
        result = get_product_review_summary("test-product-4", "Generate summary")
        # call_llm_api should not be called again due to open circuit
        assert mock_llm.call_count == CIRCUIT_BREAKER_FAILURE_THRESHOLD
        assert result == "Precomputed fallback summary"


def test_ac4_retry_attempt_logging():
    """AC-4: Structured logging for each retry attempt."""
    llm_circuit_breaker.close()

    with patch("app.call_llm_api") as mock_llm, \
         patch("structlog.get_logger") as mock_logger:
        mock_log_instance = MagicMock()
        mock_logger.return_value = mock_log_instance
        test_exception = ConnectionError("Network timeout")
        mock_llm.side_effect = [test_exception, test_exception, "Success"]

        get_product_review_summary("test-product-5", "Generate summary")

        # Verify warning logs for each retry
        assert mock_log_instance.warning.call_count == 2
        for call in mock_log_instance.warning.call_args_list:
            assert call[0][0] == "llm_api_retry_attempt"
            assert "attempt_number" in call[1]
            assert "next_delay" in call[1]
            assert "exception_type" in call[1]
            assert call[1]["exception_type"] == "ConnectionError"
            assert "exception_message" in call[1]


def test_ac5_permanent_failure_logging():
    """AC-5: Structured error logging for permanent failures with no fallback."""
    llm_circuit_breaker.close()

    with patch("app.call_llm_api") as mock_llm, \
         patch("app.get_precomputed_product_summary") as mock_fallback, \
         patch("structlog.get_logger") as mock_logger:
        mock_log_instance = MagicMock()
        mock_logger.return_value = mock_log_instance
        test_exception = Exception("LLM service unavailable")
        mock_llm.side_effect = test_exception
        mock_fallback.return_value = None

        with pytest.raises(Exception) as exc_info:
            get_product_review_summary("test-product-6", "Generate summary for product 6")

        # Verify error log contains all required fields
        assert mock_log_instance.error.called_once()
        log_call = mock_log_instance.error.call_args
        assert "product_id" in log_call[1]
        assert log_call[1]["product_id"] == "test-product-6"
        assert "prompt" in log_call[1]
        assert log_call[1]["prompt"] == "Generate summary for product 6"
        assert "number_of_attempts" in log_call[1]
        assert log_call[1]["number_of_attempts"] == RETRY_MAX_ATTEMPTS + 1
        assert "circuit_breaker_state" in log_call[1]
        assert "root_exception" in log_call[1]


def test_ac6_circuit_breaker_state_transition_logging():
    """AC-6: Structured info logging on circuit breaker state transitions."""
    llm_circuit_breaker.close()

    with patch("structlog.get_logger") as mock_logger:
        mock_log_instance = MagicMock()
        mock_logger.return_value = mock_log_instance

        # Trip circuit breaker
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            try:
                with llm_circuit_breaker:
                    raise Exception("Test failure")
            except:
                pass

        # Verify closed -> open transition log
        assert mock_log_instance.info.called_once()
        closed_open_log = mock_log_instance.info.call_args_list[0]
        assert closed_open_log[1]["previous_state"] == "closed"
        assert closed_open_log[1]["new_state"] == "open"
        assert "failure_count" in closed_open_log[1]
        assert "reset_timeout" in closed_open_log[1]

        # Force circuit half-open then close
        llm_circuit_breaker.current_state = "half_open"
        with llm_circuit_breaker:
            pass  # Success will close circuit

        # Verify half-open -> closed transition log
        assert mock_log_instance.info.call_count == 2
        halfopen_closed_log = mock_log_instance.info.call_args_list[1]
        assert halfopen_closed_log[1]["previous_state"] == "half_open"
        assert halfopen_closed_log[1]["new_state"] == "closed"


def test_ac7_503_when_all_methods_fail():
    """AC-7: Return 503 Service Unavailable when LLM API fails and no fallback exists."""
    llm_circuit_breaker.close()

    with patch("app.call_llm_api") as mock_llm, \
         patch("app.get_precomputed_product_summary") as mock_fallback:
        mock_llm.side_effect = Exception("LLM down")
        mock_fallback.return_value = None

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_product_review_summary("test-product-7", "Generate summary")

        assert exc_info.value.status_code == 503
        assert "Service Unavailable" in str(exc_info.value.detail)
