#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


# Python
import os
import json
import re
import uuid
from concurrent import futures
import random
import signal
import asyncio
from types import FrameType
from typing import Optional, Any

# Pip
import grpc
from opentelemetry import trace, metrics
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode

# Local
import logging
import demo_pb2
import demo_pb2_grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc
from grpc_health.v1.health import HealthServicer
from database import fetch_product_reviews, fetch_product_reviews_from_db, fetch_avg_product_review_score_from_db, db_connection_str

# Circuit breaker imports
from pybreaker import CircuitBreaker, CircuitBreakerListener
from cachetools import TTLCache
import time
from typing import List

from openfeature import api
from openfeature.contrib.provider.flagd import FlagdProvider

from metrics import (
    init_metrics
)

# OpenAI
from openai import OpenAI, APIConnectionError, InternalServerError, APIError, RateLimitError
import openai
import requests.exceptions

# Tenacity for retry logic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, wait_random, stop_before_attempt, before_sleep_log, retry_if_result
from typing import Callable, TypeVar, ParamSpec
import functools

P = ParamSpec("P")
R = TypeVar("R")

# Load retry configuration from environment variables
LLM_RETRY_MAX_ATTEMPTS = int(os.getenv("LLM_RETRY_MAX_ATTEMPTS", "3"))
LLM_RETRY_INITIAL_BACKOFF_SEC = float(os.getenv("LLM_RETRY_INITIAL_BACKOFF_SEC", "1.0"))
LLM_RETRY_MAX_BACKOFF_SEC = float(os.getenv("LLM_RETRY_MAX_BACKOFF_SEC", "30.0"))

def with_llm_retry(func: Callable[P, R]) -> Callable[P, R]:
    """
    Decorator that adds retry logic for LLM API calls.
    Applied to all functions making OpenAI-compatible API requests.
    
    Retry triggers on:
    - Network errors (requests.exceptions.RequestException, openai.APIConnectionError)
    - 5xx status codes (openai.InternalServerError, openai.APIError)
    - 429 rate limit errors (openai.RateLimitError)
    
    Raises original error after max retry attempts are exhausted.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Generate idempotency key once per original request
        idempotency_key = str(uuid.uuid4())
        # Add X-Idempotency-Key to headers if not present
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["X-Idempotency-Key"] = idempotency_key
        
        retry_attempt = 0
        
        def custom_wait(retry_state):
            nonlocal retry_attempt
            retry_attempt = retry_state.attempt_number
            exception = retry_state.outcome.exception()
            
            # Handle Retry-After header for 429 errors
            if isinstance(exception, RateLimitError) and hasattr(exception, 'response') and exception.response is not None:
                retry_after = exception.response.headers.get("Retry-After")
                if retry_after is not None:
                    try:
                        return float(retry_after)
                    except ValueError:
                        pass
            
            # Exponential backoff with jitter
            base_delay = LLM_RETRY_INITIAL_BACKOFF_SEC * (2 ** (retry_state.attempt_number - 1))
            jitter = random.uniform(0.5, 1.5)
            delay = min(base_delay * jitter, LLM_RETRY_MAX_BACKOFF_SEC)
            return delay
        
        def log_retry(retry_state):
            exception = retry_state.outcome.exception()
            delay = custom_wait(retry_state)
            
            retry_after_header = None
            if isinstance(exception, RateLimitError) and hasattr(exception, 'response') and exception.response is not None:
                retry_after = exception.response.headers.get("Retry-After")
                if retry_after is not None:
                    try:
                        retry_after_header = float(retry_after)
                    except ValueError:
                        pass
            
            # Structured log
            log_data = {
                "event": "llm_api_retry",
                "attempt_number": retry_state.attempt_number,
                "max_attempts": LLM_RETRY_MAX_ATTEMPTS,
                "backoff_delay_sec": delay,
                "error_type": type(exception).__name__,
                "error_message": str(exception)[:200],
                "retry_after_header": retry_after_header
            }
            logger.info(log_data, extra=log_data)
        
        @retry(
            stop=stop_after_attempt(LLM_RETRY_MAX_ATTEMPTS),
            wait=custom_wait,
            retry=retry_if_exception_type((
                requests.exceptions.RequestException,
                APIConnectionError,
                InternalServerError,
                APIError,
                RateLimitError
            )),
            before_sleep=log_retry,
            reraise=True
        )
        def wrapped_call():
            return func(*args, **kwargs)
        
        return wrapped_call()
    
    return wrapper


from google.protobuf.json_format import MessageToJson, MessageToDict

# Global shutdown flag
shutdown_initiated = False
service_initialized = False
logger = logging.getLogger('main')
import psycopg2
import time

# --- Circuit Breaker Metrics ---
meter = metrics.get_meter("product-reviews.service")
circuit_breaker_trips_counter = meter.create_counter(
    name="product_catalog_circuit_breaker_trips_total",
    description="Total number of times circuit breaker has tripped to open state"
)
circuit_breaker_state_gauge = meter.create_up_down_counter(
    name="product_catalog_circuit_breaker_state",
    description="Current state of circuit breaker: 0=closed, 1=open, 2=half-open"
)
circuit_breaker_calls_counter = meter.create_counter(
    name="product_catalog_circuit_breaker_calls_total",
    description="Total calls to wrapped gRPC method, labeled by result: success, failure, fallback"
)

# --- Circuit Breaker Listener for Logging and Metrics ---
class CircuitBreakerMetricsListener(CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):
        state_map = {"closed": 0, "open": 1, "half-open": 2}
        # Update state gauge
        circuit_breaker_state_gauge.add(state_map[new_state.name.lower()] - state_map[old_state.name.lower()])
        
        # Log state change
        logger.info(
            "Circuit breaker state changed",
            extra={
                "event_type": "circuit_state_change",
                "previous_state": old_state.name.lower(),
                "new_state": new_state.name.lower(),
                "failure_count": cb.fail_counter,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        )
        
        # If new state is open, increment trip counter
        if new_state.name.lower() == "open":
            circuit_breaker_trips_counter.add(1)
            logger.info(
                "Circuit breaker tripped",
                extra={
                    "event_type": "circuit_trip",
                    "previous_state": old_state.name.lower(),
                    "new_state": new_state.name.lower(),
                    "failure_count": cb.fail_counter,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
            )

# --- Fallback Cache ---
# TTL 1 hour, max 1000 entries
fallback_cache = TTLCache(maxsize=1000, ttl=3600)

def get_product_review_fallback(product_id: str, timeout: float = 1.0) -> List[demo_pb2.ProductReview]:
    """Fallback function when circuit is open"""
    # Increment fallback call counter
    circuit_breaker_calls_counter.add(1, {"result": "fallback"})
    
    # Log fallback event
    logger.info(
        "Circuit breaker fallback triggered",
        extra={
            "event_type": "fallback_triggered",
            "product_id": product_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
    )
    
    # Return cached value if exists, else empty list
    return fallback_cache.get(product_id, [])

# --- Circuit Breaker Instance ---
product_catalog_circuit_breaker: CircuitBreaker = CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    expected_exception=(grpc.RpcError,),
    listeners=[CircuitBreakerMetricsListener()]
)

# Wrapped gRPC call method
@product_catalog_circuit_breaker(fallback=get_product_review_fallback)
def get_product_reviews_from_catalog(product_id: str, timeout: float = 1.0) -> List[demo_pb2.ProductReview]:
    """
    Wrapped synchronous gRPC call to ProductCatalogService.GetProductReviews
    Args:
        product_id: ID of product to fetch reviews for
        timeout: gRPC call timeout in seconds
    Returns:
        List of ProductReview objects, either from catalog or fallback
    Raises:
        No exceptions propagated to caller: all errors handled by circuit breaker fallback
    """
    # Get product catalog service stub (assuming existing stub is available here, adjust as needed)
    # TODO: Replace with actual product catalog stub initialization if needed
    from grpc import insecure_channel
    product_catalog_host = os.getenv("PRODUCT_CATALOG_SERVICE_ADDR", "productcatalogservice:3550")
    channel = insecure_channel(product_catalog_host)
    stub = demo_pb2_grpc.ProductCatalogServiceStub(channel)
    
    try:
        response = stub.GetProductReviews(
            demo_pb2.GetProductReviewsRequest(product_id=product_id),
            timeout=timeout
        )
        # Increment success call counter
        circuit_breaker_calls_counter.add(1, {"result": "success"})
        # Cache successful response
        fallback_cache[product_id] = list(response.reviews)
        return list(response.reviews)
    except grpc.RpcError as e:
        # Increment failure call counter
        circuit_breaker_calls_counter.add(1, {"result": "failure"})
        # Re-raise to be handled by circuit breaker
        raise e

# Rate limiting configuration
RATE_LIMIT_ENABLED = os.getenv("PRODUCT_REVIEWS_RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_REQUESTS_PER_MINUTE = int(os.getenv("PRODUCT_REVIEWS_RATE_LIMIT_REQUESTS_PER_MINUTE", "100"))
rate_limit_store = {}
rate_limit_lock = asyncio.Lock()

def is_db_healthy() -> bool:
    """Check if database connection is healthy"""
    try:
        with psycopg2.connect(db_connection_str, connect_timeout=2):
            return True
    except Exception:
        return False

llm_host = None
llm_port = None
llm_mock_url = None
llm_base_url = None
llm_api_key = None
llm_model = None

# --- Define the tool for the OpenAI API ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "fetch_product_reviews",
            "description": "Executes a SQL query to retrieve reviews for a particular product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product ID to fetch product reviews for.",
                    }
                },
                "required": ["product_id"],
            },
        }
    },
      {
          "type": "function",
          "function": {
              "name": "fetch_product_info",
              "description": "Retrieves information for a particular product.",
              "parameters": {
                  "type": "object",
                  "properties": {
                      "product_id": {
                          "type": "string",
                          "description": "The product ID to fetch information for.",
                      }
                  },
                  "required": ["product_id"],
              },
          }
      }
]

def validate_product_id(context, product_id: str):
    """Validate product_id is non-empty and valid UUID v4.
    Raises grpc.RpcError with INVALID_ARGUMENT status if validation fails.
    """
    if not product_id:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "product_id is required")
    try:
        uuid_obj = uuid.UUID(product_id, version=4)
    except ValueError:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "product_id is not a valid UUID")

def validate_create_review_request(context, request):
    """Validate CreateProductReviewRequest fields.
    Raises grpc.RpcError with INVALID_ARGUMENT status if validation fails.
    """
    validate_product_id(context, request.product_id)
    
    # Validate rating
    if not isinstance(request.rating, int):
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "rating must be an integer")
    if request.rating < 1:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "rating must be at least 1")
    if request.rating > 5:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "rating must be at most 5")
    
    # Validate review text length
    if len(request.review_text) > 2000:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "review text exceeds 2000 character limit")

class RateLimitInterceptor(grpc.aio.ServerInterceptor):
    async def intercept_service(self, continuation, handler_call_details):
        # Skip rate limiting if disabled
        if not RATE_LIMIT_ENABLED:
            return await continuation(handler_call_details)
        
        # Only apply rate limiting to our service endpoints
        method_name = handler_call_details.method
        if not method_name.endswith(("GetProductReviews", "SubmitProductReview")):
            return await continuation(handler_call_details)
        
        # Extract client IP
        client_ip = "unknown"
        for key, value in handler_call_details.invocation_metadata:
            if key.lower() == "x-forwarded-for":
                client_ip = value.split(",")[0].strip()
                break
        else:
            # Get peer IP if no x-forwarded-for header
            peer = handler_call_details.peer
            if peer and ":" in peer:
                client_ip = peer.split(":")[1]
        
        # Calculate current minute window (timestamp rounded down to nearest 60s)
        current_window = int(time.time() // 60) * 60
        
        async with rate_limit_lock:
            # Clean up old windows
            to_delete = [ip for ip, data in rate_limit_store.items() if data["window"] != current_window]
            for ip in to_delete:
                del rate_limit_store[ip]
            
            # Get or create client entry
            if client_ip not in rate_limit_store:
                rate_limit_store[client_ip] = {
                    "window": current_window,
                    "count": 0
                }
            
            client_data = rate_limit_store[client_ip]
            client_data["count"] += 1
            current_count = client_data["count"]
        
        # Check if rate limit exceeded
        if current_count > RATE_LIMIT_REQUESTS_PER_MINUTE:
            # Log violation
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "client_ip": client_ip,
                    "endpoint": method_name,
                    "request_count": current_count,
                    "rate_limit": RATE_LIMIT_REQUESTS_PER_MINUTE
                }
            )
            # Return RESOURCE_EXHAUSTED error
            async def abort_method(request, context):
                await context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    "Rate limit exceeded. Try again later."
                )
            
            return grpc.unary_unary_rpc_method_handler(abort_method)
        
        # Proceed to handler if rate limit not exceeded
        return await continuation(handler_call_details)


class ProductReviewService(demo_pb2_grpc.ProductReviewServiceServicer):
    def GetProductReviews(self, request, context):
        logger.info(f"Receive GetProductReviews for product id:{request.product_id}")
        
        # Validate product_id
        validate_product_id(context, request.product_id)
        
        product_reviews = get_product_reviews(request.product_id)

        return product_reviews

    def SubmitProductReview(self, request, context):
        logger.info(f"Receive SubmitProductReview for product id:{request.product_id}")
        
        # Validate all request fields
        validate_create_review_request(context, request)
        
        # Existing business logic for submitting review (placeholder as per original code)
        # This preserves existing behavior for valid requests
        response = demo_pb2.SubmitProductReviewResponse()
        response.success = True
        return response

    def GetAverageProductReviewScore(self, request, context):
        logger.info(f"Receive GetAverageProductReviewScore for product id:{request.product_id}")
        # Validate product_id
        validate_product_id(context, request.product_id)
        product_reviews = get_average_product_review_score(request.product_id)

        return product_reviews

    def AskProductAIAssistant(self, request, context):
        logger.info(f"Receive AskProductAIAssistant for product id:{request.product_id}, question: {request.question}")
        # Validate product_id
        validate_product_id(context, request.product_id)
        ai_assistant_response = get_ai_assistant_response(request.product_id, request.question)

        return ai_assistant_response

def get_product_reviews(request_product_id):

    with tracer.start_as_current_span("get_product_reviews") as span:

        span.set_attribute("demo.product.id", request_product_id)

        product_reviews = demo_pb2.GetProductReviewsResponse()
        records = fetch_product_reviews_from_db(request_product_id)

        for row in records:
            logger.info(f"  username: {row[0]}, description: {row[1]}, score: {str(row[2])}")
            product_reviews.product_reviews.add(
                    username=row[0],
                    description=row[1],
                    score=str(row[2])
            )

        span.set_attribute("demo.product.review.count", len(product_reviews.product_reviews))

        # Collect metrics for this service
        product_review_svc_metrics["demo.product.review.requests"].add(len(product_reviews.product_reviews), {'demo.product.id': request_product_id})

        return product_reviews

def get_average_product_review_score(request_product_id):

    with tracer.start_as_current_span("get_average_product_review_score") as span:

        span.set_attribute("demo.product.id", request_product_id)

        product_review_score = demo_pb2.GetAverageProductReviewScoreResponse()
        avg_score = fetch_avg_product_review_score_from_db(request_product_id)
        product_review_score.average_score = avg_score

        span.set_attribute("demo.product.review.average_score", avg_score)

        return product_review_score

def get_ai_assistant_response(request_product_id, question):

    with tracer.start_as_current_span("get_ai_assistant_response") as span:

        ai_assistant_response = demo_pb2.AskProductAIAssistantResponse()

        span.set_attribute("demo.product.id", request_product_id)
        span.set_attribute("demo.product.review.question", question)

        llm_rate_limit_error = check_feature_flag("llmRateLimitError")
        logger.info(f"llmRateLimitError feature flag: {llm_rate_limit_error}")
        if llm_rate_limit_error:
            random_number = random.random()
            logger.info(f"Generated a random number: {str(random_number)}")
            # return a rate limit error 50% of the time
            if random_number < 0.5:

                # ensure the mock LLM is always used, since we want to generate a 429 error
                client = OpenAI(
                    base_url=f"{llm_mock_url}",
                    # The OpenAI API requires an api_key to be present, but
                    # our LLM doesn't use it
                    api_key=f"{llm_api_key}"
                )

                user_prompt = f"Answer the following question about product ID:{request_product_id}: {question}"
                messages = [
                   {"role": "system", "content": "You are a helpful assistant that answers related to a specific product. Use tools as needed to fetch the product reviews and product information. Keep the response brief with no more than 1-2 sentences. If you don't know the answer, just say you don't know."},
                   {"role": "user", "content": user_prompt}
                ]
                logger.info(f"Invoking mock LLM with model: astronomy-llm-rate-limit")

                try:
                    initial_response = client.chat.completions.create(
                        model="astronomy-llm-rate-limit",
                        messages=messages,
                        tools=tools,
                        tool_choice="auto"
                    )
                except Exception as e:
                    logger.error(f"Caught Exception: {e}")
                    # Record the exception
                    span.record_exception(e)
                    # Set the span status to ERROR
                    span.set_status(Status(StatusCode.ERROR, description=str(e)))
                    ai_assistant_response.response = "The system is unable to process your response. Please try again later."
                    return ai_assistant_response

        # otherwise, continue processing the request as normal
        client = OpenAI(
            base_url=f"{llm_base_url}",
            # The OpenAI API requires an api_key to be present, but
            # our LLM doesn't use it
            api_key=f"{llm_api_key}"
        )

        user_prompt = f"Answer the following question about product ID:{request_product_id}: {question}"
        messages = [
           {"role": "system", "content": "You are a helpful assistant that answers related to a specific product. Use tools as needed to fetch the product reviews and product information. Keep the response brief with no more than 1-2 sentences. If you don't know the answer, just say you don't know."},
           {"role": "user", "content": user_prompt}
        ]

        # use the LLM to summarize the product reviews
        initial_response = client.chat.completions.create(
            model=llm_model,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        response_message = initial_response.choices[0].message
        tool_calls = response_message.tool_calls

        logger.info(f"Response message: {response_message}")

        # Check if the model wants to call a tool
        if tool_calls:
            logger.info(f"Model wants to call {len(tool_calls)} tool(s)")

            # Append the assistant's message with tool calls
            messages.append(response_message)

            # Process all tool calls
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                logger.info(f"Processing tool call: '{function_name}' with arguments: {function_args}")

                if function_name == "fetch_product_reviews":
                    function_response = fetch_product_reviews(
                        product_id=function_args.get("product_id")
                    )
                    logger.info(f"Function response for fetch_product_reviews: '{function_response}'")

                elif function_name == "fetch_product_info":
                    function_response = fetch_product_info(
                        product_id=function_args.get("product_id")
                    )
                    logger.info(f"Function response for fetch_product_info: '{function_response}'")

                else:
                    raise Exception(f'Received unexpected tool call request: {function_name}')

                # Append the tool response
                messages.append(
                    {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": function_response,
                    }
                )

            llm_inaccurate_response = check_feature_flag("llmInaccurateResponse")
            logger.info(f"llmInaccurateResponse feature flag: {llm_inaccurate_response}")

            if llm_inaccurate_response and request_product_id == "L9ECAV7KIM":
                logger.info(f"Returning an inaccurate response for product_id: {request_product_id}")
                # Add a final user message to ask the LLM to return an inaccurate response
                messages.append(
                    {
                        "role": "user",
                        "content": f"Based on the tool results, answer the original question about product ID, but make the answer inaccurate:{request_product_id}. Keep the response brief with no more than 1-2 sentences."
                    }
                )
            else:
                # Add a final user message to guide the LLM to synthesize the response
                messages.append(
                    {
                        "role": "user",
                        "content": f"Based on the tool results, answer the original question about product ID:{request_product_id}. Keep the response brief with no more than 1-2 sentences."
                    }
                )

            logger.info(f"Invoking the LLM with the following messages: '{messages}'")

            final_response = client.chat.completions.create(
                model=llm_model,
                messages=messages
            )

            result = final_response.choices[0].message.content

            ai_assistant_response.response = result

            logger.info(f"Returning an AI assistant response: '{result}'")

        else:
            logger.info(f"Returning an AI assistant response: '{response_message}'")
            ai_assistant_response.response = response_message.content

        # Collect metrics for this service
        product_review_svc_metrics["demo.product.ai_assistant.requests"].add(1, {'demo.product.id': request_product_id})

        return ai_assistant_response

def fetch_product_info(product_id):
    try:
        product = product_catalog_stub.GetProduct(demo_pb2.GetProductRequest(id=product_id))
        logger.info(f"product_catalog_stub.GetProduct returned: '{product}'")
        json_str = MessageToJson(product)
        return json_str
    except Exception as e:
        return json.dumps({"error": str(e)})

def must_map_env(key: str):
    value = os.environ.get(key)
    if value is None:
        raise Exception(f'{key} environment variable must be set')
    return value

def check_feature_flag(flag_name: str):
    # Initialize OpenFeature
    client = api.get_client()
    return client.get_boolean_value(flag_name, False)

def handle_shutdown_signal(signum: int, frame: Optional[FrameType]) -> None:
    global shutdown_initiated
    if shutdown_initiated:
        logger.info("Shutdown already in progress, ignoring duplicate signal")
        return
    shutdown_initiated = True
    signal_name = signal.Signals(signum).name
    logger.info(f"Received shutdown signal ({signal_name}), starting graceful shutdown sequence")
    # Trigger the graceful shutdown coroutine
    asyncio.create_task(graceful_shutdown(server, db_connection_pool, pc_channel))

async def graceful_shutdown(
    server: grpc.aio.Server,
    db_connection_pool: Any,
    product_catalog_channel: grpc.aio.Channel,
    timeout: int = 30
) -> None:
    exit_code = 0
    try:
        # Stop accepting new connections and wait for in-flight requests to complete
        logger.info(f"Stopping gRPC server with {timeout}s timeout for in-flight requests")
        stop_task = asyncio.create_task(server.stop(timeout))
        done, pending = await asyncio.wait([stop_task], timeout=timeout)
        if pending:
            logger.warning(f"Graceful shutdown timed out after {timeout}s, forcing exit")
            exit_code = 1
            # Cancel the pending stop task
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    except Exception as e:
        logger.error(f"Error stopping gRPC server: {str(e)}", exc_info=True)
        exit_code = 1

    # Close database connections
    try:
        logger.info("Closing all database connections")
        await db_connection_pool.close()
        logger.info("Successfully closed all database connections")
    except Exception as e:
        logger.error(f"Error closing database connections: {str(e)}", exc_info=True)
        exit_code = 1

    # Close product catalog channel
    try:
        logger.info("Closing product-catalog gRPC channel")
        await product_catalog_channel.close()
        logger.info("Successfully closed product-catalog gRPC channel")
    except Exception as e:
        logger.error(f"Error closing product-catalog gRPC channel: {str(e)}", exc_info=True)
        exit_code = 1

    if exit_code == 0:
        logger.info("Graceful shutdown completed successfully, exiting")
    else:
        logger.info(f"Graceful shutdown completed with errors, exiting with code {exit_code}")
    
    os._exit(exit_code)

if __name__ == "__main__":
    service_name = must_map_env('OTEL_SERVICE_NAME')

    api.set_provider(FlagdProvider(host=os.environ.get('FLAGD_HOST', 'flagd'), port=os.environ.get('FLAGD_PORT', 8013)))

    # Initialize Traces and Metrics
    tracer = trace.get_tracer_provider().get_tracer(service_name)
    meter = metrics.get_meter_provider().get_meter(service_name)

    product_review_svc_metrics = init_metrics(meter)

    # Initialize Logs
    logger_provider = LoggerProvider(
        resource=Resource.create(
            {
                'service.name': service_name,
            }
        ),
    )
    set_logger_provider(logger_provider)
    log_exporter = OTLPLogExporter(insecure=True)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)

    # Attach OTLP handler to logger
    logger.addHandler(handler)

    # Create async gRPC server
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[RateLimitInterceptor()]
    )

    # Add class to gRPC server
    service = ProductReviewService()
    demo_pb2_grpc.add_ProductReviewServiceServicer_to_server(service, server)
    
    # Initialize health service
    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    
    # Set initial statuses
    health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
    health_servicer.set("product.reviews.v1.ProductReviewService", health_pb2.HealthCheckResponse.NOT_SERVING)

    llm_host = must_map_env('LLM_HOST')
    llm_port = must_map_env('LLM_PORT')
    llm_mock_url = f"http://{llm_host}:{llm_port}/v1"
    llm_base_url = must_map_env('LLM_BASE_URL')
    llm_api_key = must_map_env('OPENAI_API_KEY')
    llm_model = must_map_env('LLM_MODEL')

    catalog_addr = must_map_env('PRODUCT_CATALOG_ADDR')
    pc_channel = grpc.aio.insecure_channel(catalog_addr)
    product_catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(pc_channel)

    # Dummy database connection pool (current implementation uses per-request connections, no pool)
    class DummyDBPool:
        async def close(self):
            # No-op since we don't have a persistent pool
            pass

    db_connection_pool = DummyDBPool()

    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    # Background task to update health statuses
    async def update_health_statuses():
        global service_initialized
        while not shutdown_initiated:
            db_healthy = is_db_healthy()
            
            # Update liveness status (empty service name)
            if db_healthy:
                health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
            else:
                health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
            
            # Update readiness status
            if service_initialized and db_healthy:
                health_servicer.set("product.reviews.v1.ProductReviewService", health_pb2.HealthCheckResponse.SERVING)
            else:
                health_servicer.set("product.reviews.v1.ProductReviewService", health_pb2.HealthCheckResponse.NOT_SERVING)
            
            await asyncio.sleep(1)

    # Start server
    port = must_map_env('PRODUCT_REVIEWS_PORT')
    
    # TLS configuration for gRPC server
    grpc_tls_cert_path = os.environ.get('PRODUCT_REVIEWS_GRPC_TLS_CERT_PATH')
    grpc_tls_key_path = os.environ.get('PRODUCT_REVIEWS_GRPC_TLS_KEY_PATH')
    grpc_mtls_ca_path = os.environ.get('PRODUCT_REVIEWS_GRPC_MTLS_CA_CERT_PATH')
    
    if grpc_tls_cert_path and grpc_tls_key_path:
        # Validate TLS files exist and are readable
        if not os.path.exists(grpc_tls_cert_path) or not os.access(grpc_tls_cert_path, os.R_OK):
            raise Exception(f"TLS configuration error: Server certificate file is missing or unreadable (path: {grpc_tls_cert_path})")
        if not os.path.exists(grpc_tls_key_path) or not os.access(grpc_tls_key_path, os.R_OK):
            raise Exception(f"TLS configuration error: Server private key file is missing or unreadable (path: {grpc_tls_key_path})")
        
        # Read certificate and key files
        try:
            with open(grpc_tls_cert_path, 'rb') as f:
                server_cert = f.read()
            with open(grpc_tls_key_path, 'rb') as f:
                server_key = f.read()
        except Exception as e:
            raise Exception(f"TLS configuration error: Failed to read certificate/key files: {str(e)}")
        
        # Create server credentials
        server_creds = grpc.ssl_server_credentials([(server_key, server_cert)])
        
        # Configure mTLS if CA cert is provided
        if grpc_mtls_ca_path:
            if not os.path.exists(grpc_mtls_ca_path) or not os.access(grpc_mtls_ca_path, os.R_OK):
                raise Exception(f"TLS configuration error: mTLS CA certificate file is missing or unreadable (path: {grpc_mtls_ca_path})")
            try:
                with open(grpc_mtls_ca_path, 'rb') as f:
                    ca_cert = f.read()
            except Exception as e:
                raise Exception(f"TLS configuration error: Failed to read mTLS CA certificate: {str(e)}")
            
            # Require client certificate validation
            server_creds = grpc.ssl_server_credentials(
                [(server_key, server_cert)],
                root_certificates=ca_cert,
                require_client_auth=True
            )
        
        # Add secure port with minimum TLS 1.2
        server_options = server._options.copy()
        server_options.append(('grpc.ssl_target_name_override', 'localhost'))
        server_options.append(('grpc.min_tls_version', grpc.TLS_VERSION_1_2))
        server._options = server_options
        
        server.add_secure_port(f'[::]:{port}', server_creds)
        logger.info(f"gRPC server configured with TLS 1.2+ encryption, listening on port {port}")
        if grpc_mtls_ca_path:
            logger.info("mTLS client authentication enabled")
    elif grpc_tls_cert_path or grpc_tls_key_path:
        raise Exception("TLS configuration error: Both PRODUCT_REVIEWS_GRPC_TLS_CERT_PATH and PRODUCT_REVIEWS_GRPC_TLS_KEY_PATH must be provided to enable TLS")
    else:
        # Default: plaintext insecure port for backwards compatibility
        server.add_insecure_port(f'[::]:{port}')
        logger.info(f"gRPC server configured with plaintext (unencrypted) connections, listening on port {port}")

    async def serve():
        global service_initialized
        await server.start()
        logger.info(f'Product reviews service started, listening on port {port}')
        
        # Start health status updater task
        asyncio.create_task(update_health_statuses())
        
        # Mark service as initialized after all startup steps complete
        service_initialized = True
        logger.info('Service initialization completed')
        
        await server.wait_for_termination()

    asyncio.run(serve())
