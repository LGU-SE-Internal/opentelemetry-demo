const { OpenFeature } = require('@openfeature/server-sdk');
const CircuitBreaker = require('opossum');
const promClient = require('prom-client');
const { withRetry } = require('./retry');
const logger = require('./logger');

// Initialize OpenFeature client
const client = OpenFeature.getClient('paymentservice');

// Metrics definitions (prometheus doesn't allow dots in metric names)
const stateGauge = new promClient.Gauge({
  name: 'feature_flag_circuit_breaker_state',
  help: 'Current circuit state: 0 = closed, 1 = open, 2 = half-open',
  labelNames: ['service'],
});
stateGauge.set({ service: 'paymentservice' }, 0); // Initial state: closed

const opensTotalCounter = new promClient.Counter({
  name: 'feature_flag_circuit_breaker_opens_total',
  help: 'Total number of times circuit has transitioned to open state',
  labelNames: ['service'],
});

const fallbackCallsCounter = new promClient.Counter({
  name: 'feature_flag_circuit_breaker_fallback_calls_total',
  help: 'Total number of flag calls that returned defaultValue due to open circuit or evaluation failure',
  labelNames: ['service'],
});

// Circuit breaker configuration
const CIRCUIT_BREAKER_OPTIONS = {
  timeout: 10000, // 10s timeout per call
  errorThresholdPercentage: 100, // Open when 100% of requests fail
  rollingCountTimeout: 10000,
  rollingCountBuckets: 10,
  resetTimeout: 30000, // 30s reset timeout
  volumeThreshold: 5, // 5 consecutive failures to open
  errorFilter: (error) => {
    // Count all errors as failures for circuit breaker
    return true;
  }
};

/**
 * Internal function that executes flag evaluation with retries
 * @param {string} flagKey Key of the feature flag to evaluate
 * @param {any} defaultValue Fallback value
 * @param {import('@openfeature/server-sdk').EvaluationContext} [context] Optional evaluation context
 * @returns {Promise<any>} Evaluated flag value
 */
async function evaluateFlagWithRetry(flagKey, defaultValue, context) {
  return withRetry(async () => {
    // Handle different flag types based on defaultValue type
    if (typeof defaultValue === 'boolean') {
      return client.getBooleanValue(flagKey, defaultValue, context);
    }
    if (typeof defaultValue === 'string') {
      return client.getStringValue(flagKey, defaultValue, context);
    }
    if (typeof defaultValue === 'number') {
      return client.getNumberValue(flagKey, defaultValue, context);
    }
    return client.getObjectValue(flagKey, defaultValue, context);
  }, {
    serviceName: 'flagd',
    callType: 'flag_evaluation',
    maxAttempts: 3,
    initialDelayMs: 100,
    isIdempotent: true, // Flag evaluation is idempotent
  });
}

// Create circuit breaker wrapping the retry-enabled evaluation function
const breaker = new CircuitBreaker(evaluateFlagWithRetry, CIRCUIT_BREAKER_OPTIONS);

// Circuit breaker event handlers for metrics
breaker.on('open', () => {
  logger.warn('Feature flag circuit breaker OPEN: all flag calls will return default values for 30 seconds');
  stateGauge.set({ service: 'paymentservice' }, 1);
  opensTotalCounter.inc({ service: 'paymentservice' });
});

breaker.on('halfOpen', () => {
  logger.info('Feature flag circuit breaker HALF-OPEN: testing flagd connectivity with next call');
  stateGauge.set({ service: 'paymentservice' }, 2);
});

breaker.on('close', () => {
  logger.info('Feature flag circuit breaker CLOSED: normal flag evaluation resumed');
  stateGauge.set({ service: 'paymentservice' }, 0);
});

// Fallback function: returns defaultValue and increments fallback counter
breaker.fallback(async (flagKey, defaultValue, context, error) => {
  fallbackCallsCounter.inc({ service: 'paymentservice' });
  if (error) {
    logger.debug(`Flag evaluation failed, returning default value for key ${flagKey}: ${error.message}`, {
      flagKey,
      errorMessage: error.message,
    });
  }
  return defaultValue;
});

/**
 * Circuit-breaker wrapped feature flag evaluation function
 * @template T
 * @param {string} flagKey Key of the feature flag to evaluate
 * @param {T} defaultValue Fallback value to return if flag evaluation fails or circuit is open
 * @param {import('@openfeature/server-sdk').EvaluationContext} [context] Optional OpenFeature evaluation context
 * @returns {Promise<T>} Evaluated flag value (or default if circuit open/evaluation fails)
 */
async function getFlagValue(flagKey, defaultValue, context) {
  return breaker.fire(flagKey, defaultValue, context);
}

module.exports = {
  getFlagValue,
};
