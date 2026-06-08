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
        'recommendation_service_rate_limited_requests_total', unit='1', description="Total number of requests rejected due to rate limiting on the ListRecommendations endpoint."
    )
    
    # Product catalog retry attempts counter
    product_catalog_retry_attempts = meter.create_counter(
        'recommendation_service.product_catalog.retry_attempts', unit='1', description="Counts the number of retry attempts for ProductCatalogService ListProducts calls."
    )

    rec_svc_metrics = {
        "demo.recommendation.requests": recommendation_requests,
        "rate_limited_requests": rate_limited_requests,
        "product_catalog_retry_attempts": product_catalog_retry_attempts,
    }

    return rec_svc_metrics
