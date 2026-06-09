// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const { context, propagation, trace, metrics, SpanStatusCode } = require('@opentelemetry/api');
const cardValidator = require('simple-card-validator');
const { v4: uuidv4 } = require('uuid');
const CircuitBreaker = require('opossum');

const { OpenFeature } = require('@openfeature/server-sdk');
const { FlagdProvider } = require('@openfeature/flagd-provider');
const flagProvider = new FlagdProvider();

const { paymentGatewayCallsTotal, paymentRetryAttemptsTotal, paymentCircuitBreakerState } = require('./metrics');
const logger = require('./logger');
const { withRetry } = require('./retry');
const tracer = trace.getTracer('payment');
const meter = metrics.getMeter('payment');
const transactionsCounter = meter.createCounter('demo.payment.transactions');

// Retry configuration for flagd calls
const FLAGD_RETRY_MAX_ATTEMPTS = parseInt(process.env.PAYMENT_SERVICE_FLAGD_RETRY_MAX_ATTEMPTS || '3', 10);
const FLAGD_RETRY_INITIAL_DELAY_MS = parseInt(process.env.PAYMENT_SERVICE_FLAGD_RETRY_INITIAL_DELAY_MS || '100', 10);

// Fault tolerance configuration
const PAYMENT_RETRY_MAX_ATTEMPTS = parseInt(process.env.PAYMENT_RETRY_MAX_ATTEMPTS || '3', 10);
const PAYMENT_RETRY_INITIAL_DELAY_MS = parseInt(process.env.PAYMENT_RETRY_INITIAL_DELAY_MS || '100', 10);
const PAYMENT_CIRCUIT_BREAKER_ERROR_THRESHOLD = parseInt(process.env.PAYMENT_CIRCUIT_BREAKER_ERROR_THRESHOLD || '50', 10);
const PAYMENT_CIRCUIT_BREAKER_VOLUME_THRESHOLD = parseInt(process.env.PAYMENT_CIRCUIT_BREAKER_VOLUME_THRESHOLD || '10', 10);
const PAYMENT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS = parseInt(process.env.PAYMENT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS || '30000', 10);

const LOYALTY_LEVEL = ['platinum', 'gold', 'silver', 'bronze'];

/** Custom error types */
class PaymentGatewayError extends Error {
  constructor(message, statusCode, isRetryable) {
    super(message);
    this.name = 'PaymentGatewayError';
    this.statusCode = statusCode;
    this.isRetryable = isRetryable;
    Error.captureStackTrace(this, PaymentGatewayError);
  }
}

class CircuitBreakerOpenError extends Error {
  constructor(message) {
    super(message || 'Payment circuit breaker is open, requests are blocked');
    this.name = 'CircuitBreakerOpenError';
    Error.captureStackTrace(this, CircuitBreakerOpenError);
  }
}

class RetryAttemptsExhaustedError extends Error {
  constructor(attemptCount, lastError) {
    super(`All ${attemptCount} retry attempts exhausted for payment request`);
    this.name = 'RetryAttemptsExhaustedError';
    this.attemptCount = attemptCount;
    this.lastError = lastError;
    Error.captureStackTrace(this, RetryAttemptsExhaustedError);
  }
}

/** Return random element from given array */
function random(arr) {
  const index = Math.floor(Math.random() * arr.length);
  return arr[index];
}

/**
 * Processes a payment charge request, validating card details and processing the transaction.
 * @param {Object} request - The payment request object
 * @param {Object} request.creditCard - The credit card details for the transaction
 * @param {string} request.creditCard.creditCardNumber - Full credit card number
 * @param {number} request.creditCard.creditCardExpirationYear - Credit card expiration year (4-digit)
 * @param {number} request.creditCard.creditCardExpirationMonth - Credit card expiration month (1-12)
 * @param {Object} request.amount - The charge amount details
 * @param {number} request.amount.units - Whole units of the currency (e.g. dollars)
 * @param {number} request.amount.nanos - Fractional units of the currency in nanos (1e-9 units)
 * @param {string} request.amount.currencyCode - 3-letter ISO 4217 currency code
 * @returns {Promise<{transactionId: string}>} Promise resolving to object containing the unique transaction ID on success
 * @throws {Error} If payment fails for any reason: invalid card details, expired card, unsupported card type, or random simulated failure
 */
async function charge(request) {
  const span = tracer.startSpan('charge');

  try {
    await withRetry(
      async () => await OpenFeature.setProviderAndWait(flagProvider),
      {
        serviceName: "openfeature-flagd",
        callType: "set-provider",
        maxAttempts: FLAGD_RETRY_MAX_ATTEMPTS,
        initialDelayMs: FLAGD_RETRY_INITIAL_DELAY_MS,
        isIdempotent: true
      }
    );

    const numberVariant = await withRetry(
      async () => await OpenFeature.getClient().getNumberValue("paymentFailure", 0),
      {
        serviceName: "openfeature-flagd",
        callType: "feature-flag-evaluation",
        maxAttempts: FLAGD_RETRY_MAX_ATTEMPTS,
        initialDelayMs: FLAGD_RETRY_INITIAL_DELAY_MS,
        isIdempotent: true
      }
    );

    if (numberVariant > 0) {
      // n% chance to fail with demo.user_context.loyalty_level=gold
      if (Math.random() < numberVariant) {
        span.setAttributes({'demo.user_context.loyalty_level': 'gold' });

        throw new Error('Payment request failed. Invalid token. demo.user_context.loyalty_level=gold');
      }
    }

    const {
      creditCardNumber: number,
      creditCardExpirationYear: year,
      creditCardExpirationMonth: month
    } = request.creditCard;
    const currentMonth = new Date().getMonth() + 1;
    const currentYear = new Date().getFullYear();
    const lastFourDigits = number.substr(-4);
    const transactionId = uuidv4();

    const card = cardValidator(number);
    const { card_type: cardType, valid } = card.getCardDetails();

    const loyalty_level = random(LOYALTY_LEVEL);

    span.setAttributes({
      'demo.payment.card_type': cardType,
      'demo.payment.card_valid': valid,
      'demo.user_context.loyalty_level': loyalty_level
    });

    if (!valid) {
      throw new Error('Credit card info is invalid.');
    }

    if (!['visa', 'mastercard'].includes(cardType)) {
      throw new Error(`Sorry, we cannot process ${cardType} credit cards. Only VISA or MasterCard is accepted.`);
    }

    if ((currentYear * 12 + currentMonth) > (year * 12 + month)) {
      throw new Error(`The credit card (ending ${lastFourDigits}) expired on ${month}/${year}.`);
    }

    // Check baggage for synthetic_request=true, and add charged attribute accordingly
    const baggage = propagation.getBaggage(context.active());
    if (baggage && baggage.getEntry('synthetic_request') && baggage.getEntry('synthetic_request').value === 'true') {
      span.setAttribute('demo.payment.charged', false);
    } else {
      span.setAttribute('demo.payment.charged', true);
    }

    const enduserId = baggage?.getEntry('enduser.id')?.value;
    if (enduserId) {
      span.setAttribute('enduser.id', enduserId);
    }

    const { units, nanos, currencyCode } = request.amount;
    logger.info({ transactionId, cardType, lastFourDigits, amount: { units, nanos, currencyCode }, loyalty_level }, 'Transaction complete.');
    transactionsCounter.add(1, { 'demo.payment.currency': currencyCode });

    return { transactionId };
  } catch (err) {
    span.recordException(err);
    span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });

    throw err;
  } finally {
    span.end();
  }
}

/**
 * Helper function to simulate payment gateway API call
 * @param {Object} paymentRequest Payment request object
 * @returns {Promise<Object>} Payment response
 */
async function callPaymentGateway(paymentRequest) {
  // This would be the actual API call to the payment processor in production
  // For demo purposes, we use the existing charge function
  return charge(paymentRequest);
}

/**
 * Circuit breaker setup for payment gateway calls
 */
const circuitBreakerOptions = {
  timeout: 10000, // 10 second timeout for requests
  errorThresholdPercentage: PAYMENT_CIRCUIT_BREAKER_ERROR_THRESHOLD,
  volumeThreshold: PAYMENT_CIRCUIT_BREAKER_VOLUME_THRESHOLD,
  resetTimeout: PAYMENT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS,
  rollingCountTimeout: 10000, // 10-second rolling window
  rollingCountBuckets: 10,
  name: 'payment-gateway'
};

const paymentCircuitBreaker = new CircuitBreaker(callPaymentGateway, circuitBreakerOptions);

// Update circuit breaker state metric and log state changes
paymentCircuitBreaker.on('open', () => {
  logger.warn({
    event: 'circuit_breaker_opened',
    errorThreshold: PAYMENT_CIRCUIT_BREAKER_ERROR_THRESHOLD,
    errorRate: paymentCircuitBreaker.stats.errorPercentage
  }, 'Payment circuit breaker opened');
  paymentCircuitBreakerState.set(1, { state: 'open' });
});

paymentCircuitBreaker.on('halfOpen', () => {
  logger.info({ event: 'circuit_breaker_half_open' }, 'Payment circuit breaker entered half-open state');
  paymentCircuitBreakerState.set(2, { state: 'half_open' });
});

paymentCircuitBreaker.on('close', () => {
  logger.info({ event: 'circuit_breaker_closed' }, 'Payment circuit breaker closed');
  paymentCircuitBreakerState.set(0, { state: 'closed' });
});

// Initialize circuit breaker state metric
paymentCircuitBreakerState.set(0, { state: 'closed' });

/**
 * Wraps payment gateway API calls with retry and circuit breaker logic
 * @param {Object} paymentRequest Idempotent payment request object
 * @returns Promise resolving to successful payment response
 * @throws {PaymentGatewayError} For non-retryable payment failures
 * @throws {CircuitBreakerOpenError} When circuit is open and requests are blocked
 * @throws {RetryAttemptsExhaustedError} When all retry attempts fail
 */
async function chargeWithFaultTolerance(paymentRequest) {
  let attempt = 0;
  let lastError = null;
  const maxAttempts = PAYMENT_RETRY_MAX_ATTEMPTS + 1; // initial attempt + retries

  while (attempt < maxAttempts) {
    try {
      attempt++;
      const response = await paymentCircuitBreaker.fire(paymentRequest);
      
      // Log and metric for successful call
      const status = attempt > 1 ? 'retried' : 'success';
      paymentGatewayCallsTotal.add(1, { status, error_type: '' });
      
      if (attempt > 1) {
        logger.info({
          event: 'payment_retry_succeeded',
          attempt,
          idempotencyKey: paymentRequest.idempotencyKey
        }, 'Payment succeeded after retry');
      }

      return response;
    } catch (error) {
      lastError = error;

      // Handle circuit breaker open error
      if (error.type === 'CircuitBreakerOpenError') {
        paymentGatewayCallsTotal.add(1, { status: 'failed', error_type: 'circuit_breaker_open' });
        throw new CircuitBreakerOpenError();
      }

      // Classify error as retryable or not
      let isRetryable = false;
      let errorType = 'unknown';
      if (error instanceof PaymentGatewayError) {
        isRetryable = error.isRetryable;
        errorType = `payment_gateway_${error.statusCode}`;
      } else if (error.name === 'TimeoutError') {
        isRetryable = true;
        errorType = 'timeout';
      } else if (error.message.includes('network') || error.code === 'ECONNRESET' || error.code === 'ENOTFOUND') {
        isRetryable = true;
        errorType = 'network_error';
      }

      // Log and metric for failed attempt
      paymentGatewayCallsTotal.add(1, { status: 'failed', error_type: errorType });
      logger.error({
        event: 'payment_attempt_failed',
        attempt,
        idempotencyKey: paymentRequest.idempotencyKey,
        error: error.message,
        isRetryable
      }, 'Payment attempt failed');

      // Check if we should retry
      if (!isRetryable || attempt >= maxAttempts) {
        break;
      }

      // Calculate exponential backoff with jitter
      const delay = PAYMENT_RETRY_INITIAL_DELAY_MS * Math.pow(2, attempt - 1);
      const jitter = delay * 0.1 * Math.random(); // 10% jitter
      const finalDelay = delay + jitter;

      logger.info({
        event: 'payment_retry_attempt',
        attempt,
        delay: finalDelay,
        idempotencyKey: paymentRequest.idempotencyKey
      }, `Retrying payment attempt in ${finalDelay}ms`);

      // Increment retry metric
      paymentRetryAttemptsTotal.add(1);

      // Wait before retrying
      await new Promise(resolve => setTimeout(resolve, finalDelay));
    }
  }

  // All attempts exhausted
  throw new RetryAttemptsExhaustedError(attempt, lastError);
}

/**
 * Health check function for the charge module
 * @returns {boolean} True if the module is healthy, false otherwise
 */
module.exports.isHealthy = () => {
  // Simple health check for now - returns true as long as module is loaded
  // In a real implementation this would check connectivity to payment processors, etc.
  return true;
};

module.exports.charge = charge;
module.exports.chargeWithFaultTolerance = chargeWithFaultTolerance;
module.exports.PaymentGatewayError = PaymentGatewayError;
module.exports.CircuitBreakerOpenError = CircuitBreakerOpenError;
module.exports.RetryAttemptsExhaustedError = RetryAttemptsExhaustedError;
