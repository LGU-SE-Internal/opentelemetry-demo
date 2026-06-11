#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

def init_metrics(meter):

    # Recommendations counter
    recommendation_requests = meter.create_counter(
        'demo.recommendation.requests', unit='recommendations', description="Counts the total number of given recommendations"
    )
    
    # Rate limited requests counter
    rate_limited_requests = meter.create_counter(
        'recommendation_service_rate_limited_requests_total', unit='1', description="Total number of requests that were rejected due to rate limiting per endpoint."
    )
    
    # Product catalog retry attempts counter
    retry_attempts_counter = meter.create_counter(
        'recommendation_service_product_catalog_retry_attempts', unit='1', description="Count of retry attempts triggered for product catalog calls"
    )

    # Product catalog retry failures counter
    retry_failures_counter = meter.create_counter(
        'recommendation_service_product_catalog_retry_failures', unit='1', description="Count of product catalog calls that failed after exhausting all retries"
    )

    # Circuit breaker state gauge
    product_catalog_circuit_breaker_state = meter.create_gauge(
        'recommendation_service_product_catalog_circuit_breaker_state', unit='1', description="Current state of the Product Catalog Service circuit breaker: 0 = closed, 1 = open, 2 = half-open"
    )

    # Circuit breaker trip counter
    product_catalog_circuit_breaker_trips_total = meter.create_counter(
        'recommendation_service_product_catalog_circuit_breaker_trips_total', unit='1', description="Total number of times the Product Catalog Service circuit breaker has tripped from closed to open state"
    )

    rec_svc_metrics = {
        "demo.recommendation.requests": recommendation_requests,
        "rate_limited_requests": rate_limited_requests,
        "retry_attempts_counter": retry_attempts_counter,
        "retry_failures_counter": retry_failures_counter,
        "product_catalog_circuit_breaker_state": product_catalog_circuit_breaker_state,
        "product_catalog_circuit_breaker_trips_total": product_catalog_circuit_breaker_trips_total,
    }

    return rec_svc_metrics
