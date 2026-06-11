import { status as GrpcStatus } from '@grpc/grpc-js';
import CircuitBreaker from 'opossum';
import { metrics } from '@opentelemetry/api';

// Interfaces
export interface GrpcRetryConfig {
  maxAttempts: number;
  initialDelayMs: number;
  maxDelayMs: number;
  circuitBreaker: {
    errorThresholdPercentage: number;
    resetTimeoutMs: number;
  };
}

export interface RetryMetrics {
  incrementRetryAttempts(service: string, method: string): void;
  incrementSuccessfulRetries(service: string, method: string): void;
  incrementFailedRetries(service: string, method: string): void;
  incrementCircuitBreakerTripped(service: string): void;
}

// Read-only gRPC method list (idempotent)
export const READ_ONLY_GRPC_METHODS: ReadonlySet<string> = new Set([
  'GetProduct',
  'ListProducts',
  'GetCart',
  'ListRecommendations',
  'GetShippingQuote',
  'GetUserProfile'
]);

// Custom error for circuit breaker open state
export class CircuitBreakerOpenError extends Error {
  constructor(service: string) {
    super(`Circuit breaker is open for service ${service}`);
    this.name = 'CircuitBreakerOpenError';
  }
}

// Load configuration from environment variables
const loadConfig = (): GrpcRetryConfig => {
  return {
    maxAttempts: parseInt(process.env.FRONTEND_GRPC_RETRY_MAX_ATTEMPTS || '3', 10),
    initialDelayMs: parseInt(process.env.FRONTEND_GRPC_RETRY_INITIAL_DELAY_MS || '100', 10),
    maxDelayMs: parseInt(process.env.FRONTEND_GRPC_RETRY_MAX_DELAY_MS || '2000', 10),
    circuitBreaker: {
      errorThresholdPercentage: parseInt(process.env.FRONTEND_GRPC_CIRCUIT_BREAKER_ERROR_THRESHOLD || '50', 10),
      resetTimeoutMs: parseInt(process.env.FRONTEND_GRPC_CIRCUIT_BREAKER_RESET_TIMEOUT_MS || '10000', 10),
    }
  };
};

export let config: GrpcRetryConfig = loadConfig();

// Initialize OpenTelemetry metrics
const meter = metrics.getMeter('frontend-grpc-retry');
const retryAttemptsCounter = meter.createCounter('retry_attempts_total', {
  description: 'Total number of gRPC retry attempts'
});
const successfulRetriesCounter = meter.createCounter('successful_retries_total', {
  description: 'Total number of successful gRPC retries'
});
const failedRetriesCounter = meter.createCounter('failed_retries_total', {
  description: 'Total number of failed gRPC retries (all attempts exhausted)'
});
const circuitBreakerTrippedCounter = meter.createCounter('circuit_breaker_tripped_total', {
  description: 'Total number of times a circuit breaker was tripped for a service'
});

export const metricsImpl: RetryMetrics = {
  incrementRetryAttempts: (service: string, method: string) => {
    retryAttemptsCounter.add(1, { service, method });
  },
  incrementSuccessfulRetries: (service: string, method: string) => {
    successfulRetriesCounter.add(1, { service, method });
  },
  incrementFailedRetries: (service: string, method: string) => {
    failedRetriesCounter.add(1, { service, method });
  },
  incrementCircuitBreakerTripped: (service: string) => {
    circuitBreakerTrippedCounter.add(1, { service });
  }
};

// Allow overriding metrics for testing
let currentMetrics: RetryMetrics = metricsImpl;

export const setMetrics = (metrics: RetryMetrics) => {
  currentMetrics = metrics;
};

// Reload config and clear circuit breakers (for testing)
export const reloadConfig = () => {
  config = loadConfig();
  circuitBreakers.clear();
};

// Circuit breaker cache per service
const circuitBreakers: Map<string, CircuitBreaker> = new Map();

const getCircuitBreaker = (serviceName: string): CircuitBreaker => {
  if (circuitBreakers.has(serviceName)) {
    return circuitBreakers.get(serviceName)!;
  }

  const options = {
    errorThresholdPercentage: config.circuitBreaker.errorThresholdPercentage,
    resetTimeout: config.circuitBreaker.resetTimeoutMs,
    name: serviceName,
  };

  const breaker = new CircuitBreaker(async (fn: () => Promise<any>) => fn(), options);

  breaker.on('open', () => {
    currentMetrics.incrementCircuitBreakerTripped(serviceName);
  });

  circuitBreakers.set(serviceName, breaker);
  return breaker;
};

// Helper to check if error is transient and retryable
const isRetryableError = (error: any): boolean => {
  if (!error || !('code' in error)) return false;

  const retryableCodes = [
    GrpcStatus.UNAVAILABLE,
    GrpcStatus.DEADLINE_EXCEEDED,
    GrpcStatus.RESOURCE_EXHAUSTED,
    GrpcStatus.ABORTED,
    // Only INTERNAL for connection reset errors
    GrpcStatus.INTERNAL
  ];

  if (error.code === GrpcStatus.INTERNAL) {
    return error.message?.includes('connection reset') || false;
  }

  return retryableCodes.includes(error.code);
};

// Helper for exponential backoff delay
const delay = (attempt: number): Promise<void> => {
  const delayMs = Math.min(
    config.initialDelayMs * Math.pow(2, attempt - 1),
    config.maxDelayMs
  );
  return new Promise(resolve => setTimeout(resolve, delayMs));
};

export async function withGrpcRetry<T>(
  serviceName: string,
  methodName: string,
  grpcCall: () => Promise<T>,
  isIdempotent: boolean
): Promise<T> {
  const breaker = getCircuitBreaker(serviceName);

  if (breaker.opened) {
    throw new CircuitBreakerOpenError(serviceName);
  }

  // Non-idempotent calls: no retry, just run once through circuit breaker
  if (!isIdempotent) {
    try {
      return await breaker.fire(grpcCall);
    } catch (error) {
      throw error;
    }
  }

  // Idempotent calls: retry logic
  let attempt = 1;
  const maxAttempts = config.maxAttempts;

  while (attempt <= maxAttempts) {
    try {
      const result = await breaker.fire(grpcCall);
      // If we succeeded after first attempt, count as successful retry
      if (attempt > 1) {
        currentMetrics.incrementSuccessfulRetries(serviceName, methodName);
      }
      return result;
    } catch (error) {
      // Check if error is retryable and we have attempts left
      if (attempt < maxAttempts && isRetryableError(error)) {
        currentMetrics.incrementRetryAttempts(serviceName, methodName);
        // Wait for backoff before next attempt
        await delay(attempt);
        attempt++;
      } else {
        // No more attempts or non-retryable error
        if (attempt > 1) {
          currentMetrics.incrementFailedRetries(serviceName, methodName);
        }
        throw error;
      }
    }
  }

  // Should never reach here, just in case
  throw new Error('Unexpected error in retry logic');
}
