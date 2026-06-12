// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const { context, propagation, trace, metrics, SpanStatusCode } = require('@opentelemetry/api');
const cardValidator = require('simple-card-validator');
const { v4: uuidv4 } = require('uuid');
const pRetry = require('p-retry');
const CircuitBreaker = require('opossum');

const { OpenFeature } = require('@openfeature/server-sdk');
const { FlagdProvider } = require('@openfeature/flagd-provider');
const flagProvider = new FlagdProvider();

const logger = require('./logger');
const { withRetry } = require('./retry');
const tracer = trace.getTracer('payment');
const meter = metrics.getMeter('payment');
const transactionsCounter = meter.createCounter('demo.payment.transactions');

// Resilience metrics
const retryAttemptsCounter = meter.createCounter('payment.processor.retry.attempts', {
  description: 'Number of retry attempts made for payment requests'
});
const circuitBreakerStateTransitionsCounter = meter.createCounter('payment.processor.circuit_breaker.state_transitions', {
  description: 'Number of circuit breaker state changes'
});
const fallbackCallsCounter = meter.createCounter('payment.processor.fallback.calls', {
  description: 'Number of times fallback logic was invoked'
});
const callDurationHistogram = meter.createHistogram('payment.processor.call.duration', {
  description: 'Duration of payment processor API calls including retries',
  unit: 'ms'
});

// Configuration from environment variables with defaults
const PAYMENT_PROCESSOR_MAX_RETRIES = parseInt(process.env.PAYMENT_PROCESSOR_MAX_RETRIES || '3', 10);
const PAYMENT_PROCESSOR_RETRY_INITIAL_DELAY_MS = parseInt(process.env.PAYMENT_PROCESSOR_RETRY_INITIAL_DELAY_MS || '100', 10);
const PAYMENT_PROCESSOR_CIRCUIT_BREAKER_ERROR_THRESHOLD = parseFloat(process.env.PAYMENT_PROCESSOR_CIRCUIT_BREAKER_ERROR_THRESHOLD || '0.5');
const PAYMENT_PROCESSOR_CIRCUIT_BREAKER_SLID_WINDOW_SIZE_MS = parseInt(process.env.PAYMENT_PROCESSOR_CIRCUIT_BREAKER_SLID_WINDOW_SIZE_MS || '10000', 10);
const PAYMENT_PROCESSOR_CIRCUIT_BREAKER_COOLDOWN_MS = parseInt(process.env.PAYMENT_PROCESSOR_CIRCUIT_BREAKER_COOLDOWN_MS || '30000', 10);
const PAYMENT_PROCESSOR_CIRCUIT_BREAKER_MINIMUM_CALLS = parseInt(process.env.PAYMENT_PROCESSOR_CIRCUIT_BREAKER_MINIMUM_CALLS || '10', 10);

// Custom error classes
class PaymentProcessorError extends Error {
  constructor(message) {
    super(message);
    this.name = 'PaymentProcessorError';
  }
}

class PaymentTransientError extends PaymentProcessorError {
  constructor(message) {
    super(message);
    this.name = 'PaymentTransientError';
  }
}

class PaymentPermanentError extends PaymentProcessorError {
  constructor(message) {
    super(message);
    this.name = 'PaymentPermanentError';
  }
}

class CircuitBreakerOpenError extends PaymentProcessorError {
  constructor(message) {
    super(message || 'Circuit breaker is open, payment requests are blocked');
    this.name = 'CircuitBreakerOpenError';
  }
}

// Retry configuration for flagd calls
const FLAGD_RETRY_MAX_ATTEMPTS = parseInt(process.env.PAYMENT_SERVICE_FLAGD_RETRY_MAX_ATTEMPTS || '3', 10);
const FLAGD_RETRY_INITIAL_DELAY_MS = parseInt(process.env.PAYMENT_SERVICE_FLAGD_RETRY_INITIAL_DELAY_MS || '100', 10);

// Circuit breaker setup
const circuitBreakerOptions = {
  timeout: false, // We handle timeouts at the request level
  errorThresholdPercentage: PAYMENT_PROCESSOR_CIRCUIT_BREAKER_ERROR_THRESHOLD * 100,
  rollingCountTimeout: PAYMENT_PROCESSOR_CIRCUIT_BREAKER_SLID_WINDOW_SIZE_MS,
  resetTimeout: PAYMENT_PROCESSOR_CIRCUIT_BREAKER_COOLDOWN_MS,
  volumeThreshold: PAYMENT_PROCESSOR_CIRCUIT_BREAKER_MINIMUM_CALLS,
  errorFilter: (err) => {
    // Only count transient errors towards circuit breaker opening
    return err instanceof PaymentTransientError;
  }
};

// Payment processing function wrapped in circuit breaker
async function processPaymentWithCircuitBreaker(request, idempotencyKey) {
  // This simulates the external payment processor API call
  // In production, this would make an actual HTTP/GRPC call to the payment processor
  // with the idempotency key as a header
  const startTime = Date.now();
  let status = 'success';
  let circuitState = circuitBreaker.state.name;

  try {
    // Simulate 15% chance of transient failure (5xx, network error, timeout)
    const shouldFailTransient = Math.random() < 0.15;
    if (shouldFailTransient) {
      throw new PaymentTransientError('Payment processor returned 503 Service Unavailable');
    }

    // Simulate 10% chance of permanent failure (4xx, invalid payment)
    const shouldFailPermanent = Math.random() < 0.10;
    if (shouldFailPermanent) {
      throw new PaymentPermanentError('Payment processor returned 400 Bad Request');
    }

    // Simulate successful payment processing
    const transactionId = uuidv4();
    return { transactionId, status: 'success', processorMetadata: { idempotencyKey } };
  } catch (err) {
    status = 'failed';
    throw err;
  } finally {
    const durationMs = Date.now() - startTime;
    callDurationHistogram.record(durationMs, { status, circuit_state: circuitState });
  }
}

const circuitBreaker = new CircuitBreaker(processPaymentWithCircuitBreaker, circuitBreakerOptions);

// Track circuit breaker state transitions
circuitBreaker.on('open', () => {
  circuitBreakerStateTransitionsCounter.add(1, { from_state: 'closed', to_state: 'open' });
  logger.info('Circuit breaker opened');
});

circuitBreaker.on('halfOpen', () => {
  circuitBreakerStateTransitionsCounter.add(1, { from_state: 'open', to_state: 'half_open' });
  logger.info('Circuit breaker entered half-open state');
});

circuitBreaker.on('close', () => {
  circuitBreakerStateTransitionsCounter.add(1, { from_state: 'half_open', to_state: 'closed' });
  logger.info('Circuit breaker closed');
});

// Wrap circuit breaker calls to handle open circuit error
async function circuitBreakerWrapper(request, idempotencyKey) {
  try {
    return await circuitBreaker.fire(request, idempotencyKey);
  } catch (err) {
    if (err.type === 'CircuitOpenError') {
      fallbackCallsCounter.add(1, { reason: 'circuit_open' });
      throw new CircuitBreakerOpenError();
    }
    throw err;
  }
}

/**
 * Wraps the external payment processor API call with retry and circuit breaker protection.
 * @param {Object} request - Payment charge request object
 * @param {string} idempotencyKey - Unique string key to ensure duplicate charges are not created on retry
 * @returns {Promise<Object>} Successful payment charge response
 * @throws {PaymentTransientError} For retriable errors
 * @throws {PaymentPermanentError} For non-retriable errors
 * @throws {CircuitBreakerOpenError} When circuit is open
 * @throws {PaymentProcessorError} Base error class
 */
async function chargePayment(request, idempotencyKey) {
  try {
    return await pRetry(
      async (attemptNumber) => {
        if (attemptNumber > 1) {
          logger.info(`Retrying payment request, attempt ${attemptNumber} of ${PAYMENT_PROCESSOR_MAX_RETRIES}`);
        }

        try {
          const result = await circuitBreakerWrapper(request, idempotencyKey);
          if (attemptNumber > 1) {
            retryAttemptsCounter.add(1, { outcome: 'success' });
          }
          return result;
        } catch (err) {
          if (attemptNumber > 1) {
            retryAttemptsCounter.add(1, { outcome: 'failed' });
          }

          // Only retry transient errors
          if (err instanceof PaymentTransientError) {
            throw err; // p-retry will retry
          } else {
            // Permanent error or circuit open, abort retries
            throw new pRetry.AbortError(err);
          }
        }
      },
      {
        retries: PAYMENT_PROCESSOR_MAX_RETRIES,
        factor: 2, // Exponential backoff
        minTimeout: PAYMENT_PROCESSOR_RETRY_INITIAL_DELAY_MS,
        randomize: true
      }
    );
  } catch (err) {
    // If retries are exhausted
    if (err instanceof pRetry.AbortError) {
      throw err.originalError;
    }
    fallbackCallsCounter.add(1, { reason: 'retries_exhausted' });
    throw err;
  }
}

const LOYALTY_LEVEL = ['platinum', 'gold', 'silver', 'bronze'];

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
// Connectivity check configuration
const CONNECTIVITY_TIMEOUT_MS = parseInt(process.env.PAYMENT_PROCESSOR_CONNECTIVITY_TIMEOUT_MS || '1000', 10);

/**
 * Simulates check for external payment processor connectivity
 * @returns {Promise<boolean>} True if connection succeeds, false if fails (10% failure rate)
 */
async function checkPaymentProcessorConnectivity() {
  return new Promise((resolve) => {
    // Simulate 10% failure rate for testing
    const shouldFail = Math.random() < 0.1;
    // Simulate varying response time between 0 and 2000ms
    const responseTime = Math.floor(Math.random() * 2000);
    
    setTimeout(() => {
      resolve(!shouldFail);
    }, responseTime);
  });
}

/**
 * Health check function for the charge module
 * @returns {Promise<boolean>} True if the module is healthy and can process payments, false otherwise
 */
module.exports.isHealthy = async () => {
  const startTime = Date.now();
  let timeoutId;

  try {
    const timeoutPromise = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        reject(new Error('Payment processor connectivity check timed out'));
      }, CONNECTIVITY_TIMEOUT_MS);
    });

    const isConnected = await Promise.race([
      checkPaymentProcessorConnectivity(),
      timeoutPromise
    ]);

    clearTimeout(timeoutId);

    if (!isConnected) {
      throw new Error('Payment processor connectivity check failed');
    }

    return true;
  } catch (err) {
    const durationMs = Date.now() - startTime;
    logger.error({
      timestamp: new Date().toISOString(),
      event: 'payment_processor_health_check_failed',
      error: err.message,
      duration_ms: durationMs,
      configured_timeout_ms: CONNECTIVITY_TIMEOUT_MS
    });
    return false;
  }
};

// Export for testing purposes
module.exports.checkPaymentProcessorConnectivity = checkPaymentProcessorConnectivity;
module.exports.chargePayment = chargePayment;
module.exports.PaymentProcessorError = PaymentProcessorError;
module.exports.PaymentTransientError = PaymentTransientError;
module.exports.PaymentPermanentError = PaymentPermanentError;
module.exports.CircuitBreakerOpenError = CircuitBreakerOpenError;
module.exports.circuitBreaker = circuitBreaker;
module.exports.config = {
  PAYMENT_PROCESSOR_MAX_RETRIES,
  PAYMENT_PROCESSOR_RETRY_INITIAL_DELAY_MS,
  PAYMENT_PROCESSOR_CIRCUIT_BREAKER_ERROR_THRESHOLD,
  PAYMENT_PROCESSOR_CIRCUIT_BREAKER_SLID_WINDOW_SIZE_MS,
  PAYMENT_PROCESSOR_CIRCUIT_BREAKER_COOLDOWN_MS,
  PAYMENT_PROCESSOR_CIRCUIT_BREAKER_MINIMUM_CALLS
};

module.exports.charge = async request => {
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

    // Generate idempotency key for this payment request
    const idempotencyKey = uuidv4();
    const { units, nanos, currencyCode } = request.amount;

    // Process payment with resilience protections
    const paymentResult = await chargePayment(request, idempotencyKey);

    logger.info({ transactionId: paymentResult.transactionId, cardType, lastFourDigits, amount: { units, nanos, currencyCode }, loyalty_level, idempotencyKey }, 'Transaction complete.');
    transactionsCounter.add(1, { 'demo.payment.currency': currencyCode });

    return { transactionId: paymentResult.transactionId };
  } catch (err) {
    span.recordException(err);
    span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });

    throw err;
  } finally {
    span.end();
  }
};
