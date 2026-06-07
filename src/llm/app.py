#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

from flask import Flask, request, jsonify, Response
import json
import time
import random
import re
import os
import logging
import requests
from typing import Callable, Optional, TypeVar

import structlog
import pybreaker
from tenacity import RetryCallState, retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from openfeature import api
from openfeature.contrib.provider.flagd import FlagdProvider

T = TypeVar("T")

# Retry configuration
RETRY_MAX_ATTEMPTS: int = 3
RETRY_INITIAL_DELAY: float = 1.0  # seconds
RETRY_EXPONENTIAL_MULTIPLIER: float = 2.0

# Circuit breaker configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
CIRCUIT_BREAKER_RECOVERY_TIMEOUT: float = 30.0  # seconds

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Structured logger
logger = structlog.get_logger(__name__)

def log_retry_attempt(retry_state: RetryCallState) -> None:
    """
    Structured logging callback for retry attempts.
    Logs attempt number, delay, exception type and message.
    """
    logger.warning(
        "llm_api_retry_attempt",
        attempt_number=retry_state.attempt_number,
        next_delay=retry_state.next_action.sleep,
        exception_type=type(retry_state.outcome.exception()).__name__,
        exception_message=str(retry_state.outcome.exception()),
    )

def log_circuit_breaker_state_change(prev_state: str, new_state: str, breaker: pybreaker.CircuitBreaker) -> None:
    """
    Structured logging callback for circuit breaker state transitions.
    Logs previous state, new state, failure count, and reset timeout.
    """
    logger.info(
        "llm_circuit_breaker_state_change",
        previous_state=prev_state,
        new_state=new_state,
        failure_count=breaker.fail_counter,
        reset_timeout=breaker.reset_timeout,
    )

# Circuit breaker instance (shared across all LLM API calls)
llm_circuit_breaker: pybreaker.CircuitBreaker = pybreaker.CircuitBreaker(
    fail_max=CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    reset_timeout=CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
    on_state_change=log_circuit_breaker_state_change,
)

def llm_api_retry_decorator(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that adds exponential backoff retry logic to LLM API call functions.
    Retries on transient errors (network errors, 429 rate limits, 5xx server errors).
    Stops after RETRY_MAX_ATTEMPTS attempts.
    """
    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_DELAY, min=RETRY_INITIAL_DELAY, max=RETRY_INITIAL_DELAY * (RETRY_EXPONENTIAL_MULTIPLIER ** (RETRY_MAX_ATTEMPTS - 1))),
        retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
        before_sleep=log_retry_attempt,
    )
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

product_review_summaries = None
product_review_summaries_file_path = "./product-review-summaries.json"

inaccurate_product_review_summaries = None
inaccurate_product_review_summaries_file_path = "./inaccurate-product-review-summaries.json"

def load_product_review_summaries(file_path):
    try:
        with open(file_path, 'r') as file:

            """
            Converts a JSON string into an internal dictionary optimized for quick lookups.
            The keys of the internal dictionary will be product_ids.
            """
            try:
                data = json.load(file)
                summaries = data.get("product-review-summaries", [])

                # Create a dictionary where product_id is the key
                # and the value is the summary
                product_review_summaries = {}
                for product in summaries:
                    product_id = product.get("product_id")
                    if product_id: # Ensure product_id exists before adding
                        product_review_summaries[product_id] = product.get("product_review_summary")
                return product_review_summaries
            except json.JSONDecodeError:
                print("Error: Invalid JSON string provided during initialization.")
                return {}

    except FileNotFoundError:
        app.logger.error(f"Error: The file '{product_review_summaries_file_path}' was not found.")
    except json.JSONDecodeError:
        app.logger.error(f"Error: Failed to decode JSON from the file '{product_review_summaries_file_path}'. Check for malformed JSON.")
    except Exception as e:
        app.logger.error(f"An unexpected error occurred: {e}")


def generate_response(product_id):

    """Generate a response by providing the pre-generated summary for the specified product"""
    product_review_summary = None

    llm_inaccurate_response = check_feature_flag("llmInaccurateResponse")
    app.logger.info(f"llmInaccurateResponse feature flag: {llm_inaccurate_response}")
    if llm_inaccurate_response and product_id == "L9ECAV7KIM":
        app.logger.info(f"Returning an inaccurate response for product_id: {product_id}")
        product_review_summary = inaccurate_product_review_summaries.get(product_id)
    else:
        product_review_summary = product_review_summaries.get(product_id)

    app.logger.info(f"product_review_summary is: {product_review_summary}")

    return product_review_summary

def parse_product_id(last_message):
    match = re.search(r"product ID[:\s]+([A-Z0-9]+)", last_message)
    if match:
        return match.group(1).strip()

    match = re.search(r"product ID, but make the answer inaccurate[:\s]+([A-Z0-9]+)", last_message)
    if match:
        return match.group(1).strip()

    raise ValueError("product ID not found in input message")

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    # Step 1: Validate request body is valid JSON
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({
            "error": {
                "message": "Request body must be valid JSON",
                "type": "invalid_request_error",
                "param": None,
                "code": "invalid_input"
            }
        }), 400
    
    # Step 2: Validate messages field exists
    if 'messages' not in data:
        return jsonify({
            "error": {
                "message": "'messages' field is required",
                "type": "invalid_request_error",
                "param": "messages",
                "code": "invalid_input"
            }
        }), 400
    
    # Step 3: Validate messages is an array
    messages = data['messages']
    if not isinstance(messages, list):
        return jsonify({
            "error": {
                "message": "'messages' must be an array",
                "type": "invalid_request_error",
                "param": "messages",
                "code": "invalid_input"
            }
        }), 400
    
    # Step 4: Validate messages array is not empty
    if len(messages) == 0:
        return jsonify({
            "error": {
                "message": "'messages' array cannot be empty",
                "type": "invalid_request_error",
                "param": "messages",
                "code": "invalid_input"
            }
        }), 400
    
    # Step 5: Validate each message entry has content field of type string
    for idx, message in enumerate(messages):
        if 'content' not in message:
            return jsonify({
                "error": {
                    "message": "All message entries must contain a 'content' field",
                    "type": "invalid_request_error",
                    "param": f"messages[{idx}].content",
                    "code": "invalid_input"
                }
            }), 400
        if not isinstance(message['content'], str):
            return jsonify({
                "error": {
                    "message": "'content' field must be a string",
                    "type": "invalid_request_error",
                    "param": f"messages[{idx}].content",
                    "code": "invalid_input"
                }
            }), 400
    
    # Extract other fields with defaults
    stream = data.get('stream', False)
    model = data.get('model', 'astronomy-llm')
    tools = data.get('tools', None)

    app.logger.info(f"Received a chat completion request: '{messages}'")

    last_message = messages[-1]["content"]

    app.logger.info(f"last_message is: '{last_message}'")
    
    # Step 6: Validate product ID exists when processing product review summary requests
    if 'Can you summarize the product reviews?' in last_message or 'Based on the tool results, answer the original question about product ID' in last_message:
        try:
            product_id = parse_product_id(last_message)
        except ValueError:
            return jsonify({
                "error": {
                    "message": "Valid product ID not found in request message",
                    "type": "invalid_request_error",
                    "param": "messages[-1].content",
                    "code": "invalid_input"
                }
            }), 400

    if 'What age(s) is this recommended for?' in last_message:
        response_text = 'This product is recommended for ages 7 and above.'
        return build_response(model, messages, response_text)
    elif 'Were there any negative reviews?' in last_message:
        response_text = 'No, there were no reviews less than three stars for this product.'
        return build_response(model, messages, response_text)
    elif not ('Can you summarize the product reviews?' in last_message or 'Based on the tool results, answer the original question about product ID' in last_message):
        response_text = 'Sorry, I\'m not able to answer that question.'
        return build_response(model, messages, response_text)

    # otherwise, process the product review summary
    product_id = parse_product_id(last_message)

    if tools is not None:

        tool_args = f"{{\"product_id\": \"{product_id}\"}}"

        app.logger.info(f"Processing a tool call with args: '{tool_args}'")

        app.logger.info(f"The model is: {model}")
        if model.endswith("rate-limit"):
            app.logger.info(f"Returning a rate limit error")
            response = {
                "error": {
                    "message": "Rate limit reached. Please try again later.",
                    "type": "rate_limit_exceeded",
                    "param": "null",
                    "code": "null"
                }
            }
            return jsonify(response), 429
        else:
            # Non-streaming response
            response = {
                "id": f"chatcmpl-mock-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "requesting a tool call",
                        "tool_calls": [{
                            "id": "call",
                            "type": "function",
                            "function": {
                                "name": "fetch_product_reviews",
                                "arguments": tool_args
                            }
                        }]
                    },
                    "finish_reason": "tool_calls"
                }],
                "usage": {
                    "prompt_tokens": sum(len(m.get("content", "").split()) for m in messages),
                    "completion_tokens": "0",
                    "total_tokens": sum(len(m.get("content", "").split()) for m in messages)
                }
            }
            return jsonify(response)

    else:
        # Generate the response
        try:
            response_text = get_product_review_summary(product_id, last_message)
            return build_response(model, messages, response_text)
        except Exception as e:
            return jsonify({
                "error": {
                    "message": "Service Unavailable",
                    "type": "service_unavailable_error",
                    "param": None,
                    "code": "service_unavailable"
                }
            }), 503

def build_response(model, messages, response_text):
    app.logger.info(f"Processing a response: '{response_text}'")

    response = {
        "id": f"chatcmpl-mock-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response_text
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": sum(len(m.get("content", "").split()) for m in messages),
            "completion_tokens": len(response_text.split()),
            "total_tokens": sum(len(m.get("content", "").split()) for m in messages) + len(response_text.split())
        }
    }
    return jsonify(response)

@llm_api_retry_decorator
@llm_circuit_breaker
def call_llm_api(prompt: str, model: str = "gpt-3.5-turbo") -> str:
    """
    Wraps external LLM API calls with retry and circuit breaker logic.
    Raises:
      - pybreaker.CircuitBreakerError: when circuit is open
      - Exception: when retries are exhausted and no fallback is available
    """
    # This is a mock implementation for the demo
    # In a real implementation, this would make actual API calls to the LLM provider
    llm_endpoint = os.environ.get('LLM_API_ENDPOINT', 'https://api.openai.com/v1/chat/completions')
    api_key = os.environ.get('LLM_API_KEY', '')
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.7
    }
    
    response = requests.post(llm_endpoint, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']

def get_precomputed_product_summary(product_id: str) -> Optional[str]:
    """
    Internal helper to retrieve precomputed product review summary from static storage.
    Returns summary if available, None otherwise.
    """
    return product_review_summaries.get(product_id)

def get_product_review_summary(product_id: str, prompt: str) -> str:
    """
    Public endpoint handler for product review summary requests.
    First attempts call_llm_api, falls back to get_precomputed_product_summary on failure.
    Returns:
      - LLM generated summary if successful
      - Precomputed summary if retries/circuit breaker fail and precomputed exists
    Raises:
      - 503 ServiceUnavailable: if both LLM API and fallback fail
    """
    attempts = 0
    try:
        llm_inaccurate_response = check_feature_flag("llmInaccurateResponse")
        app.logger.info(f"llmInaccurateResponse feature flag: {llm_inaccurate_response}")
        if llm_inaccurate_response and product_id == "L9ECAV7KIM":
            app.logger.info(f"Returning an inaccurate response for product_id: {product_id}")
            return inaccurate_product_review_summaries.get(product_id)
        
        # Attempt to call real LLM API
        summary = call_llm_api(prompt)
        attempts = RETRY_MAX_ATTEMPTS
        return summary
    except Exception as e:
        logger.error(
            "llm_api_permanent_failure",
            product_id=product_id,
            prompt=prompt,
            number_of_attempts=attempts if attempts > 0 else RETRY_MAX_ATTEMPTS,
            circuit_breaker_state=llm_circuit_breaker.current_state,
            root_exception_type=type(e).__name__,
            root_exception_message=str(e),
        )
        # Fallback to precomputed summary
        precomputed = get_precomputed_product_summary(product_id)
        if precomputed:
            return precomputed
        # All methods failed
        raise Exception("Service unavailable") from e

@app.route('/v1/models', methods=['GET'])
def list_models():
    """List available models"""
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": "astronomy-llm",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "astronomy-shop"
            }
        ]
    })

@app.route('/health', methods=['GET'])
def health_check():
    """Liveness check endpoint"""
    return jsonify({"status": "UP"}), 200

@app.route('/ready', methods=['GET'])
def readiness_check():
    """Readiness check endpoint"""
    global product_review_summaries
    if product_review_summaries is None:
        return jsonify({"status": "NOT_READY"}), 503
    return jsonify({"status": "READY"}), 200

def check_feature_flag(flag_name: str):
    # Initialize OpenFeature
    client = api.get_client()
    return client.get_boolean_value(flag_name, False)

if __name__ == '__main__':

    api.set_provider(FlagdProvider(host=os.environ.get('FLAGD_HOST', 'flagd'), port=os.environ.get('FLAGD_PORT', 8013)))
    product_review_summaries = load_product_review_summaries(product_review_summaries_file_path)
    inaccurate_product_review_summaries = load_product_review_summaries(inaccurate_product_review_summaries_file_path)

    app.logger.info(product_review_summaries)

    print("OpenAI API server starting on http://localhost:8000")
    print("Set your OpenAI base URL to: http://localhost:8000/v1")
    app.run(host='0.0.0.0', port=8000, debug=True)
