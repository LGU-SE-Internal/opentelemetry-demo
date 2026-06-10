"""
Integration tests for flagd resilience functionality in recommendation service
"""
import os
import time
from unittest.mock import patch, Mock
import pytest
from tenacity import RetryError
import pybreaker

# Import the wrapper interface from the spec
from recommendation.flagd_resilience import get_flag_value  # type: ignore

# Test constants
TEST_FLAG_KEY = "test.feature.flag"
TEST_DEFAULT_BOOL = False
TEST_DEFAULT_STR = "default"
TEST_DEFAULT_INT = 0
TEST_DEFAULT_FLOAT = 0.0
TEST_DEFAULT_DICT = {"enabled": False}

# Environment variable test values
TEST_RETRY_MAX_ATTEMPTS = 2
TEST_INITIAL_BACKOFF = 50
TEST_MAX_BACKOFF = 200
TEST_FAILURE_THRESHOLD = 3
TEST_RESET_TIMEOUT = 1000
TEST_HALF_OPEN_MAX_CALLS = 1


@pytest.fixture(autouse=True)
def reset_env_vars():
    """Reset environment variables before each test"""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the flagd resilience singleton before each test"""
    with patch("recommendation.flagd_resilience._instance", None):
        yield


def test_ac1_retry_transient_errors_with_exponential_backoff():
    """AC-1: Retry transient errors up to max attempts with exponential backoff"""
    # Configure retry parameters
    os.environ["FLAGD_RETRY_MAX_ATTEMPTS"] = str(TEST_RETRY_MAX_ATTEMPTS)
    os.environ["FLAGD_RETRY_INITIAL_BACKOFF_MS"] = str(TEST_INITIAL_BACKOFF)
    os.environ["FLAGD_RETRY_MAX_BACKOFF_MS"] = str(TEST_MAX_BACKOFF)
    
    # Mock OpenFeature client to always fail with transient error
    with patch("openfeature.OpenFeatureClient.get_boolean_value") as mock_get:
        mock_get.side_effect = ConnectionError("flagd connection timeout")
        
        start_time = time.time()
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        end_time = time.time()
        
        # Verify retries occurred (max_attempts retries = total calls = attempts + 1)
        assert mock_get.call_count == TEST_RETRY_MAX_ATTEMPTS + 1
        # Verify backoff was applied (total time > sum of initial backoffs, capped at max)
        expected_min_time = (TEST_INITIAL_BACKOFF + min(TEST_INITIAL_BACKOFF * 2, TEST_MAX_BACKOFF)) / 1000
        assert (end_time - start_time) >= expected_min_time
        # Verify default value is returned after retries
        assert result == TEST_DEFAULT_BOOL


def test_ac2_return_default_on_retry_exhausted():
    """AC-2: Return default without exceptions when retries are exhausted"""
    os.environ["FLAGD_RETRY_MAX_ATTEMPTS"] = "1"
    
    with patch("openfeature.OpenFeatureClient.get_string_value") as mock_get:
        mock_get.side_effect = ConnectionResetError("Network error")
        
        # No exception should be raised
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_STR)
        
        assert result == TEST_DEFAULT_STR
        assert mock_get.call_count == 2  # Initial call + 1 retry


def test_ac3_circuit_opens_on_consecutive_failures():
    """AC-3: Circuit opens after failure threshold, returns default immediately"""
    os.environ["FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = str(TEST_FAILURE_THRESHOLD)
    os.environ["FLAGD_RETRY_MAX_ATTEMPTS"] = "0"  # Disable retries to simplify
    
    with patch("openfeature.OpenFeatureClient.get_integer_value") as mock_get:
        mock_get.side_effect = Exception("500 Internal Server Error")
        
        # Make failure threshold calls to trip the circuit
        for i in range(TEST_FAILURE_THRESHOLD):
            result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_INT)
            assert result == TEST_DEFAULT_INT
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD
        
        # Next call should not hit flagd (circuit is open)
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_INT)
        assert result == TEST_DEFAULT_INT
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD  # No additional calls


def test_ac4_circuit_half_open_behavior():
    """AC-4: Circuit transitions to half-open after reset timeout, test calls work as expected"""
    os.environ["FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = str(TEST_FAILURE_THRESHOLD)
    os.environ["FLAGD_CIRCUIT_BREAKER_RESET_TIMEOUT_MS"] = str(TEST_RESET_TIMEOUT)
    os.environ["FLAGD_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS"] = str(TEST_HALF_OPEN_MAX_CALLS)
    os.environ["FLAGD_RETRY_MAX_ATTEMPTS"] = "0"
    
    with patch("openfeature.OpenFeatureClient.get_float_value") as mock_get:
        # First trip the circuit
        mock_get.side_effect = Exception("503 Service Unavailable")
        for i in range(TEST_FAILURE_THRESHOLD):
            get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_FLOAT)
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD
        
        # Circuit is open, no calls made
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_FLOAT)
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD
        
        # Wait for reset timeout
        time.sleep(TEST_RESET_TIMEOUT / 1000 + 0.1)
        
        # Test successful half-open call: circuit closes
        mock_get.side_effect = None
        mock_get.return_value = 42.0
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_FLOAT)
        assert result == 42.0
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD + 1
        
        # Subsequent calls work normally
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_FLOAT)
        assert result == 42.0
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD + 2
        
        # Now trip circuit again and test failed half-open call
        mock_get.side_effect = Exception("500 Error")
        for i in range(TEST_FAILURE_THRESHOLD):
            get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_FLOAT)
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD + 2 + TEST_FAILURE_THRESHOLD
        
        # Wait for reset
        time.sleep(TEST_RESET_TIMEOUT / 1000 + 0.1)
        
        # Half-open call fails: circuit reopens
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_FLOAT)
        assert result == TEST_DEFAULT_FLOAT
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD + 2 + TEST_FAILURE_THRESHOLD + 1
        
        # No more calls allowed in this reset period
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_FLOAT)
        assert mock_get.call_count == TEST_FAILURE_THRESHOLD + 2 + TEST_FAILURE_THRESHOLD + 1


def test_ac5_config_defaults_applied():
    """AC-5: Default config values are used when env vars are not set"""
    # No env vars set, verify defaults are loaded
    with patch("recommendation.flagd_resilience.tenacity.retry") as mock_retry, \
         patch("recommendation.flagd_resilience.pybreaker.CircuitBreaker") as mock_circuit:
        
        # Initialize the singleton by calling the function
        with patch("openfeature.OpenFeatureClient.get_boolean_value", return_value=True):
            get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        
        # Verify retry was initialized with defaults
        retry_kwargs = mock_retry.call_args.kwargs
        assert retry_kwargs["stop"].max_attempt_number == 3  # Default max attempts
        assert retry_kwargs["wait"].multiplier == 0.1  # Default initial backoff 100ms = 0.1s
        assert retry_kwargs["wait"].max == 2.0  # Default max backoff 2000ms = 2s
        
        # Verify circuit breaker was initialized with defaults
        circuit_kwargs = mock_circuit.call_args.kwargs
        assert circuit_kwargs["fail_max"] == 5  # Default failure threshold
        assert circuit_kwargs["reset_timeout"] == 30  # Default reset timeout 30000ms = 30s
        assert circuit_kwargs["half_open_max"] == 2  # Default half-open max calls


def test_ac6_structured_logging():
    """AC-6: Structured logs are emitted for failures, retries, and circuit state changes"""
    os.environ["FLAGD_RETRY_MAX_ATTEMPTS"] = "1"
    
    with patch("structlog.get_logger") as mock_logger, \
         patch("openfeature.OpenFeatureClient.get_boolean_value") as mock_get:
        mock_log = Mock()
        mock_logger.return_value = mock_log
        mock_get.side_effect = ConnectionError("Connection refused")
        
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        
        # Verify error log for initial failure
        mock_log.error.assert_any_call(
            "flagd call failed",
            flag_key=TEST_FLAG_KEY,
            error="Connection refused",
            attempt=1
        )
        # Verify retry log
        mock_log.info.assert_any_call(
            "retrying flagd call",
            flag_key=TEST_FLAG_KEY,
            attempt=2,
            backoff_ms=pytest.approx(100, rel=0.2)
        )
        # Verify circuit open log when threshold is hit
        os.environ["FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = "1"
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        mock_log.warning.assert_any_call(
            "circuit breaker state changed",
            old_state="closed",
            new_state="open",
            failure_count=1
        )


def test_ac7_metrics_exported():
    """AC-7: OpenTelemetry metrics are exported for flagd calls, retries, and circuit state"""
    from opentelemetry.metrics import get_meter
    
    with patch.object(get_meter("recommendation"), "create_counter") as mock_counter, \
         patch.object(get_meter("recommendation"), "create_gauge") as mock_gauge, \
         patch("openfeature.OpenFeatureClient.get_boolean_value") as mock_get:
        
        # Initialize the singleton
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        
        # Verify metrics are created
        mock_counter.assert_any_call("flagd_call_total", description="Total flagd API calls")
        mock_counter.assert_any_call("flagd_retry_total", description="Total flagd retry attempts")
        mock_gauge.assert_any_call("flagd_circuit_breaker_state", description="Current circuit breaker state (0=closed, 1=open, 2=half-open)")
        
        # Test successful call increments success counter
        mock_get.return_value = True
        counter_add_mock = mock_counter.return_value.add
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        counter_add_mock.assert_any_call(1, {"status": "success", "flag_key": TEST_FLAG_KEY})
        
        # Test failed call increments failure counter and retry counter
        mock_get.side_effect = ConnectionError("Error")
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        counter_add_mock.assert_any_call(1, {"status": "failure", "flag_key": TEST_FLAG_KEY})
        retry_calls = [call for call in counter_add_mock.call_args_list if "retry" in str(call)]
        assert len(retry_calls) > 0


def test_ac8_open_circuit_no_outbound_calls():
    """AC-8: No outbound calls to flagd when circuit is open"""
    os.environ["FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = "2"
    os.environ["FLAGD_RETRY_MAX_ATTEMPTS"] = "0"
    
    with patch("openfeature.OpenFeatureClient.get_boolean_value") as mock_get:
        mock_get.side_effect = Exception("500 Error")
        
        # Trip the circuit
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        assert mock_get.call_count == 2
        
        # Multiple calls while open should not increase call count
        for i in range(10):
            get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        assert mock_get.call_count == 2


def test_ac9_non_retryable_errors_return_default_immediately():
    """AC-9: Non-retryable errors return default immediately, no retries, no circuit impact"""
    os.environ["FLAGD_RETRY_MAX_ATTEMPTS"] = "3"
    os.environ["FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = "1"
    
    with patch("openfeature.OpenFeatureClient.get_boolean_value") as mock_get:
        # 4xx error is non-retryable
        mock_get.side_effect = Exception("400 Bad Request: Invalid flag key")
        
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        
        # Only 1 call, no retries
        assert mock_get.call_count == 1
        assert result == TEST_DEFAULT_BOOL
        
        # Circuit should still be closed (failure not counted)
        mock_get.side_effect = None
        mock_get.return_value = True
        result = get_flag_value(TEST_FLAG_KEY, TEST_DEFAULT_BOOL)
        assert result == True
        assert mock_get.call_count == 2  # Call goes through, circuit is open


def test_ac10_backward_compatibility():
    """AC-10: Wrapper maintains backward compatibility with existing calls when flagd is healthy"""
    test_cases = [
        (TEST_FLAG_KEY, TEST_DEFAULT_BOOL, True, bool),
        (TEST_FLAG_KEY, TEST_DEFAULT_STR, "enabled", str),
        (TEST_FLAG_KEY, TEST_DEFAULT_INT, 10, int),
        (TEST_FLAG_KEY, TEST_DEFAULT_FLOAT, 99.9, float),
        (TEST_FLAG_KEY, TEST_DEFAULT_DICT, {"enabled": True, "version": "v1"}, dict),
    ]
    
    with patch("openfeature.OpenFeatureClient") as mock_client:
        for flag_key, default, return_val, type_ in test_cases:
            if type_ == bool:
                mock_client.get_boolean_value.return_value = return_val
            elif type_ == str:
                mock_client.get_string_value.return_value = return_val
            elif type_ == int:
                mock_client.get_integer_value.return_value = return_val
            elif type_ == float:
                mock_client.get_float_value.return_value = return_val
            elif type_ == dict:
                mock_client.get_object_value.return_value = return_val
            
            result = get_flag_value(flag_key, default)
            assert result == return_val
            assert isinstance(result, type_)
