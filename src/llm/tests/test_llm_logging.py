import json
import logging
import sys
from io import StringIO
from unittest.mock import patch, MagicMock
from datetime import datetime
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
from flask import Flask

# Import the init function and app
sys.path.insert(0, '/workspace/src/llm')
from app import init_llm_service_logger, app as flask_app, load_product_review_summaries

@pytest.fixture
def logger():
    return init_llm_service_logger()

@pytest.fixture
def app():
    yield flask_app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def capture_stderr():
    old_stderr = sys.stderr
    sys.stderr = StringIO()
    yield sys.stderr
    sys.stderr = old_stderr

@pytest.fixture
def setup_tracing():
    # Setup OpenTelemetry tracing
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    yield
    # Reset tracing
    trace._TRACE_PROVIDER = None

def test_ac1_no_unstructured_print_output(logger, capture_stderr):
    """AC-1: No unstructured print statement output is emitted to stdout or stderr when the LLM service runs; all output consists of valid JSON objects"""
    # Test logger output is valid JSON
    test_message = "Test info message"
    logger.info(test_message)
    
    output = capture_stderr.getvalue().strip()
    assert output, "No output captured"
    
    # Verify it's valid JSON
    log_entry = json.loads(output)
    assert isinstance(log_entry, dict), "Log output is not a JSON object"
    assert log_entry['message'] == test_message, "Message content mismatch"
    
    # Test error output too
    capture_stderr.truncate(0)
    capture_stderr.seek(0)
    logger.error("Test error message")
    output = capture_stderr.getvalue().strip()
    log_entry = json.loads(output)
    assert isinstance(log_entry, dict), "Error log output is not a JSON object"

def test_ac2_logs_include_trace_span_context_when_tracing_active(logger, capture_stderr, setup_tracing):
    """AC-2: When OpenTelemetry tracing is active, every log entry contains non-empty trace_id (32-character hex string) and span_id (16-character hex string) fields matching the current active span context"""
    tracer = trace.get_tracer("test-tracer")
    
    with tracer.start_as_current_span("test-span") as span:
        span_context = span.get_span_context()
        expected_trace_id = format(span_context.trace_id, '032x')
        expected_span_id = format(span_context.span_id, '016x')
        
        logger.info("Test message with tracing")
        output = capture_stderr.getvalue().strip()
        log_entry = json.loads(output)
        
        assert 'trace_id' in log_entry, "trace_id field missing from log entry"
        assert 'span_id' in log_entry, "span_id field missing from log entry"
        assert log_entry['trace_id'] == expected_trace_id, f"Expected trace_id {expected_trace_id}, got {log_entry['trace_id']}"
        assert log_entry['span_id'] == expected_span_id, f"Expected span_id {expected_span_id}, got {log_entry['span_id']}"
        assert len(log_entry['trace_id']) == 32, "trace_id must be 32 characters"
        assert len(log_entry['span_id']) == 16, "span_id must be 16 characters"

def test_ac3_original_print_messages_preserved_as_message_field(capture_stderr):
    """AC-3: All original log message content from existing print statements is preserved exactly as the `message` field in structured log output"""
    # Test the print statement we replaced
    with patch('app.logger') as mock_logger:
        # Trigger the error case that previously used print
        with patch('json.load', side_effect=json.JSONDecodeError("Test error", "", 0)):
            result = load_product_review_summaries("test.json")
            assert result == {}
            mock_logger.error.assert_called_once_with("Error: Invalid JSON string provided during initialization.")

def test_ac4_log_level_field_correct(logger, capture_stderr):
    """AC-4: Log entries have correct `level` field values: "INFO" for normal operation events, "ERROR" for error events including stack traces when exc_info is provided"""
    # Test INFO level
    logger.info("Info message")
    output = capture_stderr.getvalue().strip()
    log_entry = json.loads(output)
    assert log_entry['level'] == "INFO", f"Expected level INFO, got {log_entry['level']}"
    
    # Test ERROR level
    capture_stderr.truncate(0)
    capture_stderr.seek(0)
    logger.error("Error message")
    output = capture_stderr.getvalue().strip()
    log_entry = json.loads(output)
    assert log_entry['level'] == "ERROR", f"Expected level ERROR, got {log_entry['level']}"
    
    # Test ERROR with exc_info
    capture_stderr.truncate(0)
    capture_stderr.seek(0)
    try:
        raise ValueError("Test exception")
    except ValueError:
        logger.error("Error with exception", exc_info=True)
    output = capture_stderr.getvalue().strip()
    log_entry = json.loads(output)
    assert log_entry['level'] == "ERROR"
    assert 'exc_info' in log_entry or 'stack_trace' in log_entry or 'Test exception' in str(log_entry), "Stack trace not included in error log"

def test_ac5_logs_have_standard_required_fields(logger, capture_stderr):
    """AC-5: All log entries include consistent standard fields matching other demo services: `timestamp` (ISO 8601 format), `service.name` (fixed value "llm-service"), `message`, `level`"""
    logger.info("Test standard fields")
    output = capture_stderr.getvalue().strip()
    log_entry = json.loads(output)
    
    # Check required fields exist
    required_fields = ['timestamp', 'service.name', 'message', 'level']
    for field in required_fields:
        assert field in log_entry, f"Required field {field} missing from log entry"
    
    # Validate field values
    assert log_entry['service.name'] == "llm-service", "service.name must be 'llm-service'"
    assert log_entry['message'] == "Test standard fields"
    assert log_entry['level'] == "INFO"
    
    # Validate timestamp is ISO 8601 format
    try:
        datetime.fromisoformat(log_entry['timestamp'].replace('Z', '+00:00'))
    except ValueError:
        pytest.fail("timestamp is not in valid ISO 8601 format")

def test_ac6_service_functionality_unchanged(client):
    """AC-6: LLM service functionality remains fully operational after changes: all existing API endpoints return correct responses with no degradation in performance"""
    # Test health endpoint
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json == {"status": "UP"}
    
    # Test models endpoint
    response = client.get('/v1/models')
    assert response.status_code == 200
    assert 'data' in response.json
    assert len(response.json['data']) == 1
    assert response.json['data'][0]['id'] == "astronomy-llm"
    
    # Test chat completions endpoint with invalid body
    response = client.post('/v1/chat/completions', json={})
    assert response.status_code == 400
    assert 'error' in response.json
    assert response.json['error']['message'] == "'messages' field is required"

def test_ac7_logs_work_when_tracing_disabled(logger, capture_stderr):
    """AC-7: When OpenTelemetry tracing is disabled, log entries are still valid JSON objects with no missing required fields, and no runtime errors are thrown due to missing trace context"""
    # Ensure no trace provider is set
    trace._TRACE_PROVIDER = None
    
    # Test logging doesn't throw errors
    logger.info("Test with tracing disabled")
    output = capture_stderr.getvalue().strip()
    log_entry = json.loads(output)
    
    # Check required fields are still present
    required_fields = ['timestamp', 'service.name', 'message', 'level']
    for field in required_fields:
        assert field in log_entry, f"Required field {field} missing when tracing disabled"
    
    # trace_id and span_id may be absent, that's acceptable
    # Just ensure no errors were thrown
