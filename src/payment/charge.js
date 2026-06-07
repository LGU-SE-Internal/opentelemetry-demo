// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const { context, propagation, trace, metrics, SpanStatusCode } = require('@opentelemetry/api');
const cardValidator = require('simple-card-validator');
const { v4: uuidv4 } = require('uuid');

const { OpenFeature } = require('@openfeature/server-sdk');
const { FlagdProvider } = require('@openfeature/flagd-provider');
const flagProvider = new FlagdProvider();

const logger = require('./logger');
const { withRetry } = require('./retry');
const tracer = trace.getTracer('payment');
const meter = metrics.getMeter('payment');
const transactionsCounter = meter.createCounter('demo.payment.transactions');

// Retry configuration for flagd calls
const FLAGD_RETRY_MAX_ATTEMPTS = parseInt(process.env.PAYMENT_SERVICE_FLAGD_RETRY_MAX_ATTEMPTS || '3', 10);
const FLAGD_RETRY_INITIAL_DELAY_MS = parseInt(process.env.PAYMENT_SERVICE_FLAGD_RETRY_INITIAL_DELAY_MS || '100', 10);

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
/**
 * Health check function for the charge module
 * @returns {boolean} True if the module is healthy, false otherwise
 */
module.exports.isHealthy = () => {
  // Simple health check for now - returns true as long as module is loaded
  // In a real implementation this would check connectivity to payment processors, etc.
  return true;
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
};
