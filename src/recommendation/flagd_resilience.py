"""
Flagd resilience wrapper with retry, circuit breaker, logging, and metrics
"""
import os
import time
from typing import Union, Optional, Any
from enum import Enum
import structlog
import tenacity
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, RetryCallState
import pybreaker
from openfeature import OpenFeatureClient
from opentelemetry.metrics import get_meter

logger = structlog.get_logger()
meter = get_meter("recommendation")

# Singleton instance
_instance: Optional["FlagdResilienceWrapper"] = None

# Metrics
flagd_call_counter = meter.create_counter(
    "flagd_call_total",
    description="Total flagd API calls"
)
flagd_retry_counter = meter.create_counter(
    "flagd_retry_total",
    description="Total flagd retry attempts"
)
circuit_breaker_gauge = meter.create_gauge(
    "flagd_circuit_breaker_state",
    description="Current circuit breaker state (0=closed, 1=open, 2=half-open)"
)

class CircuitBreakerState(Enum):
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2

def get_flag_value(
    flag_key: str,
    default_value: Union[bool, str, int, float, dict],
    context: Optional[dict] = None
) -> Union[bool, str, int, float, dict]:
    """
    Wraps OpenFeature flag evaluation with resiliency patterns (retry, circuit breaker).
    
    Args:
        flag_key: Unique key of the feature flag to evaluate
        default_value: Value to return if flag evaluation fails, retries are exhausted, or circuit is open
        context: Optional evaluation context containing attributes for flag targeting
        
    Returns:
        Evaluated flag value if successful, otherwise returns the provided default_value.
        
    Raises:
        No exceptions are raised to the caller; all errors are handled internally and result in default_value being returned.
    """
    global _instance
    if _instance is None:
        _instance = FlagdResilienceWrapper()
    return _instance.get_flag_value(flag_key, default_value, context)

class NonRetryableError(Exception):
    """Exception to wrap non-retryable errors so they don't trigger retries or circuit breaker"""
    pass

class FlagdResilienceWrapper:
    def __init__(self):
        # Load configuration from environment variables with defaults
        self.retry_max_attempts = int(os.environ.get("FLAGD_RETRY_MAX_ATTEMPTS", "3"))
        self.retry_initial_backoff_ms = int(os.environ.get("FLAGD_RETRY_INITIAL_BACKOFF_MS", "100"))
        self.retry_max_backoff_ms = int(os.environ.get("FLAGD_RETRY_MAX_BACKOFF_MS", "2000"))
        self.circuit_failure_threshold = int(os.environ.get("FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
        self.circuit_reset_timeout_ms = int(os.environ.get("FLAGD_CIRCUIT_BREAKER_RESET_TIMEOUT_MS", "30000"))
        self.circuit_half_open_max_calls = int(os.environ.get("FLAGD_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS", "2"))
        
        # Initialize OpenFeature client
        self.flagd_client = OpenFeatureClient()
        
        # Setup retry policy
        self.retry_decorator = retry(
            stop=stop_after_attempt(self.retry_max_attempts),
            wait=wait_exponential(
                multiplier=self.retry_initial_backoff_ms / 1000,
                max=self.retry_max_backoff_ms / 1000
            ),
            retry=retry_if_exception(lambda e: not isinstance(e, NonRetryableError)),
            before_sleep=self._log_retry_attempt,
            reraise=True
        )
        
        # Setup circuit breaker
        self.circuit_breaker = pybreaker.CircuitBreaker(
            fail_max=self.circuit_failure_threshold,
            reset_timeout=self.circuit_reset_timeout_ms / 1000,
            half_open_max=self.circuit_half_open_max_calls,
            on_open=self._on_circuit_open,
            on_close=self._on_circuit_close,
            on_half_open=self._on_circuit_half_open,
            exclude=[NonRetryableError]
        )
        
        # Initialize circuit gauge state
        self._update_circuit_gauge()
        
    def _is_retryable_error(self, e: Exception) -> bool:
        """Check if an error is retryable (transient network/5xx errors)"""
        error_msg = str(e).lower()
        # Network errors are retryable
        if any(err in error_msg for err in ["connectionerror", "timeout", "network error", "connection reset", "connection refused", "transport error"]):
            return True
        # 5xx responses are retryable
        if any(status in error_msg for status in [" 500", " 501", " 502", " 503", " 504", "500 internal", "502 bad gateway", "503 service unavailable", "504 gateway timeout"]):
            return True
        # All other errors (4xx, invalid inputs) are not retryable
        return False
    
    def _log_retry_attempt(self, state: RetryCallState) -> None:
        """Log retry attempts and increment retry metric"""
        flag_key = state.kwargs.get("flag_key", "unknown")
        attempt_number = state.attempt_number
        backoff_ms = state.next_action.sleep * 1000 if state.next_action else 0
        
        logger.info(
            "retrying flagd call",
            flag_key=flag_key,
            attempt=attempt_number,
            backoff_ms=int(backoff_ms)
        )
        flagd_retry_counter.add(1, {"flag_key": flag_key})
    
    def _on_circuit_open(self, breaker: pybreaker.CircuitBreaker) -> None:
        """Log circuit open state transition"""
        logger.warning(
            "circuit breaker state changed",
            old_state="closed",
            new_state="open",
            failure_count=breaker.fail_counter
        )
        self._update_circuit_gauge()
    
    def _on_circuit_close(self, breaker: pybreaker.CircuitBreaker) -> None:
        """Log circuit close state transition"""
        logger.warning(
            "circuit breaker state changed",
            old_state="half-open",
            new_state="closed",
            failure_count=breaker.fail_counter
        )
        self._update_circuit_gauge()
    
    def _on_circuit_half_open(self, breaker: pybreaker.CircuitBreaker) -> None:
        """Log circuit half-open state transition"""
        logger.warning(
            "circuit breaker state changed",
            old_state="open",
            new_state="half-open",
            failure_count=breaker.fail_counter
        )
        self._update_circuit_gauge()
    
    def _update_circuit_gauge(self) -> None:
        """Update the circuit breaker state gauge metric"""
        state_map = {
            pybreaker.STATE_CLOSED: CircuitBreakerState.CLOSED.value,
            pybreaker.STATE_OPEN: CircuitBreakerState.OPEN.value,
            pybreaker.STATE_HALF_OPEN: CircuitBreakerState.HALF_OPEN.value
        }
        circuit_breaker_gauge.set(state_map[self.circuit_breaker.state], {})
    
    def _call_flagd_client(self, flag_key: str, default_value: Any, context: Optional[dict]) -> Any:
        """Call the appropriate OpenFeature client method based on default value type"""
        if isinstance(default_value, bool):
            return self.flagd_client.get_boolean_value(flag_key, default_value, context)
        elif isinstance(default_value, str):
            return self.flagd_client.get_string_value(flag_key, default_value, context)
        elif isinstance(default_value, int):
            return self.flagd_client.get_integer_value(flag_key, default_value, context)
        elif isinstance(default_value, float):
            return self.flagd_client.get_float_value(flag_key, default_value, context)
        elif isinstance(default_value, dict):
            return self.flagd_client.get_object_value(flag_key, default_value, context)
        else:
            return default_value
    
    def get_flag_value(self, flag_key: str, default_value: Any, context: Optional[dict] = None) -> Any:
        """Internal implementation with resiliency patterns"""
        try:
            # Wrap call with circuit breaker and retry
            @self.circuit_breaker
            @self.retry_decorator
            def wrapped_call():
                try:
                    result = self._call_flagd_client(flag_key, default_value, context)
                    flagd_call_counter.add(1, {"status": "success", "flag_key": flag_key})
                    return result
                except Exception as e:
                    # Check if error is non-retryable
                    if not self._is_retryable_error(e):
                        logger.error(
                            "non-retryable flagd error",
                            flag_key=flag_key,
                            error=str(e)
                        )
                        flagd_call_counter.add(1, {"status": "failure", "flag_key": flag_key})
                        # Raise as NonRetryableError to avoid retries and circuit breaker
                        raise NonRetryableError(f"Non-retryable error: {str(e)}") from e
                    # Log retryable error
                    logger.error(
                        "flagd call failed",
                        flag_key=flag_key,
                        error=str(e)
                    )
                    flagd_call_counter.add(1, {"status": "failure", "flag_key": flag_key})
                    raise e
            
            return wrapped_call()
        except (tenacity.RetryError, pybreaker.CircuitBreakerError, NonRetryableError, Exception) as e:
            # All errors are caught and return default
            logger.warning(
                "returning default flag value",
                flag_key=flag_key,
                error=str(e),
                default_value=default_value
            )
            return default_value
