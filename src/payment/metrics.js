// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const { metrics } = require('@opentelemetry/api');
const meter = metrics.getMeter('payment');

/**
 * Total count of payment gateway API calls
 * Labels: status: success/failed/retried, error_type: string
 */
const paymentGatewayCallsTotal = meter.createCounter('payment_gateway_calls_total', {
  description: 'Total count of payment gateway API calls'
});

/**
 * Total number of retry attempts made
 */
const paymentRetryAttemptsTotal = meter.createCounter('payment_retry_attempts_total', {
  description: 'Total number of retry attempts made for payment gateway calls'
});

/**
 * Current state of the payment circuit breaker
 * Labels: state: closed/open/half_open
 * Values: closed=0, open=1, half_open=2
 */
const paymentCircuitBreakerState = meter.createGauge('payment_circuit_breaker_state', {
  description: 'Current state of the payment circuit breaker'
});

module.exports = {
  paymentGatewayCallsTotal,
  paymentRetryAttemptsTotal,
  paymentCircuitBreakerState
};
