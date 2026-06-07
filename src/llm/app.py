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

from openfeature import api
from openfeature.contrib.provider.flagd import FlagdProvider
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Rate limiting configuration
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get('RATE_LIMIT_MAX_REQUESTS', 100))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('RATE_LIMIT_WINDOW_SECONDS', 60))

# Initialize rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[f"{RATE_LIMIT_MAX_REQUESTS} per {RATE_LIMIT_WINDOW_SECONDS} seconds"],
    storage_uri="memory://",
    headers_enabled=True,
    header_name_mapping={
        'X-RateLimit-Limit': 'X-RateLimit-Limit',
        'X-RateLimit-Remaining': 'X-RateLimit-Remaining',
        'X-RateLimit-Reset': 'X-RateLimit-Reset',
        'Retry-After': 'Retry-After'
    }
)

# Custom rate limit exceeded handler
@app.errorhandler(429)
def rate_limit_exceeded_handler(e):
    retry_after = int(e.description.split()[-2]) if "Too Many Requests" in e.description else RATE_LIMIT_WINDOW_SECONDS
    response = jsonify({
        "error": "Rate limit exceeded",
        "retry_after": retry_after
    })
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after)
    return response

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
        response_text = generate_response(product_id)

        return build_response(model, messages, response_text)

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
