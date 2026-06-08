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

# gRPC interceptor to log retry attempts for ProductCatalogService ListProducts calls
class RetryLoggingInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, logger):
        self.logger = logger

    def intercept_unary_unary(self, continuation, client_call_details, request):
        # Only intercept ListProducts calls
        if client_call_details.method.endswith("oteldemo.ProductCatalogService/ListProducts"):
            span = trace.get_current_span()
            attempt = 1
            def on_retry(call_details, response, error):
                nonlocal attempt
                if error is not None:
                    status_code = error.code().name
                    status_details = error.details()
                    # Calculate backoff delay (exponential with jitter: 100ms * 2^(attempt-1) ± 20%)
                    base_delay = 100 * (2 ** (attempt - 1))
                    jitter = random.uniform(-0.2, 0.2)
                    backoff_ms = int(base_delay * (1 + jitter))
                    
                    # Get request ID from trace span if available
                    request_id = span.get_span_context().trace_id if span.is_recording() else None
                    
                    self.logger.warning(
                        f"Product catalog ListProducts retry attempt {attempt} after {status_code} error",
                        extra={
                            "event": "product_catalog_retry_attempt",
                            "attempt_number": attempt,
                            "error_code": status_code,
                            "error_message": status_details,
                            "backoff_delay_ms": backoff_ms,
                            "request_id": request_id
                        }
                    )
                    attempt += 1
            # Add retry callback to call options
            if client_call_details.options is None:
                client_call_details.options = []
            client_call_details.options.append(("grpc.on_retry", on_retry))
        
        # Add per-call 5s timeout for ListProducts
        if client_call_details.method.endswith("oteldemo.ProductCatalogService/ListProducts"):
            if client_call_details.timeout is None:
                client_call_details.timeout = 5  # 5 seconds per attempt
        
        return continuation(client_call_details, request)

class RecommendationService(demo_pb2_grpc.RecommendationServiceServicer):
    def ListRecommendations(self, request, context):
        span = trace.get_current_span()
        trace_id = format(span.get_span_context().trace_id, '016x') if span.is_recording() else "unknown"
        
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
                cat_response = product_catalog_stub.ListProducts(demo_pb2.Empty())
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
            cat_response = product_catalog_stub.ListProducts(demo_pb2.Empty())
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
    # Configure gRPC channel with resilience settings for ProductCatalogService
    service_config = json.dumps({
        "methodConfig": [
            {
                "name": [
                    { "service": "oteldemo.ProductCatalogService", "method": "ListProducts" }
                ],
                "timeout": "10s",
                "retryPolicy": {
                    "maxAttempts": 3,
                    "initialBackoff": "0.1s",
                    "maxBackoff": "1s",
                    "backoffMultiplier": 2,
                    "retryableStatusCodes": ["UNAVAILABLE", "RESOURCE_EXHAUSTED", "ABORTED", "INTERNAL", "DEADLINE_EXCEEDED"]
                }
            }
        ]
    })
    channel_options = [
        ("grpc.enable_retries", 1),
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

    # Start server
    port = must_map_env('RECOMMENDATION_PORT')
    serve(f'[::]:{port}', product_catalog_channel=product_catalog_channel, logger=logger)
