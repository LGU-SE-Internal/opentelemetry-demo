"""
Integration tests for payload and message content size limits (issue #1967)
"""
import os
import json
import pytest
from fastapi.testclient import TestClient
from main import app
from validation import validate_request_body_size, validate_message_content_length, PayloadTooLargeError

client = TestClient(app)

DEFAULT_MAX_BYTES = 1048576
DEFAULT_MAX_CONTENT_LENGTH = 4096

# Reset environment variables before each test
@pytest.fixture(autouse=True)
def reset_env_vars():
    if "LLM_MAX_REQUEST_BYTES" in os.environ:
        del os.environ["LLM_MAX_REQUEST_BYTES"]
    if "LLM_MAX_MESSAGE_CONTENT_LENGTH" in os.environ:
        del os.environ["LLM_MAX_MESSAGE_CONTENT_LENGTH"]
    yield

def test_ac1_content_length_exceeds_limit_returns_413():
    """AC-1: Request with Content-Length > configured LLM_MAX_REQUEST_BYTES returns 413"""
    # Set custom limit for this test
    os.environ["LLM_MAX_REQUEST_BYTES"] = "100"
    # Send request with Content-Length header larger than limit
    response = client.post(
        "/v1/chat/completions",
        headers={"Content-Length": "200"},
        json={"messages": [{"role": "user", "content": "test"}]}
    )
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["type"] == "payload_too_large"
    assert error["code"] == "payload_too_large"
    assert "request body size" in error["message"].lower()

def test_ac2_actual_body_size_exceeds_limit_returns_413():
    """AC-2: Request body larger than limit (even with wrong Content-Length) returns 413"""
    os.environ["LLM_MAX_REQUEST_BYTES"] = "100"
    # Create large payload that exceeds 100 bytes
    large_content = "x" * 200
    payload = {"messages": [{"role": "user", "content": large_content}]}
    # Send with wrong Content-Length header that's under limit
    response = client.post(
        "/v1/chat/completions",
        headers={"Content-Length": "50"},
        json=payload
    )
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["type"] == "payload_too_large"
    assert error["code"] == "payload_too_large"

def test_ac3_message_content_exceeds_limit_returns_413():
    """AC-3: Chat completion with message content longer than limit returns 413"""
    os.environ["LLM_MAX_MESSAGE_CONTENT_LENGTH"] = "10"
    # Message content is 11 characters long
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": "0123456789a"}]
    }
    response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["type"] == "payload_too_large"
    assert error["code"] == "payload_too_large"
    assert "message content" in error["message"].lower()

def test_ac4_custom_request_size_limit_used():
    """AC-4: Custom LLM_MAX_REQUEST_BYTES value is used instead of default"""
    custom_limit = 500
    os.environ["LLM_MAX_REQUEST_BYTES"] = str(custom_limit)
    # Test that limit is correctly applied: payload larger than custom limit returns 413
    large_payload = {"messages": [{"role": "user", "content": "x" * 600}]}
    response = client.post("/v1/chat/completions", json=large_payload)
    assert response.status_code == 413
    # Test that payload under custom limit passes
    small_payload = {"messages": [{"role": "user", "content": "x" * 100}]}
    response = client.post("/v1/chat/completions", json=small_payload)
    # Should not return 413 (may return other errors if model not available, but not 413)
    assert response.status_code != 413

def test_ac5_custom_message_content_limit_used():
    """AC-5: Custom LLM_MAX_MESSAGE_CONTENT_LENGTH value is used instead of default"""
    custom_limit = 20
    os.environ["LLM_MAX_MESSAGE_CONTENT_LENGTH"] = str(custom_limit)
    # Message longer than custom limit returns 413
    long_msg = {"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "x" * 21}]}
    response = client.post("/v1/chat/completions", json=long_msg)
    assert response.status_code == 413
    # Message shorter than custom limit passes
    short_msg = {"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "x" * 10}]}
    response = client.post("/v1/chat/completions", json=short_msg)
    assert response.status_code != 413

def test_ac6_valid_requests_processed_normally():
    """AC-6: Requests within both limits are processed normally with no 413 errors"""
    # Use default limits
    valid_payload = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": "Hello, how are you?"}]
    }
    response = client.post("/v1/chat/completions", json=valid_payload)
    assert response.status_code != 413

def test_ac7_error_response_conforms_to_openai_schema():
    """AC-7: All 413 error responses conform to OpenAI standard error schema"""
    # Test request size error format
    os.environ["LLM_MAX_REQUEST_BYTES"] = "10"
    response = client.post("/v1/chat/completions", json={"messages": [{"content": "x" * 100}]})
    assert response.status_code == 413
    response_json = response.json()
    assert "error" in response_json
    error = response_json["error"]
    assert isinstance(error["message"], str)
    assert error["type"] == "payload_too_large"
    assert error["param"] is None
    assert error["code"] == "payload_too_large"

    # Test message content error format
    os.environ["LLM_MAX_MESSAGE_CONTENT_LENGTH"] = "5"
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x" * 10}]})
    assert response.status_code == 413
    response_json = response.json()
    assert "error" in response_json
    error = response_json["error"]
    assert isinstance(error["message"], str)
    assert error["type"] == "payload_too_large"
    assert error["param"] is None
    assert error["code"] == "payload_too_large"

# Unit tests for validation functions as per spec interface
def test_validate_request_body_size_interface():
    """Test validate_request_body_size function interface as defined in spec"""
    # Should raise PayloadTooLargeError when body exceeds limit
    with pytest.raises(PayloadTooLargeError):
        validate_request_body_size(b"x" * 100, max_allowed_bytes=50)
    # Should not raise when body is within limit
    validate_request_body_size(b"x" * 50, max_allowed_bytes=50)
    validate_request_body_size(b"x" * 49, max_allowed_bytes=50)

def test_validate_message_content_length_interface():
    """Test validate_message_content_length function interface as defined in spec"""
    # Should raise when any message content exceeds limit
    messages = [
        {"role": "user", "content": "x" * 10},
        {"role": "assistant", "content": "x" * 20}
    ]
    with pytest.raises(PayloadTooLargeError):
        validate_message_content_length(messages, max_allowed_length=15)
    # Should not raise when all messages are within limit
    validate_message_content_length(messages, max_allowed_length=20)
    validate_message_content_length(messages, max_allowed_length=25)
