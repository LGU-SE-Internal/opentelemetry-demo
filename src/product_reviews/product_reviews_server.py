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

# Rate limiting imports
from limits import Limiter, RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import TokenBucketRateLimiter as LimitsTokenBucket
from datetime import datetime
import ipaddress

# OpenAI
from openai import OpenAI

from google.protobuf.json_format import MessageToJson, MessageToDict

# Global shutdown flag
shutdown_initiated = False
shutdown_event = asyncio.Event()
service_initialized = False
logger = logging.getLogger('main')
import psycopg2
import time
from dataclasses import dataclass

server: Optional[grpc.aio.Server] = None
db_connection_pool: Optional[Any] = None
otel_providers: Optional[OTelProviders] = None

@dataclass
class OTelProviders:
    trace: Any
    metric: Any
    log: Any

# Graceful shutdown implementation
def handle_shutdown_signal(signum: int, frame: Optional[FrameType]) -> None:
    """Handle SIGINT/SIGTERM signals to trigger graceful shutdown"""
    global shutdown_initiated, server, db_connection_pool, otel_providers
    if shutdown_initiated:
        logger.warning("Received second shutdown signal, forcing immediate exit")
        exit(1)
    
    signal_name = signal.Signals(signum).name
    logger.info(f"Shutdown initiated by {signal_name} signal")
    shutdown_initiated = True
    
    if server is None or db_connection_pool is None or otel_providers is None:
        logger.warning("Service not fully initialized, exiting immediately")
        exit(0)
    
    # Run graceful shutdown synchronously
    exit_code = run_graceful_shutdown(server, db_connection_pool, otel_providers)
    exit(exit_code)

def run_graceful_shutdown(server: grpc.Server, db_pool: Any, otel_providers: OTelProviders) -> int:
    """
    Execute graceful shutdown sequence:
    1. Stop accepting new requests
    2. Wait up to 10s for in-flight requests to complete
    3. Clean up database connections
    4. Flush and shut down OTel providers
    Returns exit code 0 on success, 1 on timeout/failure
    """
    import asyncio
    
    # Step 1: Stop accepting new connections
    logger.info("Stopping gRPC server from accepting new connections")
    stop_future = server.stop(grace=10)
    
    # Step 2: Wait for in-flight requests to complete (max 10s)
    logger.info("Waiting for in-flight requests to complete (timeout: 10s)")
    try:
        asyncio.run(asyncio.wait_for(stop_future, timeout=10))
        logger.info("All in-flight requests completed successfully")
        timeout_occurred = False
    except asyncio.TimeoutError:
        logger.warning("Shutdown timeout elapsed, forcibly terminating remaining requests")
        asyncio.run(server.stop(grace=0))
        timeout_occurred = True
    
    # Step 3: Clean up database connections
    logger.info("Closing all open database connections")
    if hasattr(db_pool, 'closeall'):
        db_pool.closeall()
    logger.info("Database connections closed successfully")
    
    # Step 4: Flush and shut down OTel providers
    # Trace provider
    if hasattr(otel_providers.trace, 'force_flush'):
        logger.info("Flushing OpenTelemetry trace provider")
        otel_providers.trace.force_flush(timeout_millis=5000)
    if hasattr(otel_providers.trace, 'shutdown'):
        otel_providers.trace.shutdown()
    logger.info("OpenTelemetry trace provider flushed and shut down")
    
    # Metric provider
    if hasattr(otel_providers.metric, 'force_flush'):
        logger.info("Flushing OpenTelemetry metric provider")
        otel_providers.metric.force_flush(timeout_millis=5000)
    if hasattr(otel_providers.metric, 'shutdown'):
        otel_providers.metric.shutdown()
    logger.info("OpenTelemetry metric provider flushed and shut down")
    
    # Log provider
    if hasattr(otel_providers.log, 'force_flush'):
        logger.info("Flushing OpenTelemetry log provider")
        otel_providers.log.force_flush(timeout_millis=5000)
    if hasattr(otel_providers.log, 'shutdown'):
        otel_providers.log.shutdown()
    logger.info("OpenTelemetry log provider flushed and shut down")
    
    logger.info("Graceful shutdown completed")
    return 1 if timeout_occurred else 0

# --- Rate Limiting Implementation ---
class TokenBucketRateLimiter:
    def __init__(self, capacity: int, refill_rate_per_minute: float) -> None:
        """
        Args:
            capacity: Maximum number of requests allowed in a burst
            refill_rate_per_minute: Number of tokens added to the bucket per minute
        """
        self.capacity = capacity
        self.refill_rate_per_minute = refill_rate_per_minute
        self._is_unlimited = capacity == 0 or refill_rate_per_minute == 0
        if not self._is_unlimited:
            self._limiter = Limiter(
                storage=MemoryStorage(),
                strategy=LimitsTokenBucket()
            )
            self._rate = RateLimitItemPerMinute(int(refill_rate_per_minute))
    
    def allow_request(self) -> bool:
        """Returns True if request is allowed, False if rate limit exceeded"""
        if self._is_unlimited:
            return True
        return self._limiter.check(self._rate, "global")

# --- Rate Limit gRPC Interceptor ---
class RateLimitInterceptor(grpc.ServerInterceptor):
    def __init__(
        self,
        endpoint_limits: dict[str, TokenBucketRateLimiter],
        metrics_client,
        logger: logging.Logger
    ) -> None:
        self.endpoint_limits = endpoint_limits
        self.metrics_client = metrics_client
        self.logger = logger
    
    def intercept_service(self, continuation, handler_call_details, context):
        method = handler_call_details.method
        limiter = self.endpoint_limits.get(method)
        
        if not limiter or limiter.allow_request():
            return continuation(handler_call_details)
        
        # Rate limit exceeded
        self.metrics_client.product_reviews_rate_limited_requests_total.labels(
            endpoint=method
        ).add(1)
        
        # Extract client IP
        peer = handler_call_details.peer()
        remote_addr = "unknown"
        if peer.startswith("ipv4:") or peer.startswith("ipv6:"):
            parts = peer.split(":", 2)
            if len(parts) >= 2:
                remote_addr = parts[1]
        
        # Emit structured log
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "endpoint": method,
            "remote_addr": remote_addr,
            "rate_limit": limiter.refill_rate_per_minute,
            "status": "rejected"
        }
        self.logger.info(log_data)
        
        # Set gRPC error status
        context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
        context.set_details("Rate limit exceeded. Try again later.")
        return None

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
    
    # Initialize rate limiters
    ENDPOINTS = {
        "ListProductReviews": "/opentelemetry.demo.DemoService/ListProductReviews",
        "CreateProductReview": "/opentelemetry.demo.DemoService/CreateProductReview",
        "GetProductReviewSummary": "/opentelemetry.demo.DemoService/GetProductReviewSummary",
        "DeleteProductReview": "/opentelemetry.demo.DemoService/DeleteProductReview",
    }
    
    # Get default limit from env
    default_limit = int(os.environ.get("PRODUCT_REVIEWS_RATE_LIMIT_DEFAULT", "0"))
    
    endpoint_limits = {}
    for endpoint_name, full_method_name in ENDPOINTS.items():
        env_var = f"PRODUCT_REVIEWS_RATE_LIMIT_{endpoint_name.upper()}"
        limit = int(os.environ.get(env_var, str(default_limit)))
        if limit > 0:
            endpoint_limits[full_method_name] = TokenBucketRateLimiter(
                capacity=limit,
                refill_rate_per_minute=limit
            )
        else:
            endpoint_limits[full_method_name] = TokenBucketRateLimiter(
                capacity=0,
                refill_rate_per_minute=0
            )
    
    # Create rate limit interceptor
    rate_limit_interceptor = RateLimitInterceptor(
        endpoint_limits=endpoint_limits,
        metrics_client=product_review_svc_metrics,
        logger=logger
    )

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
        interceptors=[rate_limit_interceptor]
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

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    logger.info("Registered SIGINT/SIGTERM signal handlers for graceful shutdown")

    async def serve():
        global service_initialized
        await server.start()
        logger.info(f'Product reviews service started, listening on port {port}')
        
        # Start health status updater task
        asyncio.create_task(update_health_statuses())
        
        # Mark service as initialized after all startup steps complete
        service_initialized = True
        logger.info('Service initialization completed')
        
        # Wait for shutdown event
        await shutdown_event.wait()
        
        # Get OTel providers instances
        from opentelemetry import trace, metrics
        from opentelemetry._logs import get_logger_provider
        
        otel_providers = OTelProviders(
            trace_provider=trace.get_tracer_provider(),
            metric_provider=metrics.get_meter_provider(),
            log_provider=get_logger_provider()
        )
        
        # Run graceful shutdown
        exit_code = run_graceful_shutdown(server, None, otel_providers)
        exit(exit_code)

    asyncio.run(serve())
