import os
import pytest
from unittest.mock import patch, MagicMock
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import locustfile

@pytest.fixture(autouse=True)
def reset_env_vars():
    # Save original env vars
    original_env = os.environ.copy()
    yield
    # Restore original env vars
    os.environ.clear()
    os.environ.update(original_env)

def test_ac1_custom_traces_endpoint():
    """AC-1: Custom traces endpoint is used instead of default"""
    custom_endpoint = "https://custom-otel:4318/v1/traces"
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": custom_endpoint}):
        with patch("locustfile.TraceExporter") as mock_exporter:
            locustfile.initialize_otel_exporters()
            call_args = mock_exporter.call_args
            assert call_args[1]["endpoint"] == custom_endpoint

def test_ac2_custom_metrics_endpoint():
    """AC-2: Custom metrics endpoint is used instead of default"""
    custom_endpoint = "https://custom-otel:4318/v1/metrics"
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": custom_endpoint}):
        with patch("locustfile.MetricExporter") as mock_exporter:
            locustfile.initialize_otel_exporters()
            call_args = mock_exporter.call_args
            assert call_args[1]["endpoint"] == custom_endpoint

def test_ac3_custom_logs_endpoint():
    """AC-3: Custom logs endpoint is used instead of default"""
    custom_endpoint = "https://custom-otel:4318/v1/logs"
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": custom_endpoint}):
        with patch("locustfile.LogExporter") as mock_exporter:
            locustfile.initialize_otel_exporters()
            call_args = mock_exporter.call_args
            assert call_args[1]["endpoint"] == custom_endpoint

def test_ac4_insecure_mode_enabled():
    """AC-4: TLS disabled when INSECURE is True"""
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_INSECURE": "True"}):
        with patch("locustfile.TraceExporter") as mock_trace, \
             patch("locustfile.MetricExporter") as mock_metric, \
             patch("locustfile.LogExporter") as mock_log:
            locustfile.initialize_otel_exporters()
            assert mock_trace.call_args[1]["insecure"] == True
            assert mock_metric.call_args[1]["insecure"] == True
            assert mock_log.call_args[1]["insecure"] == True

def test_ac5_insecure_mode_disabled_default():
    """AC-5: TLS enabled by default when INSECURE not set or False"""
    # Test when not set
    with patch("locustfile.TraceExporter") as mock_trace, \
         patch("locustfile.MetricExporter") as mock_metric, \
         patch("locustfile.LogExporter") as mock_log:
        locustfile.initialize_otel_exporters()
        assert mock_trace.call_args[1]["insecure"] == False
        assert mock_metric.call_args[1]["insecure"] == False
        assert mock_log.call_args[1]["insecure"] == False

    # Test when explicitly False
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_INSECURE": "False"}):
        with patch("locustfile.TraceExporter") as mock_trace, \
             patch("locustfile.MetricExporter") as mock_metric, \
             patch("locustfile.LogExporter") as mock_log:
            locustfile.initialize_otel_exporters()
            assert mock_trace.call_args[1]["insecure"] == False
            assert mock_metric.call_args[1]["insecure"] == False
            assert mock_log.call_args[1]["insecure"] == False

def test_ac6_mtls_config_used():
    """AC-6: mTLS config applied when client cert and key provided"""
    cert_path = "/path/to/client.crt"
    key_path = "/path/to/client.key"
    with patch.dict(os.environ, {
        "OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE": cert_path,
        "OTEL_EXPORTER_OTLP_CLIENT_KEY": key_path
    }):
        with patch("locustfile.TraceExporter") as mock_trace, \
             patch("locustfile.MetricExporter") as mock_metric, \
             patch("locustfile.LogExporter") as mock_log:
            locustfile.initialize_otel_exporters()
            assert mock_trace.call_args[1]["client_cert_path"] == cert_path
            assert mock_trace.call_args[1]["client_key_path"] == key_path
            assert mock_metric.call_args[1]["client_cert_path"] == cert_path
            assert mock_metric.call_args[1]["client_key_path"] == key_path
            assert mock_log.call_args[1]["client_cert_path"] == cert_path
            assert mock_log.call_args[1]["client_key_path"] == key_path

def test_ac7_ca_cert_used():
    """AC-7: Custom CA cert used when provided"""
    ca_path = "/path/to/ca.crt"
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_CERTIFICATE_AUTHORITY": ca_path}):
        with patch("locustfile.TraceExporter") as mock_trace, \
             patch("locustfile.MetricExporter") as mock_metric, \
             patch("locustfile.LogExporter") as mock_log:
            locustfile.initialize_otel_exporters()
            assert mock_trace.call_args[1]["certificate_path"] == ca_path
            assert mock_metric.call_args[1]["certificate_path"] == ca_path
            assert mock_log.call_args[1]["certificate_path"] == ca_path

def test_ac8_retry_parameters_applied():
    """AC-8: Retry parameters are correctly applied to all exporters"""
    max_attempts = "3"
    initial_delay = "0.5"
    max_delay = "2.0"
    with patch.dict(os.environ, {
        "OTEL_EXPORTER_OTLP_RETRY_MAX_ATTEMPTS": max_attempts,
        "OTEL_EXPORTER_OTLP_RETRY_INITIAL_DELAY": initial_delay,
        "OTEL_EXPORTER_OTLP_RETRY_MAX_DELAY": max_delay
    }):
        with patch("locustfile.TraceExporter") as mock_trace, \
             patch("locustfile.MetricExporter") as mock_metric, \
             patch("locustfile.LogExporter") as mock_log:
            locustfile.initialize_otel_exporters()
            retry_config = mock_trace.call_args[1]["retry"]
            assert retry_config["max_attempts"] == int(max_attempts)
            assert retry_config["initial_delay"] == float(initial_delay)
            assert retry_config["max_delay"] == float(max_delay)
            
            retry_config_metric = mock_metric.call_args[1]["retry"]
            assert retry_config_metric["max_attempts"] == int(max_attempts)
            assert retry_config_metric["initial_delay"] == float(initial_delay)
            assert retry_config_metric["max_delay"] == float(max_delay)
            
            retry_config_log = mock_log.call_args[1]["retry"]
            assert retry_config_log["max_attempts"] == int(max_attempts)
            assert retry_config_log["initial_delay"] == float(initial_delay)
            assert retry_config_log["max_delay"] == float(max_delay)

def test_ac10_backward_compatibility_defaults():
    """AC-10: Service starts successfully with no env vars set, uses defaults"""
    expected_defaults = {
        "traces_endpoint": "http://otel-collector:4318/v1/traces",
        "metrics_endpoint": "http://otel-collector:4318/v1/metrics",
        "logs_endpoint": "http://otel-collector:4318/v1/logs",
        "insecure": False,
        "retry_max_attempts": 5,
        "retry_initial_delay": 1.0,
        "retry_max_delay": 5.0
    }
    # Ensure no OTEL env vars are set
    for k in list(os.environ.keys()):
        if k.startswith("OTEL_EXPORTER_OTLP_"):
            del os.environ[k]
            
    with patch("locustfile.TraceExporter") as mock_trace, \
         patch("locustfile.MetricExporter") as mock_metric, \
         patch("locustfile.LogExporter") as mock_log:
        # Should not raise any exceptions
        try:
            locustfile.initialize_otel_exporters()
        except Exception as e:
            pytest.fail(f"Initialization failed with default env vars: {e}")
        
        # Verify defaults are used
        assert mock_trace.call_args[1]["endpoint"] == expected_defaults["traces_endpoint"]
        assert mock_metric.call_args[1]["endpoint"] == expected_defaults["metrics_endpoint"]
        assert mock_log.call_args[1]["endpoint"] == expected_defaults["logs_endpoint"]
        assert mock_trace.call_args[1]["insecure"] == expected_defaults["insecure"]
        retry_config = mock_trace.call_args[1]["retry"]
        assert retry_config["max_attempts"] == expected_defaults["retry_max_attempts"]
        assert retry_config["initial_delay"] == expected_defaults["retry_initial_delay"]
        assert retry_config["max_delay"] == expected_defaults["retry_max_delay"]


def test_ac1_timeout_default_when_no_env_var():
    """AC-1: When OTEL_EXPORTER_OTLP_TIMEOUT is not set, all exporters use 10s timeout"""
    # Ensure no timeout env var is set
    for k in list(os.environ.keys()):
        if k == "OTEL_EXPORTER_OTLP_TIMEOUT":
            del os.environ[k]
    
    with patch("locustfile.TraceExporter") as mock_trace, \
         patch("locustfile.MetricExporter") as mock_metric, \
         patch("locustfile.LogExporter") as mock_log:
        
        locustfile.initialize_otel_exporters()
        
        # Check all exporters have timeout=10
        assert mock_trace.call_args[1]["timeout"] == 10
        assert mock_metric.call_args[1]["timeout"] == 10
        assert mock_log.call_args[1]["timeout"] == 10


def test_ac2_timeout_uses_env_var_when_valid():
    """AC-2: When OTEL_EXPORTER_OTLP_TIMEOUT is set to valid positive integer, all exporters use that value"""
    test_timeout = 20
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_TIMEOUT": str(test_timeout)}):
        with patch("locustfile.TraceExporter") as mock_trace, \
             patch("locustfile.MetricExporter") as mock_metric, \
             patch("locustfile.LogExporter") as mock_log:
            
            locustfile.initialize_otel_exporters()
            
            # Check all exporters use the custom timeout value
            assert mock_trace.call_args[1]["timeout"] == test_timeout
            assert mock_metric.call_args[1]["timeout"] == test_timeout
            assert mock_log.call_args[1]["timeout"] == test_timeout


def test_ac3_timeout_fallback_to_default_when_invalid():
    """AC-3: When OTEL_EXPORTER_OTLP_TIMEOUT is set to invalid value, fallback to default 10s"""
    invalid_values = ["abc", "0", "-5", "10.5", "", "  15  ", "null"]
    
    for invalid_val in invalid_values:
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_TIMEOUT": invalid_val}):
            with patch("locustfile.TraceExporter") as mock_trace, \
                 patch("locustfile.MetricExporter") as mock_metric, \
                 patch("locustfile.LogExporter") as mock_log:
                
                locustfile.initialize_otel_exporters()
                
                # Check all exporters fall back to 10
                assert mock_trace.call_args[1]["timeout"] == 10, f"Failed for invalid value '{invalid_val}'"
                assert mock_metric.call_args[1]["timeout"] == 10, f"Failed for invalid value '{invalid_val}'"
                assert mock_log.call_args[1]["timeout"] == 10, f"Failed for invalid value '{invalid_val}'"


def test_ac5_no_hardcoded_timeout_in_exporter_init():
    """AC-5: No hardcoded 10-second timeout value remains in OTLP exporter initialization parameters"""
    # Read the locustfile source code
    locustfile_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locustfile.py")
    with open(locustfile_path, "r") as f:
        content = f.read()
    
    # Find the initialize_otel_exporters function
    import re
    func_match = re.search(r"def initialize_otel_exporters\(\):(.*?)(?=\ndef |\Z)", content, re.DOTALL)
    assert func_match is not None, "initialize_otel_exporters function not found"
    func_content = func_match.group(1)
    
    # Check for hardcoded timeout=10 in exporter calls
    hardcoded_timeout_pattern = re.compile(r"timeout\s*=\s*10\b")
    matches = hardcoded_timeout_pattern.findall(func_content)
    assert len(matches) == 0, f"Hardcoded timeout=10 found in initialize_otel_exporters function: {matches}"
