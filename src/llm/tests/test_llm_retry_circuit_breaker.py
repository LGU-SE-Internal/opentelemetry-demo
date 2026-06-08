import os
import time
from unittest.mock import Mock, patch, MagicMock
import requests
import pytest
from llm_retry import (
    with_llm_retry,
    CircuitBreakerOpen,
    MAX_RETRY_ATTEMPTS,
    INITIAL_BACKOFF_MS,
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS,
    circuit_breaker,
)

@pytest.fixture
def reset_circuit_breaker():
    circuit_breaker.close()
    yield
    circuit_breaker.close()

@pytest.fixture
def mock_request_metadata():
    return {
        "provider": "openai",
        "endpoint": "/v1/chat/completions",
        "request_id": "test-request-123",
        "user_id": "user-456",
    }

def test_ac1_5xx_retries_with_exponential_backoff(mock_request_metadata):
    mock_api_call = Mock()
    mock_response = Mock()
    mock_response.status_code = 503
    mock_response.reason = "Service Unavailable"
    mock_api_call.return_value = mock_response
    
    with patch('llm_retry.logger.info') as mock_logger, pytest.raises(requests.exceptions.HTTPError):
        with_llm_retry(mock_api_call, mock_request_metadata)
    
    # Verify number of attempts: max attempts + 1 initial call?
    assert mock_api_call.call_count == MAX_RETRY_ATTEMPTS + 1
    
    # Verify backoff delays are exponential
    logged_delays = []
    for call in mock_logger.call_args_list:
        if call[0][0] == "llm_retry_attempt":
            logged_delays.append(call[1]["backoff_delay_ms"])
    
    expected_delays = [INITIAL_BACKOFF_MS * (2 ** i) for i in range(MAX_RETRY_ATTEMPTS)]
    assert logged_delays == expected_delays

def test_ac2_429_respects_retry_after_header(mock_request_metadata):
    mock_api_call = Mock()
    mock_response = Mock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "5"}
    mock_response.reason = "Too Many Requests"
    mock_api_call.return_value = mock_response
    
    with patch('llm_retry.logger.info') as mock_logger, pytest.raises(requests.exceptions.HTTPError):
        with_llm_retry(mock_api_call, mock_request_metadata)
    
    # First retry should have 5000ms delay from Retry-After
    first_attempt_log = [call for call in mock_logger.call_args_list if call[0][0] == "llm_retry_attempt"][0]
    assert first_attempt_log[1]["backoff_delay_ms"] == 5000

def test_ac3_non_429_4xx_no_retries(mock_request_metadata):
    mock_api_call = Mock()
    mock_response = Mock()
    mock_response.status_code = 401
    mock_response.reason = "Unauthorized"
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized", response=mock_response)
    mock_api_call.return_value = mock_response
    
    with pytest.raises(requests.exceptions.HTTPError):
        with_llm_retry(mock_api_call, mock_request_metadata)
    
    assert mock_api_call.call_count == 1

def test_ac4_network_errors_retry_with_backoff(mock_request_metadata):
    mock_api_call = Mock()
    mock_api_call.side_effect = requests.exceptions.ConnectionError("Connection reset by peer")
    
    with patch('llm_retry.logger.info') as mock_logger, pytest.raises(requests.exceptions.ConnectionError):
        with_llm_retry(mock_api_call, mock_request_metadata)
    
    assert mock_api_call.call_count == MAX_RETRY_ATTEMPTS + 1
    
    # Verify backoff delays are exponential
    logged_delays = []
    for call in mock_logger.call_args_list:
        if call[0][0] == "llm_retry_attempt":
            logged_delays.append(call[1]["backoff_delay_ms"])
    
    expected_delays = [INITIAL_BACKOFF_MS * (2 ** i) for i in range(MAX_RETRY_ATTEMPTS)]
    assert logged_delays == expected_delays

def test_ac5_structured_logging_for_retry_events(mock_request_metadata):
    mock_api_call = Mock()
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.reason = "Internal Server Error"
    mock_api_call.return_value = mock_response
    
    with patch('llm_retry.logger.info') as mock_info_logger, patch('llm_retry.logger.error') as mock_error_logger:
        try:
            with_llm_retry(mock_api_call, mock_request_metadata)
        except:
            pass
    
    # Check retry attempt logs have all required fields
    retry_logs = [call for call in mock_info_logger.call_args_list if call[0][0] == "llm_retry_attempt"]
    assert len(retry_logs) == MAX_RETRY_ATTEMPTS
    for log in retry_logs:
        log_fields = log[1]
        assert "event" in log_fields
        assert "provider" in log_fields
        assert "endpoint" in log_fields
        assert "request_id" in log_fields
        assert "user_id" in log_fields
        assert "attempt_number" in log_fields
        assert "backoff_delay_ms" in log_fields
        assert "status_code" in log_fields
        assert "error_message" in log_fields
        assert log_fields["provider"] == mock_request_metadata["provider"]
        assert log_fields["endpoint"] == mock_request_metadata["endpoint"]
        assert log_fields["request_id"] == mock_request_metadata["request_id"]
        assert log_fields["user_id"] == mock_request_metadata["user_id"]
    
    # Check final failure log has all required fields
    failure_log = [call for call in mock_error_logger.call_args_list if call[0][0] == "llm_retry_final_failure"][0]
    failure_fields = failure_log[1]
    assert "event" in failure_fields
    assert "provider" in failure_fields
    assert "endpoint" in failure_fields
    assert "request_id" in failure_fields
    assert "user_id" in failure_fields
    assert "status_code" in failure_fields
    assert "error_message" in failure_fields

def test_ac6_circuit_breaker_opens_after_consecutive_failures(reset_circuit_breaker, mock_request_metadata):
    mock_api_call = Mock()
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.reason = "Internal Server Error"
    mock_api_call.return_value = mock_response
    
    # Make enough failed requests to open circuit breaker
    for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        try:
            with_llm_retry(mock_api_call, mock_request_metadata)
        except:
            pass
    
    # Next request should raise CircuitBreakerOpen immediately without calling api
    mock_api_call.reset_mock()
    with pytest.raises(CircuitBreakerOpen):
        with_llm_retry(mock_api_call, mock_request_metadata)
    
    assert mock_api_call.call_count == 0

def test_ac7_circuit_breaker_recovery(reset_circuit_breaker, mock_request_metadata):
    mock_api_call = Mock()
    fail_response = Mock()
    fail_response.status_code = 500
    fail_response.reason = "Internal Server Error"
    success_response = Mock()
    success_response.status_code = 200
    success_response.ok = True
    
    # Open circuit breaker
    mock_api_call.return_value = fail_response
    for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        try:
            with_llm_retry(mock_api_call, mock_request_metadata)
        except:
            pass
    
    # Verify circuit is open
    with pytest.raises(CircuitBreakerOpen):
        with_llm_retry(mock_api_call, mock_request_metadata)
    
    # Fast forward time past recovery timeout
    with patch('time.time', return_value=time.time() + (CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS / 1000) + 1):
        # Test request succeeds, circuit should close
        mock_api_call.return_value = success_response
        response = with_llm_retry(mock_api_call, mock_request_metadata)
        assert response.status_code == 200
        assert circuit_breaker.current_state == "closed"

def test_ac8_configuration_from_environment_variables():
    with patch.dict(os.environ, {
        "LLM_RETRY_MAX_ATTEMPTS": "5",
        "LLM_RETRY_INITIAL_BACKOFF_MS": "200",
        "LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "20",
        "LLM_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS": "60000",
    }):
        # Reload the module to pick up new env vars
        import importlib
        import llm_retry
        importlib.reload(llm_retry)
        
        assert llm_retry.MAX_RETRY_ATTEMPTS == 5
        assert llm_retry.INITIAL_BACKOFF_MS == 200
        assert llm_retry.CIRCUIT_BREAKER_FAILURE_THRESHOLD == 20
        assert llm_retry.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS == 60000
        assert llm_retry.circuit_breaker.fail_max == 20
        assert llm_retry.circuit_breaker.reset_timeout == 60
