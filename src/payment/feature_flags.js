const { OpenFeature } = require('@openfeature/server-sdk');
const CircuitBreaker = require('opossum');
const promClient = require('prom-client');
const { withRetry } = require('./retry');
const logger = require('./logger');

// Initialize OpenFeature client
const client = OpenFeature.getClient('paymentservice');

// Metrics definitions matching issue requirements
const stateGauge = new promClient.Gauge({
  name: 'payment_flagd_circuit_breaker_state',
  help: 'Current state of the flagd circuit breaker',
  labelNames: ['state'],
});
// Initialize all states to 0, set closed to 1 initially
stateGauge.set({ state: 'closed' }, 1);
stateGauge.set({ state: 'open' }, 0);
stateGauge.set({ state: 'half_open' }, 0);

const failureCountCounter = new promClient.Counter({
  name: 'payment_flagd_circuit_breaker_failure_count',
  help: 'Total number of failed flagd calls',
});
failureCountCounter.inc(0); // Initialize to 0

const circuitOpenCountCounter = new promClient.Counter({
  name: 'payment_flagd_circuit_breaker_circuit_open_count',
  help: 'Total number of times the circuit has transitioned to open state',
});
circuitOpenCountCounter.inc(0); // Initialize to 0

const fallbackUsedCountCounter = new promClient.Counter({
  name: 'payment_flagd_circuit_breaker_fallback_used_count',
  help: 'Total number of times default flag values were used due to open circuit',
});
fallbackUsedCountCounter.inc(0); // Initialize to 0

// Circuit breaker configuration from environment variables with defaults
const CIRCUIT_BREAKER_OPTIONS = {
  timeout: 10000, // 10s timeout per call
  errorThresholdPercentage: 100, // Open when 100% of requests fail
  rollingCountTimeout: 10000,
  rollingCountBuckets: 10,
  resetTimeout: parseInt(process.env.FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS || '30000', 10),
  volumeThreshold: parseInt(process.env.FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD || '5', 10),
  errorFilter: (error) => {
    // Count all errors as failures for circuit breaker
    return true;
  }
};

/**
 * Internal function that executes flag evaluation with retries
 * @param {string} flagName Name of the feature flag to evaluate
 * @param {any} defaultValue Fallback value
 * @param {import('@openfeature/server-sdk').EvaluationContext} [context] Optional evaluation context
 * @returns {Promise<any>} Evaluated flag value
 */
async function evaluateFlagWithRetry(flagName, defaultValue, context) {
  try {
    return await withRetry(async () => {
      // Handle different flag types based on defaultValue type
      if (typeof defaultValue === 'boolean') {
        return client.getBooleanValue(flagName, defaultValue, context);
      }
      if (typeof defaultValue === 'string') {
        return client.getStringValue(flagName, defaultValue, context);
      }
      if (typeof defaultValue === 'number') {
        return client.getNumberValue(flagName, defaultValue, context);
      }
      return client.getObjectValue(flagName, defaultValue, context);
    }, {
      serviceName: 'flagd',
      callType: 'flag_evaluation',
      maxAttempts: 3,
      initialDelayMs: 100,
      isIdempotent: true, // Flag evaluation is idempotent
    });
  } catch (error) {
    // Increment failure counter for all failed calls
    failureCountCounter.inc(1);
    throw error;
  }
}

// Create circuit breaker wrapping the retry-enabled evaluation function
const breaker = new CircuitBreaker(evaluateFlagWithRetry, CIRCUIT_BREAKER_OPTIONS);

// Circuit breaker event handlers for metrics
breaker.on('open', () => {
  logger.warn('Feature flag circuit breaker OPEN: all flag calls will return default values for 30 seconds');
  stateGauge.set({ state: 'closed' }, 0);
  stateGauge.set({ state: 'open' }, 1);
  stateGauge.set({ state: 'half_open' }, 0);
  circuitOpenCountCounter.inc(1);
});

breaker.on('halfOpen', () => {
  logger.info('Feature flag circuit breaker HALF-OPEN: testing flagd connectivity with next call');
  stateGauge.set({ state: 'closed' }, 0);
  stateGauge.set({ state: 'open' }, 0);
  stateGauge.set({ state: 'half_open' }, 1);
});

breaker.on('close', () => {
  logger.info('Feature flag circuit breaker CLOSED: normal flag evaluation resumed');
  stateGauge.set({ state: 'closed' }, 1);
  stateGauge.set({ state: 'open' }, 0);
  stateGauge.set({ state: 'half_open' }, 0);
});

// Fallback function: returns defaultValue and increments fallback counter
breaker.fallback(async (flagName, defaultValue, context, error) => {
  fallbackUsedCountCounter.inc(1);
  if (error) {
    logger.debug(`Flag evaluation failed, returning default value for key ${flagName}: ${error.message}`, {
      flagName,
      errorMessage: error.message,
    });
  }
  return defaultValue;
});

/**
 * Wraps flagd feature flag evaluation with circuit breaker logic
 * @param flagName Name of the feature flag to evaluate
 * @param defaultValue Fallback value if flagd call fails (used when circuit is closed but call fails)
 * @param context Evaluation context passed to flagd
 * @returns Evaluated flag value (either from flagd, or configured default when circuit is open)
 */
async function evaluateFlagWithCircuitBreaker(
  flagName,
  defaultValue,
  context
) {
  return breaker.fire(flagName, defaultValue, context);
}

// Attach circuit breaker to function for test access
evaluateFlagWithCircuitBreaker.circuit = breaker;

module.exports = {
  evaluateFlagWithCircuitBreaker,
};
