#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


"""Recommendation service gRPC server implementation for the OpenTelemetry demo."""

# Python
import os
import random
import json
import re
import signal
from concurrent import futures

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
cached_ids = []
first_run = True

# Retry configuration from environment variables
RETRY_MAX_ATTEMPTS = int(os.environ.get('RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_MAX_ATTEMPTS', '3'))
RETRY_ATTEMPTS = RETRY_MAX_ATTEMPTS # Alias for tests
RETRY_INITIAL_BACKOFF_MS = int(os.environ.get('RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_INITIAL_BACKOFF_MS', '100'))
RETRY_BACKOFF_MULTIPLIER = float(os.environ.get('RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_BACKOFF_MULTIPLIER', '2'))
RETRY_MAX_BACKOFF_MS = int(os.environ.get('RECOMMENDATION_SERVICE_PRODUCT_CATALOG_RETRY_MAX_BACKOFF_MS', '2000'))

# Eligible retry status codes
RETRYABLE_STATUS_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.INTERNAL
}

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

# TLS Configuration
RECOMMENDATION_SERVICE_TLS_MODE = os.environ.get('RECOMMENDATION_SERVICE_TLS_MODE', 'disabled').lower()
RECOMMENDATION_SERVICE_TLS_CERT_PATH = os.environ.get('RECOMMENDATION_SERVICE_TLS_CERT_PATH', '')
RECOMMENDATION_SERVICE_TLS_KEY_PATH = os.environ.get('RECOMMENDATION_SERVICE_TLS_KEY_PATH', '')
RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH = os.environ.get('RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH', '')
PRODUCT_CATALOG_SERVICE_TLS_ENABLED = os.environ.get('PRODUCT_CATALOG_SERVICE_TLS_ENABLED', 'false').lower() == 'true'
PRODUCT_CATALOG_SERVICE_TLS_CA_CERT_PATH = os.environ.get('PRODUCT_CATALOG_SERVICE_TLS_CA_CERT_PATH', '')

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
        
        # Rate limiting check
        if limiter is not None and rate_limit is not None:
            if not limiter.check(rate_limit, "list_recommendations_endpoint"):
                # Increment rate limit metric
                rec_svc_metrics["rate_limited_requests"].add(1, {'endpoint': 'ListRecommendations', 'status': 'rate_limited'})
                error_msg = "Rate limit exceeded. Try again later."
                logger.warning(
                    f"Rate limit exceeded for recommendation request (trace_id={trace_id})",
                    extra={
                        "event": "rate_limit_exceeded",
                        "trace_id": trace_id
                    }
                )
                context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, error_msg)
        
        # Helper function to strip control characters (AC-6)
        def sanitize_string(s):
            if not s:
                return s
            # Remove ASCII control characters 0-31 and 127
            return re.sub(r'[\x00-\x1F\x7F]', '', s)
        
        # Sanitize all input parameters first
        sanitized_user_id = sanitize_string(request.user_id)
        sanitized_product_ids = [sanitize_string(pid) for pid in request.product_ids]
        
        # AC-1: Validate user_id is present
        if not sanitized_user_id:
            error_msg = "user_id parameter is required"
            logger.error(
                f"Validation failed for recommendation request (trace_id={trace_id}): parameter=user_id, error={error_msg}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
        # AC-2: Validate user_id format
        user_id_pattern = re.compile(r'^[a-zA-Z0-9-]{3,36}$')
        if not user_id_pattern.match(sanitized_user_id):
            error_msg = "user_id has invalid format: must be alphanumeric (including '-') between 3-36 characters"
            logger.error(
                f"Validation failed for recommendation request (trace_id={trace_id}): parameter=user_id, error={error_msg}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
        # AC-3: Validate product_ids list size <=100
        if len(sanitized_product_ids) > 100:
            error_msg = "product_ids list exceeds maximum allowed size of 100"
            logger.error(
                f"Validation failed for recommendation request (trace_id={trace_id}): parameter=product_ids, error={error_msg}, count={len(sanitized_product_ids)}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
        # AC-4: Validate each product ID format
        product_id_pattern = re.compile(r'^[a-zA-Z0-9]{3,12}$')
        for idx, product_id in enumerate(sanitized_product_ids):
            if not product_id_pattern.match(product_id):
                error_msg = f"product ID at index {idx}: invalid format, must be alphanumeric 3-12 characters"
                logger.error(
                    f"Validation failed for recommendation request (trace_id={trace_id}): parameter=product_ids[{idx}], error={error_msg}, value={product_id}"
                )
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
        # AC-5: Validate result_size bounds
        result_size = request.result_size
        if result_size == 0:  # proto3 int32 default value, use default 5
            result_size = 5
        if result_size < 1 or result_size > 20:
            error_msg = "result_size must be between 1 and 20 (inclusive)"
            logger.error(
                f"Validation failed for recommendation request (trace_id={trace_id}): parameter=result_size, error={error_msg}, value={result_size}"
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
        
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
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING)

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
    """Create ProductCatalogService client with optional TLS configuration."""
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
    
    # Configure Product Catalog client channel (plaintext or TLS)
    if PRODUCT_CATALOG_SERVICE_TLS_ENABLED:
        # Load CA cert if provided for server validation
        root_certificates = None
        if PRODUCT_CATALOG_SERVICE_TLS_CA_CERT_PATH:
            root_certificates = load_cert_file(PRODUCT_CATALOG_SERVICE_TLS_CA_CERT_PATH)
        
        # Create TLS credentials
        client_credentials = grpc.ssl_channel_credentials(
            root_certificates=root_certificates
        )
        channel = grpc.secure_channel(
            catalog_addr,
            client_credentials,
            options=channel_options
        )
        if logger:
            logger.info(f"Connected to Product Catalog Service at {catalog_addr} using TLS")
    else:
        # Plaintext connection (existing behavior)
        channel = grpc.insecure_channel(
            catalog_addr,
            options=channel_options
        )
        if logger:
            logger.info(f"Connected to Product Catalog Service at {catalog_addr} using plaintext")
    
    stub = demo_pb2_grpc.ProductCatalogServiceStub(channel)
    return stub, channel


def serve(listen_addr: str, product_catalog_channel=None, test_mode: bool = False, logger=None):
    """Start recommendation service gRPC server with optional TLS/mTLS configuration."""
    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

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
    tls_mode = RECOMMENDATION_SERVICE_TLS_MODE
    valid_tls_modes = ['disabled', 'tls', 'mtls']
    
    if tls_mode not in valid_tls_modes:
        if logger:
            logger.warning(f"Invalid TLS mode '{tls_mode}', defaulting to 'disabled'")
        tls_mode = 'disabled'
    
    if tls_mode != 'disabled':
        # Validate required TLS certificate and key paths
        if not RECOMMENDATION_SERVICE_TLS_CERT_PATH or not RECOMMENDATION_SERVICE_TLS_KEY_PATH:
            raise ValueError(f"TLS mode is '{tls_mode}' but RECOMMENDATION_SERVICE_TLS_CERT_PATH or RECOMMENDATION_SERVICE_TLS_KEY_PATH is not set")
        
        # Load server certificate and private key
        server_cert = load_cert_file(RECOMMENDATION_SERVICE_TLS_CERT_PATH)
        server_key = load_cert_file(RECOMMENDATION_SERVICE_TLS_KEY_PATH)
        root_certificates = None
        
        # Configure mTLS if enabled
        require_client_auth = False
        if tls_mode == 'mtls':
            if not RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH:
                raise ValueError("TLS mode is 'mtls' but RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH is not set")
            root_certificates = load_cert_file(RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH)
            require_client_auth = True
        
        # Create server TLS credentials
        server_credentials = grpc.ssl_server_credentials(
            [(server_key, server_cert)],
            root_certificates=root_certificates,
            require_client_auth=require_client_auth
        )
        
        # Add secure port
        server.add_secure_port(listen_addr, server_credentials)
        if logger:
            logger.info(f"gRPC server listening on {listen_addr} with TLS mode '{tls_mode}'")
    else:
        # Plaintext server (existing behavior)
        server.add_insecure_port(listen_addr)
        if logger:
            logger.info(f"gRPC server listening on {listen_addr} with plaintext (TLS disabled)")
    
    server.start()
    
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
rate_limit_rps = int(os.environ.get('RECOMMENDATION_SERVICE_RATE_LIMIT_RPS', '0'))
limiter = None
rate_limit = None
if rate_limit_rps > 0:
    limiter = Limiter(
        storage=MemoryStorage(),
        strategy=FixedWindowRateLimiter()
    )
    rate_limit = RateLimitItemPerSecond(rate_limit_rps)

# Start server
port = must_map_env('RECOMMENDATION_PORT')
serve(f'[::]:{port}', product_catalog_channel=product_catalog_channel, logger=logger)
