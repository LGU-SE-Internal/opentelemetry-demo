#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import grpc
import tenacity
from typing import Callable, Any
from opentelemetry.metrics import get_meter

meter = get_meter("recommendationservice")
recommendation_service_product_catalog_retry_attempts = meter.create_counter(
    name="recommendation_service_product_catalog_retry_attempts",
    description="Number of retry attempts made to product catalog service",
    unit="1"
)

RETRYABLE_STATUS_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.ABORTED,
}

def is_retryable_exception(e: Exception) -> bool:
    if isinstance(e, grpc.RpcError):
        return e.code() in RETRYABLE_STATUS_CODES
    return False

def before_sleep_retry(retry_state: tenacity.RetryCallState) -> None:
    exception = retry_state.outcome.exception()
    if isinstance(exception, grpc.RpcError):
        grpc_status_code = exception.code().name
        status = "failure" if retry_state.attempt_number == 3 else "success"
        recommendation_service_product_catalog_retry_attempts.labels(
            status=status,
            grpc_status_code=grpc_status_code
        ).inc()

@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential_jitter(
        multiplier=0.1,
        exp_base=2,
        max=0.4,
        jitter=0.25
    ),
    retry=tenacity.retry_if_exception(is_retryable_exception),
    before_sleep=before_sleep_retry,
    reraise=True
)
def with_exponential_backoff_retry(call: Callable, *args, **kwargs) -> Any:
    if not hasattr(call, '__grpc_stub_method__'):
        raise ValueError("Input call must be a gRPC stub method")
    return call(*args, **kwargs)
