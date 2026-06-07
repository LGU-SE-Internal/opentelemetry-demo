#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


"""Recommendation service gRPC server implementation for the OpenTelemetry demo."""

# Python
import os
import random
import json
import re
from typing import Optional
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
        # Validation logic
        product_ids = request.product_ids
        span = trace.get_current_span()
        trace_id = format(span.get_span_context().trace_id, '016x') if span.is_recording() else None
        
        # Validate list is not empty
        if len(product_ids) == 0:
            error_msg = "product_ids list cannot be empty"
            logger.warning(
                "Invalid recommendation request received",
                extra={
                    "error": error_msg,
                    "trace_id": trace_id,
                    "request_product_ids_count": 0
                }
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
            
        # Validate list size <= 100
        if len(product_ids) > 100:
            error_msg = "product_ids list exceeds maximum allowed size of 100"
            logger.warning(
                "Invalid recommendation request received",
                extra={
                    "error": error_msg,
                    "trace_id": trace_id,
                    "request_product_ids_count": len(product_ids)
                }
            )
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
            
        # Validate each product ID format
        id_pattern = re.compile(r'^[a-zA-Z0-9]{3,12}$')
        for idx, product_id in enumerate(product_ids):
            if not id_pattern.match(product_id):
                error_msg = f"product ID at index {idx}: invalid format, must be alphanumeric 3-12 characters"
                logger.warning(
                    "Invalid recommendation request received",
                    extra={
                        "error": error_msg,
                        "trace_id": trace_id,
                        "request_product_ids_count": len(product_ids),
                        "invalid_product_id": product_id,
                        "invalid_product_index": idx
                    }
                )
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)

        prod_list = get_product_list(request.product_ids)
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


def get_product_list(request_product_ids):
    global first_run
    global cached_ids
    with tracer.start_as_current_span("get_product_list") as span:
        max_responses = 5

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


def load_tls_credentials(
    cert_path: str,
    key_path: str,
    ca_cert_path: Optional[str] = None
) -> grpc.ServerCredentials:
    """
    Loads, validates, and constructs gRPC TLS server credentials from provided paths.

    Args:
        cert_path: Filesystem path to server TLS certificate
        key_path: Filesystem path to server private key
        ca_cert_path: Optional filesystem path to CA certificate bundle for mTLS

    Returns:
        Configured gRPC ServerCredentials object for TLS connections

    Raises:
        FileNotFoundError: If any provided path does not exist
        PermissionError: If any provided file is not readable by the service process
        ValueError: If any provided file contains invalid PEM formatted data
    """
    # Read and validate certificate file
    try:
        with open(cert_path, 'rb') as f:
            cert_data = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"TLS certificate file not found at path: {cert_path}")
    except PermissionError:
        raise PermissionError(f"Insufficient permissions to read TLS certificate file at path: {cert_path}")
    except Exception as e:
        raise ValueError(f"Failed to read TLS certificate file: {str(e)}") from e
    
    # Validate certificate is PEM formatted
    if b"-----BEGIN CERTIFICATE-----" not in cert_data:
        raise ValueError("invalid certificate format: not a valid PEM certificate")

    # Read and validate private key file
    try:
        with open(key_path, 'rb') as f:
            key_data = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"TLS private key file not found at path: {key_path}")
    except PermissionError:
        raise PermissionError(f"Insufficient permissions to read TLS private key file at path: {key_path}")
    except Exception as e:
        raise ValueError(f"Failed to read TLS private key file: {str(e)}") from e
    
    # Validate private key is PEM formatted
    if b"-----BEGIN PRIVATE KEY-----" not in key_data and b"-----BEGIN RSA PRIVATE KEY-----" not in key_data:
        raise ValueError("invalid private key format: not a valid PEM private key")

    # Handle mTLS if CA cert path is provided
    if ca_cert_path is not None:
        try:
            with open(ca_cert_path, 'rb') as f:
                ca_cert_data = f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"TLS CA certificate file not found at path: {ca_cert_path}")
        except PermissionError:
            raise PermissionError(f"Insufficient permissions to read TLS CA certificate file at path: {ca_cert_path}")
        except Exception as e:
            raise ValueError(f"Failed to read TLS CA certificate file: {str(e)}") from e
        
        # Validate CA cert is PEM formatted
        if b"-----BEGIN CERTIFICATE-----" not in ca_cert_data:
            raise ValueError("invalid CA certificate format: not a valid PEM certificate")
        
        # Create mTLS credentials requiring client certificate
        return grpc.ssl_server_credentials(
            [(key_data, cert_data)],
            root_certificates=ca_cert_data,
            require_client_auth=True
        )
    else:
        # Create standard one-way TLS credentials
        return grpc.ssl_server_credentials([(key_data, cert_data)])


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
    
    pc_channel = grpc.insecure_channel(
        catalog_addr,
        options=[
            ("grpc.enable_retries", 1),
            ("grpc.service_config", service_config),
            ("grpc.max_receive_message_length", -1),
        ]
    )
    
    # Add retry logging interceptor
    retry_interceptor = RetryLoggingInterceptor(logger)
    intercepted_channel = grpc.intercept_channel(pc_channel, retry_interceptor)
    product_catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(intercepted_channel)

    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Add class to gRPC server
    service = RecommendationService()
    demo_pb2_grpc.add_RecommendationServiceServicer_to_server(service, server)
    health_pb2_grpc.add_HealthServicer_to_server(service, server)

    # Start server
    port = must_map_env('RECOMMENDATION_PORT')
    
    # TLS Configuration
    tls_cert_path = os.environ.get('RECOMMENDATION_SERVICE_TLS_CERT_PATH')
    tls_key_path = os.environ.get('RECOMMENDATION_SERVICE_TLS_KEY_PATH')
    tls_ca_cert_path = os.environ.get('RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH')
    
    if tls_cert_path or tls_key_path:
        # Both must be provided if either is provided
        if not tls_cert_path or not tls_key_path:
            raise ValueError("both certificate and key path must be provided together when enabling TLS")
        
        # Load TLS credentials
        server_credentials = load_tls_credentials(tls_cert_path, tls_key_path, tls_ca_cert_path)
        server.add_secure_port(f'[::]:{port}', server_credentials)
        logger.info(f'Recommendation service started with TLS enabled, listening on port {port}')
        if tls_ca_cert_path:
            logger.info('Mutual TLS (mTLS) client authentication is enabled')
    else:
        # Fall back to insecure mode
        server.add_insecure_port(f'[::]:{port}')
        logger.info(f'Recommendation service started in insecure mode, listening on port {port}')
    
    server.start()
    server.wait_for_termination()
