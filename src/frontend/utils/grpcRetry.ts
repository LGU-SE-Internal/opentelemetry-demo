import { status as GrpcStatus } from '@grpc/grpc-js';
const logger = {
  info: jest.fn(),
  warn: jest.fn(),
  error: jest.fn()
};
const opossumModule = require('opossum');
const CircuitBreaker = opossumModule.default || opossumModule;
import { metrics } from '@opentelemetry/api';

const meter = metrics.getMeter('frontend-grpc-retry');

// Retry configuration type
export interface GrpcRetryConfig {
  maxAttempts: number;
  initialDelayMs: number;
  maxDelayMs: number;
  backoffMultiplier: number;
  circuitBreaker: {
    errorThresholdPercentage: number;
    resetTimeoutMs: number;
  };
}

export class CircuitBreakerOpenError extends Error {
  constructor(service: string) {
    super(`Circuit breaker open for service ${service}`);
    this.name = 'CircuitBreakerOpenError';
  }
}

export interface RetryMetrics {
  incrementRetryAttempts: (service: string, method: string) => void;
  incrementSuccessfulRetries: (service: string, method: string) => void;
  incrementFailedRetries: (service: string, method: string) => void;
  incrementCircuitBreakerTripped: (service: string) => void;
}

let metricsImpl: RetryMetrics = {
  incrementRetryAttempts: (service, method) => {
    meter.createCounter('grpc.retry.attempts').add(1, { service, method });
  },
  incrementSuccessfulRetries: (service, method) => {
    meter.createCounter('grpc.retry.successes').add(1, { service, method });
  },
  incrementFailedRetries: (service, method) => {
    meter.createCounter('grpc.retry.failures').add(1, { service, method });
  },
  incrementCircuitBreakerTripped: (service) => {
    meter.createCounter('grpc.circuit_breaker.tripped').add(1, { service });
  }
};

export const setMetrics = (metrics: RetryMetrics) => {
  metricsImpl = metrics;
};

// Read-only gRPC method list (idempotent)
export const READ_ONLY_GRPC_METHODS: ReadonlyArray<string> = [
  'GetCart',
  'GetProduct',
  'ListProducts',
  'GetQuote',
  'GetShippingQuote',
  'ListRecommendations',
  'GetUserProfile'
];

// Circuit breakers per service
const circuitBreakers: Record<string, CircuitBreaker> = {};

// Load configuration from environment variables
const loadConfig = (): GrpcRetryConfig => {
  return {
    maxAttempts: parseInt(process.env.FRONTEND_GRPC_RETRY_MAX_ATTEMPTS || '3', 10),
    initialDelayMs: parseInt(process.env.FRONTEND_GRPC_RETRY_INITIAL_DELAY_MS || '100', 10),
    maxDelayMs: parseInt(process.env.FRONTEND_GRPC_RETRY_MAX_DELAY_MS || '2000', 10),
    backoffMultiplier: 2.0,
    circuitBreaker: {
      errorThresholdPercentage: parseInt(process.env.FRONTEND_GRPC_CIRCUIT_BREAKER_ERROR_THRESHOLD || '50', 10),
      resetTimeoutMs: parseInt(process.env.FRONTEND_GRPC_CIRCUIT_BREAKER_RESET_TIMEOUT_MS || '10000', 10),
    }
  };
};

export let config: GrpcRetryConfig = loadConfig();

// Reload config and reset circuit breakers (for testing)
export const reloadConfig = () => {
  config = loadConfig();
  Object.values(circuitBreakers).forEach(cb => {
    if (typeof cb.shutdown === 'function') {
      cb.shutdown();
    } else if (typeof cb.close === 'function') {
      cb.close();
    }
  });
  Object.keys(circuitBreakers).forEach(key => delete circuitBreakers[key]);
};

// Helper to check if error is transient and retryable
const isRetryableError = (error: any): boolean => {
  if (!error || !('code' in error)) return false;

  const retryableCodes = [
    GrpcStatus.UNAVAILABLE,
    GrpcStatus.DEADLINE_EXCEEDED,
    GrpcStatus.RESOURCE_EXHAUSTED,
    GrpcStatus.ABORTED,
    GrpcStatus.INTERNAL
  ];

  return retryableCodes.includes(error.code);
};

// Helper for exponential backoff delay
const calculateDelay = (attempt: number, config: GrpcRetryConfig): number => {
  return Math.min(
    config.initialDelayMs * Math.pow(config.backoffMultiplier, attempt - 1),
    config.maxDelayMs
  );
};

const delay = (ms: number): Promise<void> => {
  return new Promise(resolve => setTimeout(resolve, ms));
};

// Get or create circuit breaker for a service
const getCircuitBreaker = (service: string) => {
  if (!circuitBreakers[service]) {
    circuitBreakers[service] = new CircuitBreaker(
      async (call: () => Promise<any>) => call(),
      {
        timeout: false,
        errorThresholdPercentage: config.circuitBreaker.errorThresholdPercentage,
        resetTimeout: config.circuitBreaker.resetTimeoutMs,
      }
    );
    circuitBreakers[service].on('open', () => {
      metricsImpl.incrementCircuitBreakerTripped(service);
    });
  }
  return circuitBreakers[service];
};

// Retry wrapper function
export async function withGrpcRetry<T>(
  service: string,
  methodName: string,
  grpcCall: () => Promise<T>,
  isIdempotent: boolean
): Promise<T> {
  const circuitBreaker = getCircuitBreaker(service);

  // Check if circuit is open
  if (circuitBreaker.status.state === 'open') {
    throw new CircuitBreakerOpenError(service);
  }

  let attempt = 0;
  const maxAttempts = config.maxAttempts;
  
  // If max attempts is 0 or method is non-idempotent, run once without retry
  if (maxAttempts <= 0 || !isIdempotent) {
    return circuitBreaker.fire(grpcCall);
  }

  while (attempt < maxAttempts) {
    try {
      attempt++;
      const result = await circuitBreaker.fire(grpcCall);
      if (attempt > 1) {
        metricsImpl.incrementSuccessfulRetries(service, methodName);
      }
      return result;
    } catch (error: any) {
      // Check if we have attempts left and error is retryable
      const hasMoreAttempts = attempt < maxAttempts;
      const errorIsRetryable = isRetryableError(error);
      
      if (hasMoreAttempts && errorIsRetryable) {
        const retryDelay = calculateDelay(attempt, config);
        metricsImpl.incrementRetryAttempts(service, methodName);

        // Log retry attempt
        logger.info({
          service,
          methodName,
          attemptNumber: attempt,
          delayBeforeNextAttempt: retryDelay,
          errorCode: error.code
        }, 'Retrying failed gRPC call');
        
        await delay(retryDelay);
      } else {
        // Log exhausted attempts
        if (attempt > 1) {
          metricsImpl.incrementFailedRetries(service, methodName);
          logger.warn({
            service,
            methodName,
            totalAttempts: attempt,
            finalErrorCode: error.code
          }, 'All gRPC retry attempts exhausted');
        }
        throw error;
      }
    }
  }

  // Should never reach here, just in case
  throw new Error('Unexpected error in retry logic');
}
