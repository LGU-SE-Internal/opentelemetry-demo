import os
import time
from unittest.mock import Mock, patch
import pytest

# Import the public API from the flag provider module as per spec
from recommendation.flag_provider import (
    get_boolean_flag,
    get_string_flag,
    get_number_flag,
)

# Helper to reset environment variables between tests
@pytest.fixture(autouse=True)
def reset_env():
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)

# AC-1: FLAGD_TIMEOUT aborts slow calls and returns default
def test_ac1_timeout_aborts_slow_flagd_calls():
    # Set timeout to 100ms
    os.environ["FLAGD_TIMEOUT"] = "100"
    
    # Mock flagd client that takes 500ms to respond
    with patch("recommendation.flag_provider.flagd_client.evaluate") as mock_eval:
        def slow_evaluate(*args, **kwargs):
            time.sleep(0.5)
            return {"value": True, "reason": "TARGETING_MATCH"}
        mock_eval.side_effect = slow_evaluate
        
        # Call flag evaluation, should return default instead of waiting
        result = get_boolean_flag("test_flag", default_value=False)
        assert result == False
        mock_eval.assert_called_once()

# AC-2: Circuit opens after N consecutive failures
def test_ac2_circuit_opens_after_consecutive_failures():
    # Set failure threshold to 3
    os.environ["FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = "3"
    
    with patch("recommendation.flag_provider.flagd_client.evaluate") as mock_eval:
        # Make first 3 calls fail
        mock_eval.side_effect = ConnectionError("Flagd unreachable")
        
        # First 3 calls should attempt to call flagd and return default
        for _ in range(3):
            result = get_boolean_flag("test_flag", default_value=False)
            assert result == False
        assert mock_eval.call_count == 3
        
        # 4th call should NOT call flagd, return default immediately (circuit open)
        result = get_boolean_flag("test_flag", default_value=False)
        assert result == False
        assert mock_eval.call_count == 3  # No new call made

# AC-3: Circuit closes after recovery timeout if flagd is healthy
def test_ac3_circuit_closes_after_recovery_timeout_when_flagd_healthy():
    os.environ["FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = "2"
    os.environ["FLAGD_CIRCUIT_BREAKER_RECOVERY_TIMEOUT"] = "500"  # 500ms recovery
    
    with patch("recommendation.flag_provider.flagd_client.evaluate") as mock_eval:
        # First 2 calls fail to open circuit
        mock_eval.side_effect = ConnectionError("Flagd unreachable")
        for _ in range(2):
            get_boolean_flag("test_flag", default_value=False)
        assert mock_eval.call_count == 2
        
        # Call immediately after circuit open: no new call
        get_boolean_flag("test_flag", default_value=False)
        assert mock_eval.call_count == 2
        
        # Wait for recovery timeout
        time.sleep(0.6)
        
        # Now make flagd return success
        mock_eval.side_effect = None
        mock_eval.return_value = {"value": True, "reason": "TARGETING_MATCH"}
        
        # Test call should go through to flagd, circuit closes
        result = get_boolean_flag("test_flag", default_value=False)
        assert result == True
        assert mock_eval.call_count == 3

# AC-4: TLS enabled uses encrypted connections
def test_ac4_tls_enabled_uses_encrypted_connections():
    os.environ["FLAGD_TLS_ENABLED"] = "true"
    os.environ["FLAGD_TLS_CERT_PATH"] = "/tmp/test_ca.crt"
    
    with patch("recommendation.flag_provider.flagd_client.Client") as mock_client:
        # Initialize the flag provider (should happen on import/startup)
        from importlib import reload
        import recommendation.flag_provider
        reload(recommendation.flag_provider)
        
        # Verify client was initialized with TLS enabled
        client_args = mock_client.call_args
        assert client_args[1]["tls"] == True
        assert client_args[1]["ca_cert"] == "/tmp/test_ca.crt"

# AC-5: Client cert/key provided uses mTLS
def test_ac5_client_cert_key_provided_uses_mtls():
    os.environ["FLAGD_TLS_ENABLED"] = "true"
    os.environ["FLAGD_TLS_CLIENT_CERT_PATH"] = "/tmp/client.crt"
    os.environ["FLAGD_TLS_CLIENT_KEY_PATH"] = "/tmp/client.key"
    
    with patch("recommendation.flag_provider.flagd_client.Client") as mock_client:
        from importlib import reload
        import recommendation.flag_provider
        reload(recommendation.flag_provider)
        
        client_args = mock_client.call_args
        assert client_args[1]["client_cert"] == "/tmp/client.crt"
        assert client_args[1]["client_key"] == "/tmp/client.key"

# AC-6: All configuration options use correct defaults when not set
def test_ac6_config_uses_correct_defaults():
    # No env vars set, use defaults
    with patch("recommendation.flag_provider.flagd_client.Client") as mock_client:
        from importlib import reload
        import recommendation.flag_provider
        reload(recommendation.flag_provider)
        
        # Verify client default config
        client_args = mock_client.call_args
        assert client_args[1]["timeout"] == 2000
        assert client_args[1]["tls"] == False
        
        # Verify circuit breaker defaults
        assert recommendation.flag_provider.circuit_breaker.failure_threshold == 5
        assert recommendation.flag_provider.circuit_breaker.recovery_timeout == 30000

# AC-7: Invalid configuration causes fast fail on startup
def test_ac7_negative_timeout_causes_startup_failure():
    os.environ["FLAGD_TIMEOUT"] = "-100"
    
    with pytest.raises(ValueError, match="FLAGD_TIMEOUT must be a positive integer"):
        from importlib import reload
        import recommendation.flag_provider
        reload(recommendation.flag_provider)

def test_ac7_tls_enabled_missing_cert_causes_startup_failure():
    os.environ["FLAGD_TLS_ENABLED"] = "true"
    os.environ["FLAGD_TLS_CERT_PATH"] = "/nonexistent/path/ca.crt"
    
    with pytest.raises(FileNotFoundError, match="TLS cert file not found at /nonexistent/path/ca.crt"):
        from importlib import reload
        import recommendation.flag_provider
        reload(recommendation.flag_provider)
