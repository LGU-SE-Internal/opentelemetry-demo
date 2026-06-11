#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


"""Recommendation service gRPC server implementation for the OpenTelemetry demo."""

# Python
import os
import random
import json
import re
import uuid
import signal
import time
from concurrent import futures
import threading
from flask import Flask, Response

# Pip
import grpc
import pybreaker
from opentelemetry import trace, metrics
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from openfeature import api
from openfeature.contrib.provider.flagd import FlagdProvider

from openfeature.contrib.hook.opentelemetry import TracingHook

from limits import Limiter, RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter

# Local
import logging
import demo_pb2
import demo_pb2_grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

from metrics import (
    init_metrics
)
# Health check functions
def check_product_catalog_health():
    """Check if product catalog service is reachable and responsive"""
    try:
        if not product_catalog_client:
            return False, "Product catalog client not initialized"
        # Make a simple ListProducts call to verify connectivity
        response = list_products_with_retry(product_catalog_client, demo_pb2.Empty())
        if response and hasattr(response, 'products'):
            return True, None
        return False, "Product catalog returned invalid response"
    except Exception as e:
        return False, f"Product catalog service unreachable: {str(e)}"

def check_flagd_health():
    """Check if flagd service is reachable and responsive"""
    try:
        provider = api.get_provider()
        if not provider:
            return False, "Flagd provider not initialized"
        # Simple metadata check to verify connection
        metadata = provider.get_metadata()
        if metadata:
            return True, None
        return False, "Flagd returned invalid metadata"
    except Exception as e:
        return False, f"Flagd service unreachable: {str(e)}"

# Create Flask app for HTTP health endpoints
app = Flask(__name__)

@app.route('/health/live', methods=['GET'])
def liveness_check():
    """Liveness check endpoint - always returns 200 OK when process is running"""
    return Response("OK", status=200, content_type="text/plain")

@app.route('/health/ready', methods=['GET'])
def readiness_check():
    """Readiness check endpoint - returns 200 only if all dependencies are healthy"""
    # Check product catalog health
    pc_healthy, pc_error = check_product_catalog_health()
    if not pc_healthy:
        return Response(f"Unavailable: {pc_error}", status=503, content_type="text/plain")
    
    # Check flagd health
    flagd_healthy, flagd_error = check_flagd_health()
    if not flagd_healthy:
        return Response(f"Unavailable: {flagd_error}", status=503, content_type="text/plain")
    
    return Response("OK", status=200, content_type="text/plain")

def run_http_server():
    """Run the Flask HTTP server on port 8080"""
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

cached_ids = []
first_run = True

# Retry configuration from environment variables
RETRY_MAX_ATTEMPTS = int(os.environ.get('RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_ATTEMPTS', '3'))
RETRY_ATTEMPTS = RETRY_MAX_ATTEMPTS # Alias for tests
RETRY_INITIAL_BACKOFF_MS = int(os.environ.get('RECOMMENDATION_SERVICE_PRODUCT_CATALOG_INITIAL_RETRY_BACKOFF_MS', '100'))
RETRY_BACKOFF_MULTIPLIER = 2.0  # Exponential multiplier per spec
RETRY_MAX_BACKOFF_MS = int(os.environ.get('RECOMMENDATION_SERVICE_PRODUCT_CATALOG_MAX_RETRY_BACKOFF_MS', '2000'))

# Eligible retry status codes
RETRYABLE_STATUS_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.INTERNAL,
    grpc.StatusCode.RESOURCE_EXHAUSTED
}

# Idempotent product catalog methods that can be retried
IDEMPOTENT_PRODUCT_CATALOG_METHODS = {"GetProduct", "ListProducts", "SearchProducts"}

# Global metrics references (will be set during init)
retry_attempts_counter = None
retry_failures_counter = None

# Structured logger reference (will be set during init)
logger = None

from typing import Callable, Any
def retry_product_catalog_call(
    func: Callable[..., Any],
    *args,
    **kwargs
) -> Any:
    """
    Wraps product catalog gRPC calls with exponential backoff retry logic.
    Args:
        func: gRPC method to call
        *args: Positional arguments for the gRPC method
        **kwargs: Keyword arguments for the gRPC method
    Returns:
        Result of the gRPC call if successful
    Raises:
        Original gRPC error if retries are exhausted or error is non-retryable
    """
    method_name = func.__name__
    
    # No retries if max attempts is 0 or method is not idempotent
    if RETRY_MAX_ATTEMPTS <= 0 or method_name not in IDEMPOTENT_PRODUCT_CATALOG_METHODS:
        return func(*args, **kwargs)
    
    attempt = 0
    backoff_ms = RETRY_INITIAL_BACKOFF_MS
    
    while True:
        try:
            return func(*args, **kwargs)
        except grpc.RpcError as e:
            status_code = e.code()
            # Check if error is retryable
            if status_code not in RETRYABLE_STATUS_CODES:
                raise e
            
            attempt += 1
            if attempt > RETRY_MAX_ATTEMPTS:
                # Log retry exhausted
                if logger:
                    logger.error(
                        f"Exhausted all {RETRY_MAX_ATTEMPTS} retry attempts for product catalog method {method_name}",
                        extra={
                            "event": "product_catalog_retry_exhausted",
                            "method": method_name,
                            "error_type": status_code.name,
                            "total_attempts": attempt
                        }
                    )
                # Increment failure metric
                if retry_failures_counter:
                    retry_failures_counter.add(
                        1,
                        {
                            "method": method_name,
                            "error_type": status_code.name
                        }
                    )
                raise e
            
            # Increment retry attempt metric
            if retry_attempts_counter:
                retry_attempts_counter.add(
                    1,
                    {
                        "method": method_name,
                        "error_type": status_code.name
                    }
                )
            
            # Log retry attempt
            if logger:
                logger.info(
                    f"Retrying product catalog method {method_name} (attempt {attempt}/{RETRY_MAX_ATTEMPTS}) after {backoff_ms}ms backoff",
                    extra={
                        "event": "product_catalog_retry_attempt",
                        "method": method_name,
                        "error_type": status_code.name,
                        "attempt_number": attempt,
                        "backoff_duration_ms": backoff_ms
                    }
                )
            
            # Wait for backoff duration
            time.sleep(backoff_ms / 1000.0)
            
            # Calculate next backoff (exponential, capped at max)
            backoff_ms = min(backoff_ms * RETRY_BACKOFF_MULTIPLIER, RETRY_MAX_BACKOFF_MS)

# Circuit breaker configuration from environment variables
PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(os.environ.get('PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD', '5'))
PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT = int(os.environ.get('PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT', '30'))
PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS = int(os.environ.get('PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS', '3'))

# Circuit breaker state values for metric
CIRCUIT_BREAKER_STATE_CLOSED = 0
CIRCUIT_BREAKER_STATE_OPEN = 1
CIRCUIT_BREAKER_STATE_HALF_OPEN = 2

# Open circuit error message
CIRCUIT_OPEN_ERROR_MSG = "Product Catalog Service is temporarily unavailable: circuit breaker is open"

# Global product catalog client stub
product_catalog_client = None

# Circuit breaker listener to update metrics on state changes
class CircuitBreakerMetricsListener(pybreaker.CircuitBreakerListener):
    def __init__(self, metrics):
        self.metrics = metrics
        self._update_state_metric(pybreaker.STATE_CLOSED)

    def _update_state_metric(self, state):
        if state == pybreaker.STATE_CLOSED:
            value = CIRCUIT_BREAKER_STATE_CLOSED
        elif state == pybreaker.STATE_OPEN:
            value = CIRCUIT_BREAKER_STATE_OPEN
        elif state == pybreaker.STATE_HALF_OPEN:
            value = CIRCUIT_BREAKER_STATE_HALF_OPEN
        else:
            value = CIRCUIT_BREAKER_STATE_CLOSED
        self.metrics["product_catalog_circuit_breaker_state"].set(value)

    def state_change(self, cb, old_state, new_state):
        self._update_state_metric(new_state)
        if new_state == pybreaker.STATE_OPEN:
            self.metrics["product_catalog_circuit_breaker_trips_total"].add(1)

# Global circuit breaker instance (initialized after metrics are set up)
product_catalog_circuit_breaker = None
product_catalog_client = None
_circuit_breaker_initialized = False

def list_products_with_retry(client: demo_pb2_grpc.ProductCatalogServiceStub, request: demo_pb2.Empty, metadata = None, context = None) -> demo_pb2.ListProductsResponse:
    """
    Wraps ProductCatalogService.ListProducts gRPC call with exponential backoff retry logic.
    
    Args:
        client: gRPC stub for ProductCatalogService
        request: ListProductsRequest object
        metadata: Optional gRPC call metadata
        context: gRPC context object
    
    Returns:
        ListProductsResponse object from successful call
    
    Raises:
        gRPC error: Original error from final failed attempt, with additional context that max retries were exhausted
    """
    @product_catalog_circuit_breaker
    def _call_with_retry():
        import time
        
        attempt = 0
        last_error = None
        
        while attempt <= RETRY_MAX_ATTEMPTS:
            try:
                return client.ListProducts(request, metadata=metadata)
            except grpc.RpcError as e:
                last_error = e
                status_code = e.code()
                
                # Check if we should retry
                if status_code not in RETRYABLE_STATUS_CODES or attempt >= RETRY_MAX_ATTEMPTS:
                    break
                
                attempt += 1
                
                # Calculate backoff duration
                backoff_ms = min(
                    RETRY_INITIAL_BACKOFF_MS * (RETRY_BACKOFF_MULTIPLIER ** (attempt - 1)),
                    RETRY_MAX_BACKOFF_MS
                )
                backoff_sec = backoff_ms / 1000.0
                
                # Log the retry attempt
                logger.info(
                    f"ProductCatalog ListProducts retry attempt {attempt}/{RETRY_MAX_ATTEMPTS} after {status_code.name} error, backing off for {backoff_ms}ms",
                    extra={
                        "attempt_number": attempt,
                        "status_code": status_code.name,
                        "backoff_ms": backoff_ms,
                        "max_attempts": RETRY_MAX_ATTEMPTS
                    }
                )
                
                # Increment retry metric
                rec_svc_metrics["product_catalog_retry_attempts"].add(
                    1,
                    {
                        "status_code": status_code.name,
                        "attempt_number": str(attempt)
                    }
                )
                
                # Wait before retrying
                time.sleep(backoff_sec)
        
        # If we exhausted all retries, raise the last error with context
        if last_error is not None:
            context_message = f"Max retry attempts ({RETRY_MAX_ATTEMPTS}) exhausted for ProductCatalog ListProducts call: {last_error.details()}"
            # Augment the error details
            setattr(last_error, "_details", context_message)
            raise last_error
    try:
        return _call_with_retry()
    except pybreaker.CircuitBreakerError:
        if context:
            context.abort(grpc.StatusCode.UNAVAILABLE, "Product Catalog Service is temporarily unavailable: circuit breaker is open")
        else:
            raise grpc.RpcError(grpc.StatusCode.UNAVAILABLE, "Product Catalog Service is temporarily unavailable: circuit breaker is open")

class RecommendationService(demo_pb2_grpc.RecommendationServiceServicer):
    def ListRecommendations(self, request, context):
        span = trace.get_current_span()
        trace_id = format(span.get_span_context().trace_id, '016x') if span.is_recording() else "unknown"
        # Extract client IP from context
        peer = context.peer()
        client_ip = peer.split(':')[1] if peer and ':' in peer else "unknown"
        
        # Helper function to strip control characters
        def sanitize_string(s):
            if not s:
                return s
            # Remove ASCII control characters 0-31 and 127
            return re.sub(r'[\x00-\x1F\x7F]', '', s)
        
        # Sanitize all input parameters first
        sanitized_user_id = sanitize_string(request.user_id)
        sanitized_product_ids = [sanitize_string(pid) for pid in request.product_ids]
        
        # Validate user_id is present and valid UUID v4
        if not sanitized_user_id:
            error_msg = "user_id is required and must be a valid UUID v4"
            logger.error(
                f"invalid_request: field=user_id, error={error_msg}, client_ip={client_ip}, trace_id={trace_id}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        try:
            user_uuid = uuid.UUID(sanitized_user_id, version=4)
        except ValueError:
            error_msg = "user_id must be valid UUID v4"
            logger.error(
                f"invalid_request: field=user_id, error={error_msg}, client_ip={client_ip}, value={sanitized_user_id}, trace_id={trace_id}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
        # Validate product_ids list size <=100
        if len(sanitized_product_ids) > 100:
            error_msg = "product_ids list exceeds maximum allowed length of 100 entries"
            logger.error(
                f"invalid_request: field=product_ids, error={error_msg}, client_ip={client_ip}, count={len(sanitized_product_ids)}, trace_id={trace_id}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
        # Validate each product ID entry is non-empty
        for idx, product_id in enumerate(sanitized_product_ids):
            if not product_id:
                error_msg = f"product_ids entry at index {idx} is empty"
                logger.error(
                    f"invalid_request: field=product_ids[{idx}], error={error_msg}, client_ip={client_ip}, trace_id={trace_id}"
                )
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
        # Validate max_results bounds (optional field)
        max_results = getattr(request, 'max_results', 5)
        if max_results <= 0:  # use default 5 if not set or 0
            max_results = 5
        if max_results < 1 or max_results > 20:
            error_msg = "max_results must be between 1 and 20 (inclusive)"
            logger.error(
                f"invalid_request: field=max_results, error={error_msg}, client_ip={client_ip}, value={max_results}, trace_id={trace_id}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        result_size = max_results
        
        # Update request object with sanitized values for further processing
        request.user_id = sanitized_user_id
        # Clear existing product_ids and add sanitized ones
        del request.product_ids[:]
        request.product_ids.extend(sanitized_product_ids)
        
        prod_list = get_product_list(request.product_ids, result_size)
        span.set_attribute("demo.product.recommended.count", len(prod_list))
        logger.info(f"Receive ListRecommendations for product ids:{prod_list}")

        # build and return response
        response = demo_pb2.ListRecommendationsResponse()
        response.product_ids.extend(prod_list)

        # Collect metrics for this service
        rec_svc_metrics["demo.recommendation.requests"].add(len(prod_list), {'recommendation.type': 'catalog'})

        return response

    def Check(self, request, context):
        # Check if service name is either empty or "recommendationService"
        if request.service and request.service != "recommendationService":
            return health_pb2.HealthCheckResponse(
                status=health_pb2.HealthCheckResponse.SERVICE_UNKNOWN
            )
        
        # Check all dependencies
        pc_healthy, _ = check_product_catalog_health()
        flagd_healthy, _ = check_flagd_health()
        
        if pc_healthy and flagd_healthy:
            return health_pb2.HealthCheckResponse(
                status=health_pb2.HealthCheckResponse.SERVING
            )
        else:
            return health_pb2.HealthCheckResponse(
                status=health_pb2.HealthCheckResponse.NOT_SERVING
            )

    def Watch(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.UNIMPLEMENTED)


def get_product_list(request_product_ids, result_size=5):
    global first_run
    global cached_ids
    with tracer.start_as_current_span("get_product_list") as span:
        max_responses = result_size

        # Formulate the list of characters to list of strings
        request_product_ids_str = ''.join(request_product_ids)
        request_product_ids = request_product_ids_str.split(',')

        # Feature flag scenario - Cache Leak
        if check_feature_flag("recommendationCacheFailure"):
            span.set_attribute("demo.feature_flag.recommendation_cache", True)
            if random.random() < 0.5 or first_run:
                first_run = False
                span.set_attribute("demo.recommendation.cache_hit", False)
                logger.info("get_product_list: cache miss")
                cat_response = list_products_with_retry(product_catalog_stub, demo_pb2.Empty(), context=context)
                response_ids = [x.id for x in cat_response.products]
                cached_ids = cached_ids + response_ids
                cached_ids = cached_ids + cached_ids[:len(cached_ids) // 4]
                product_ids = cached_ids
            else:
                span.set_attribute("demo.recommendation.cache_hit", True)
                logger.info("get_product_list: cache hit")
                product_ids = cached_ids
        else:
            span.set_attribute("demo.feature_flag.recommendation_cache", False)
            cat_response = list_products_with_retry(product_catalog_stub, demo_pb2.Empty(), context=context)
            product_ids = [x.id for x in cat_response.products]

        span.set_attribute("demo.product.count", len(product_ids))

        # Create a filtered list of products excluding the products received as input
        filtered_products = list(set(product_ids) - set(request_product_ids))
        num_products = len(filtered_products)
        span.set_attribute("demo.product.filtered.count", num_products)
        num_return = min(max_responses, num_products)

        # Sample list of indicies to return
        indices = random.sample(range(num_products), num_return)
        # Fetch product ids from indices
        prod_list = [filtered_products[i] for i in indices]

        span.set_attribute("demo.product.filtered.list", prod_list)

        return prod_list


def must_map_env(key: str):
    value = os.environ.get(key)
    if value is None:
        raise Exception(f'{key} environment variable must be set')
    return value


def check_feature_flag(flag_name: str):
    # Initialize OpenFeature
    client = api.get_client()
    return client.get_boolean_value("recommendationCacheFailure", False)


def str_to_bool(value: str) -> bool:
    """Convert string to boolean, supports 1/0, true/false (case-insensitive)."""
    if not value:
        return False
    return value.lower() in ('true', '1', 'yes')


def load_cert_file(path: str) -> bytes:
    """Load certificate/key file from filesystem, returns bytes."""
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Certificate file not found: {path}")
    except PermissionError:
        raise PermissionError(f"Permission denied reading certificate file: {path}")
    except Exception as e:
        raise ValueError(f"Error reading certificate file {path}: {str(e)}") from e


def create_product_catalog_client(catalog_addr: str, logger=None):
    """Create ProductCatalogService client with optional TLS/mTLS configuration."""
    # Configure gRPC channel settings for ProductCatalogService
    service_config = json.dumps({
        "methodConfig": [
            {
                "name": [
                    { "service": "oteldemo.ProductCatalogService", "method": "ListProducts" }
                ],
                "timeout": "10s"
            }
        ]
    })
    channel_options = [
        ("grpc.enable_retries", 0),  # Disable built-in retries since we implement our own
        ("grpc.service_config", service_config),
        ("grpc.max_receive_message_length", -1),
    ]
    
    # Configure Product Catalog client channel (plaintext or TLS/mTLS)
    client_tls_enabled = str_to_bool(os.environ.get('TLS_CLIENT_ENABLE', 'false'))
    if client_tls_enabled:
        # Load required CA cert for server validation
        client_ca_cert_path = os.environ.get('TLS_CLIENT_CA_CERT_PATH')
        if not client_ca_cert_path:
            raise ValueError("TLS_CLIENT_ENABLE is true but TLS_CLIENT_CA_CERT_PATH is not set")
        root_certificates = load_cert_file(client_ca_cert_path)
        certificate_chain = None
        private_key = None
        
        # Check if mTLS is enabled
        client_mtls_enabled = str_to_bool(os.environ.get('TLS_CLIENT_ENABLE_MTLS', 'false'))
        if client_mtls_enabled:
            client_cert_path = os.environ.get('TLS_CLIENT_CERT_PATH')
            client_key_path = os.environ.get('TLS_CLIENT_KEY_PATH')
            if not client_cert_path or not client_key_path:
                raise ValueError("TLS_CLIENT_ENABLE_MTLS is true but TLS_CLIENT_CERT_PATH or TLS_CLIENT_KEY_PATH is not set")
            certificate_chain = load_cert_file(client_cert_path)
            private_key = load_cert_file(client_key_path)
        
        # Create TLS credentials
        client_credentials = grpc.ssl_channel_credentials(
            root_certificates=root_certificates,
            certificate_chain=certificate_chain,
            private_key=private_key
        )
        pc_channel = grpc.secure_channel(
            catalog_addr,
            credentials=client_credentials,
            options=channel_options
        )
    else:
        # Use plaintext channel (default behavior)
        pc_channel = grpc.insecure_channel(
            catalog_addr,
            options=channel_options
        )
    
    # Add retry logging interceptor if logger is provided
    if logger is not None:
        retry_interceptor = RetryLoggingInterceptor(logger)
        intercepted_channel = grpc.intercept_channel(pc_channel, retry_interceptor)
        return demo_pb2_grpc.ProductCatalogServiceStub(intercepted_channel), pc_channel
    else:
        # For test cases without logger
        return demo_pb2_grpc.ProductCatalogServiceStub(pc_channel), pc_channel


def serve(listen_addr: str, product_catalog_channel=None, test_mode: bool = False, logger=None):
    """Start recommendation service gRPC server with optional TLS/mTLS configuration."""
    # Create gRPC server with rate limit interceptor if enabled
    interceptors = []
    if limiter is not None:
        interceptors.append(RateLimitInterceptor())
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=interceptors)

    # Add class to gRPC server
    service = RecommendationService()
    demo_pb2_grpc.add_RecommendationServiceServicer_to_server(service, server)
    health_pb2_grpc.add_HealthServicer_to_server(service, server)

    # Define signal handler for graceful shutdown (registered before server start)
    def handle_shutdown_signal(signum, frame):
        signal_name = signal.Signals(signum).name
        if logger:
            logger.info(f"Received termination signal {signal_name}, starting graceful shutdown sequence")
            logger.info("Stopping new gRPC connections, waiting up to 10s for in-flight requests to complete")
        # Initiate graceful shutdown with 10s grace period per requirements
        shutdown_event = server.stop(grace=10.0)
        
        def wait_for_shutdown():
            shutdown_completed = shutdown_event.wait()
            if shutdown_completed and logger:
                logger.info("All in-flight requests completed within grace period")
            elif logger:
                logger.warning("10s grace period timeout reached: terminating remaining active requests")
            
            # Close product catalog connections if available
            if product_catalog_channel:
                product_catalog_channel.close()
                if logger:
                    logger.info("Product catalog service connections closed")
            
            if logger:
                logger.info("Shutdown sequence complete, exiting service")
        
        # Wait for shutdown to complete
        wait_for_shutdown()
        os._exit(0)
    
    # Register signal handlers for SIGTERM and SIGINT before starting server
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    # Configure server listener (plaintext or TLS/mTLS)
    server_tls_enabled = str_to_bool(os.environ.get('TLS_SERVER_ENABLE', 'false'))
    
    if server_tls_enabled:
        # Load required server cert and key
        server_cert_path = os.environ.get('TLS_SERVER_CERT_PATH')
        server_key_path = os.environ.get('TLS_SERVER_KEY_PATH')
        if not server_cert_path or not server_key_path:
            raise ValueError("TLS_SERVER_ENABLE is true but TLS_SERVER_CERT_PATH or TLS_SERVER_KEY_PATH is not set")
        server_cert_chain = load_cert_file(server_cert_path)
        server_private_key = load_cert_file(server_key_path)
        root_certificates = None
        
        # Check if server-side mTLS is required
        client_ca_cert_path = os.environ.get('TLS_SERVER_CLIENT_CA_CERT_PATH')
        if client_ca_cert_path:
            root_certificates = load_cert_file(client_ca_cert_path)
        
        # Create server TLS credentials
        server_credentials = grpc.ssl_server_credentials(
            private_key_certificate_chain_pairs=[(server_private_key, server_cert_chain)],
            root_certificates=root_certificates,
            require_client_auth=client_ca_cert_path is not None
        )
        server.add_secure_port(listen_addr, server_credentials)
        if logger:
            logger.info(f'Recommendation service started with TLS enabled, listening on {listen_addr}')
    else:
        # Use plaintext port (default behavior)
        server.add_insecure_port(listen_addr)
        if logger:
            logger.info(f'Recommendation service started, listening on {listen_addr}')
    
    server.start()
    
    # Start HTTP health check server in separate thread
    if not test_mode:
        http_thread = threading.Thread(target=run_http_server, daemon=True)
        http_thread.start()
        if logger:
            logger.info("HTTP health check server started on port 8080")
    
    if test_mode:
        # Return server instance for testing, don't wait for termination
        # Store port for test access if using random port [::]:0
        if listen_addr.endswith(':0'):
            server._port = server.addrs[0].get_port()
        return server
    
    # Wait for server termination
    server.wait_for_termination()


if __name__ == "__main__":
    service_name = must_map_env('OTEL_SERVICE_NAME')
    api.set_provider(FlagdProvider(host=os.environ.get('FLAGD_HOST', 'flagd'), port=os.environ.get('FLAGD_PORT', 8013)))
    api.add_hooks([TracingHook()])

    # Initialize Traces and Metrics
    tracer = trace.get_tracer_provider().get_tracer(service_name)
    meter = metrics.get_meter_provider().get_meter(service_name)
    rec_svc_metrics = init_metrics(meter)
    
    # Set global retry metrics references
    retry_attempts_counter = rec_svc_metrics["retry_attempts_counter"]
    retry_failures_counter = rec_svc_metrics["retry_failures_counter"]

    # Initialize circuit breaker with metrics listener
    circuit_breaker_listener = CircuitBreakerMetricsListener(rec_svc_metrics)
    product_catalog_circuit_breaker = pybreaker.CircuitBreaker(
        fail_max=PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        reset_timeout=PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT,
        half_open_max_calls=PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS,
        listeners=[circuit_breaker_listener]
    )

    def wrap_call(func):
        @product_catalog_circuit_breaker
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        def wrapped(*args, **kwargs):
            try:
                return wrapper(*args, **kwargs)
            except pybreaker.CircuitBreakerError:
                context = grpc.ServicerContext()
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details(CIRCUIT_OPEN_ERROR_MSG)
                raise context._state.error
        return wrapped
    product_catalog_circuit_breaker.wrap = wrap_call

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
    logger = logging.getLogger('main')
    logger.addHandler(handler)

catalog_addr = must_map_env('PRODUCT_CATALOG_ADDR')
product_catalog_stub, product_catalog_channel = create_product_catalog_client(catalog_addr, logger=logger)
product_catalog_client = product_catalog_stub

# Initialize rate limiter
LEGACY_RATE_LIMIT_ENV_VAR = 'RECOMMENDATION_SERVICE_RATE_LIMIT_RPS'
DEFAULT_RATE_LIMIT_ENV_VAR = 'RECOMMENDATION_SERVICE_RATE_LIMIT_DEFAULT_RPS'
RATE_LIMIT_ENV_VAR_PREFIX = 'RECOMMENDATION_SERVICE_RATE_LIMIT_'
RATE_LIMIT_ENV_VAR_SUFFIX = '_RPS'
DEFAULT_PER_ENDPOINT_RPS = 100

# Helper to normalize gRPC method name to env var suffix
def normalize_endpoint_name(method_name):
    # Extract the method name part after the last slash
    if '/' in method_name:
        method_part = method_name.rsplit('/', 1)[-1]
    else:
        method_part = method_name
    # Convert to uppercase, replace non-alphanumeric with underscores
    normalized = re.sub(r'[^a-zA-Z0-9]', '_', method_part).upper()
    return normalized

# Load rate limit configuration
limiter = None
endpoint_limits = {}
legacy_rate_limit_rps = int(os.environ.get(LEGACY_RATE_LIMIT_ENV_VAR, '0'))
default_limit_rps = int(os.environ.get(DEFAULT_RATE_LIMIT_ENV_VAR, str(legacy_rate_limit_rps if legacy_rate_limit_rps > 0 else DEFAULT_PER_ENDPOINT_RPS)))

# Check if rate limiting is enabled at all
if legacy_rate_limit_rps > 0 or default_limit_rps > 0:
    limiter = Limiter(
        storage=MemoryStorage(),
        strategy=FixedWindowRateLimiter()
    )
    # Load per-endpoint limits from env vars
    for env_key, env_value in os.environ.items():
        if env_key.startswith(RATE_LIMIT_ENV_VAR_PREFIX) and env_key.endswith(RATE_LIMIT_ENV_VAR_SUFFIX) and env_key != DEFAULT_RATE_LIMIT_ENV_VAR:
            try:
                limit_rps = int(env_value)
                if limit_rps > 0:
                    endpoint_limits[env_key] = RateLimitItemPerSecond(limit_rps)
            except ValueError:
                # Ignore invalid values
                pass

# gRPC interceptor for rate limiting
class RateLimitInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        method_name = handler_call_details.method
        def rate_limit_wrapper(behavior, request_streaming, response_streaming):
            def new_behavior(request, context):
                if limiter is None:
                    return behavior(request, context)
                
                # Get limit for this endpoint
                normalized_name = normalize_endpoint_name(method_name)
                endpoint_env_key = f"{RATE_LIMIT_ENV_VAR_PREFIX}{normalized_name}{RATE_LIMIT_ENV_VAR_SUFFIX}"
                limit = endpoint_limits.get(endpoint_env_key, RateLimitItemPerSecond(default_limit_rps))
                
                if limit.amount > 0:
                    if not limiter.check(limit, f"endpoint_{normalized_name}"):
                        # Increment rate limit metric
                        rec_svc_metrics["rate_limited_requests"].add(1, {'endpoint': method_name})
                        error_msg = f"Rate limit exceeded for endpoint {method_name}: allowed {limit.amount} requests per second, please retry later"
                        logger.warning(
                            f"Rate limit exceeded for endpoint {method_name}",
                            extra={
                                "event": "rate_limit_exceeded",
                                "endpoint": method_name,
                                "limit": limit.amount
                            }
                        )
                        context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, error_msg)
                
                return behavior(request, context)
            return new_behavior
        return grpc.unary_unary_rpc_method_handler(
            rate_limit_wrapper(
                handler_call_details.request_deserializer,
                handler_call_details.response_deserializer
            ),
            request_deserializer=handler_call_details.request_deserializer,
            response_deserializer=handler_call_details.response_deserializer
        )

# Start server
port = must_map_env('RECOMMENDATION_PORT')
serve(f'[::]:{port}', product_catalog_channel=product_catalog_channel, logger=logger)
