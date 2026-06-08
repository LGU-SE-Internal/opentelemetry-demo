from typing import Callable, Any, Optional
import os
import time
import requests
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_result,
    RetryCallState,
)
import pybreaker

logger = structlog.get_logger()

# Custom exception for circuit breaker open state
class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is open and requests are blocked temporarily."""
    pass

# Load configuration from environment variables
MAX_RETRY_ATTEMPTS = int(os.environ.get("LLM_RETRY_MAX_ATTEMPTS", 3))
INITIAL_BACKOFF_MS = int(os.environ.get("LLM_RETRY_INITIAL_BACKOFF_MS", 100))
CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(os.environ.get("LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 10))
CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS = int(os.environ.get("LLM_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS", 30000))

# Initialize circuit breaker
circuit_breaker = pybreaker.CircuitBreaker(
    fail_max=CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    reset_timeout=CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS / 1000,
    on_open=lambda _: logger.warning(
        "llm_circuit_breaker_open",
        event="llm_circuit_breaker_open",
        provider="",
        endpoint="",
        request_id="",
        user_id="",
    ),
    on_close=lambda _: logger.info(
        "llm_circuit_breaker_closed",
        event="llm_circuit_breaker_closed",
        provider="",
        endpoint="",
        request_id="",
        user_id="",
    ),
)

def get_common_metadata(request_metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract common metadata fields from request metadata for logging."""
    return {
        "provider": request_metadata.get("provider", ""),
        "endpoint": request_metadata.get("endpoint", ""),
        "request_id": request_metadata.get("request_id", ""),
        "user_id": request_metadata.get("user_id", ""),
    }

def is_transient_error(response: Optional[requests.Response]) -> bool:
    """Check if a response or exception is a transient error that should be retried."""
    if response is None:
        return True
    # Retry on 5xx status codes and 429 rate limit
    return response.status_code >= 500 or response.status_code == 429

def should_retry(retry_state: RetryCallState) -> bool:
    """Determine if a retry should be attempted based on the outcome of the last attempt."""
    outcome = retry_state.outcome
    if outcome is None:
        return False
    
    if outcome.failed:
        exc = outcome.exception()
        # Retry on network errors (requests exceptions except HTTPError)
        if isinstance(exc, requests.exceptions.RequestException) and not isinstance(exc, requests.exceptions.HTTPError):
            return True
        return False
    else:
        response = outcome.result()
        # Check if response is a transient error
        if is_transient_error(response):
            # If it's 4xx except 429, don't retry
            if 400 <= response.status_code < 500 and response.status_code != 429:
                return False
            return True
        return False

def get_wait_time(retry_state: RetryCallState) -> float:
    """Get the wait time for the next retry, respecting Retry-After headers for 429 responses."""
    attempt_number = retry_state.attempt_number
    outcome = retry_state.outcome
    
    # Check if we have a response with Retry-After header for 429
    if outcome and not outcome.failed:
        response = outcome.result()
        if response.status_code == 429 and "Retry-After" in response.headers:
            try:
                retry_after_seconds = int(response.headers["Retry-After"])
                return retry_after_seconds
            except (ValueError, TypeError):
                # If Retry-After is not a valid integer, fall back to exponential backoff
                pass
    
    # Calculate exponential backoff
    backoff_seconds = (INITIAL_BACKOFF_MS / 1000) * (2 ** (attempt_number - 1))
    return backoff_seconds

def log_retry_attempt(retry_state: RetryCallState) -> None:
    """Log retry attempt details including backoff delay."""
    # Get request metadata from the wrapped function args
    request_metadata = retry_state.args[1] if len(retry_state.args) > 1 else {}
    common_metadata = get_common_metadata(request_metadata)
    attempt_number = retry_state.attempt_number
    backoff_delay_ms = int(get_wait_time(retry_state) * 1000)
    
    status_code = None
    error_message = None
    outcome = retry_state.outcome
    if outcome:
        if outcome.failed:
            error_message = str(outcome.exception())
        else:
            response = outcome.result()
            status_code = response.status_code
            error_message = response.reason if 400 <= response.status_code < 600 else None
    
    logger.info(
        "llm_retry_attempt",
        event="llm_retry_attempt",
        attempt_number=attempt_number,
        backoff_delay_ms=backoff_delay_ms,
        status_code=status_code,
        error_message=error_message,
        **common_metadata,
    )

def with_llm_retry(
    api_call: Callable[..., requests.Response],
    request_metadata: dict[str, Any]
) -> requests.Response:
    """
    Wraps outbound LLM provider API calls with retry, backoff and circuit breaker logic.
    
    Args:
        api_call: Callable that executes the API request and returns a requests.Response object
        request_metadata: Structured metadata including provider name, endpoint, request ID, user ID for logging
    
    Returns:
        Successful requests.Response object from the LLM provider
    
    Raises:
        requests.exceptions.HTTPError: For non-transient errors (4xx except 429) or when retries are exhausted
        CircuitBreakerOpen: When circuit breaker is open and requests are blocked temporarily
    """
    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=get_wait_time,
        retry=should_retry,
        before_sleep=log_retry_attempt,
        reraise=True,
    )
    def _wrapped():
        try:
            response = circuit_breaker.call(api_call)
            # Raise HTTPError for non-transient bad responses
            if not is_transient_error(response):
                response.raise_for_status()
            return response
        except pybreaker.CircuitBreakerError:
            common_metadata = get_common_metadata(request_metadata)
            logger.error(
                "llm_circuit_breaker_open",
                event="llm_circuit_breaker_open",
                **common_metadata,
            )
            raise CircuitBreakerOpen("Circuit breaker is open, requests are temporarily blocked") from None
        except requests.exceptions.HTTPError as e:
            # Log final failure
            common_metadata = get_common_metadata(request_metadata)
            logger.error(
                "llm_retry_final_failure",
                event="llm_retry_final_failure",
                status_code=e.response.status_code if e.response else None,
                error_message=str(e),
                **common_metadata,
            )
            raise
        except requests.exceptions.RequestException as e:
            # Log final failure for network errors
            common_metadata = get_common_metadata(request_metadata)
            logger.error(
                "llm_retry_final_failure",
                event="llm_retry_final_failure",
                error_message=str(e),
                **common_metadata,
            )
            raise
    
    return _wrapped()
