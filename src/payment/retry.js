const logger = require('./logger');
const { metrics } = require('./opentelemetry');

// Default retryable error codes
const DEFAULT_RETRYABLE_ERROR_CODES = ["ECONNRESET", "ETIMEDOUT", "ECONNREFUSED", "500", "502", "503", "504", "429"];

// Custom error types
class MaxRetriesExceededError extends Error {
  constructor(message, lastError) {
    super(message);
    this.name = 'MaxRetriesExceededError';
    this.lastError = lastError;
  }
}

class NonIdempotentRetryAttemptError extends Error {
  constructor(message) {
    super(message);
    this.name = 'NonIdempotentRetryAttemptError';
  }
}

/**
 * Wraps an external service call with exponential backoff retry logic
 * @param callFn Function that executes the external service call
 * @param options Retry configuration options
 * @returns Result of the successful call
 * @throws Last encountered error if all retries fail
 */
async function withRetry(callFn, options) {
  const {
    serviceName,
    callType,
    maxAttempts,
    initialDelayMs,
    isIdempotent,
    retryableErrorCodes = DEFAULT_RETRYABLE_ERROR_CODES
  } = options;

  const retryIdempotencyOnly = process.env.PAYMENT_SERVICE_RETRY_IDEMPOTENCY_ONLY !== 'false';

  if (retryIdempotencyOnly && !isIdempotent) {
    throw new NonIdempotentRetryAttemptError(
      `Retry attempted on non-idempotent call ${serviceName}:${callType} when PAYMENT_SERVICE_RETRY_IDEMPOTENCY_ONLY is true`
    );
  }

  let attempt = 1;
  let lastError = null;

  while (attempt <= maxAttempts) {
    try {
      const result = await callFn();
      if (attempt > 1) {
        // Emit success metric if we succeeded after retries
        metrics.getCounter('external_call.retry_successes').add(1, {
          service_name: serviceName,
          call_type: callType
        });
      }
      return result;
    } catch (error) {
      lastError = error;

      // Check if error is retryable
      const errorCode = error.code || (error.statusCode ? String(error.statusCode) : null);
      const isRetryable = errorCode && retryableErrorCodes.includes(errorCode);

      if (!isRetryable || attempt >= maxAttempts) {
        if (attempt > 1) {
          // Emit failure metric if we tried multiple times and failed
          metrics.getCounter('external_call.retry_failures').add(1, {
            service_name: serviceName,
            call_type: callType
          });
        }
        if (attempt >= maxAttempts && isRetryable) {
          throw new MaxRetriesExceededError(
            `Max retries (${maxAttempts}) exceeded for ${serviceName}:${callType}`,
            lastError
          );
        }
        throw lastError;
      }

      // Emit retry attempt metric
      metrics.getCounter('external_call.retry_attempts').add(1, {
        service_name: serviceName,
        call_type: callType,
        attempt_number: attempt
      });

      // Log retry attempt
      logger.warn(
        `Retry attempt ${attempt} for ${serviceName}:${callType} failed with error: ${error.message}`,
        {
          serviceName,
          callType,
          attempt,
          errorMessage: error.message,
          errorCode
        }
      );

      // Calculate delay: initialDelayMs * 2^(attempt - 1)
      const delay = initialDelayMs * (2 ** (attempt - 1));
      await new Promise(resolve => setTimeout(resolve, delay));

      attempt++;
    }
  }

  // This line should never be reached
  throw new MaxRetriesExceededError(
    `Max retries (${maxAttempts}) exceeded for ${serviceName}:${callType}`,
    lastError
  );
}

module.exports = {
  withRetry,
  MaxRetriesExceededError,
  NonIdempotentRetryAttemptError,
  DEFAULT_RETRYABLE_ERROR_CODES
};
