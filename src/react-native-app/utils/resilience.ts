import CircuitBreaker from 'opossum';
import Toast from 'react-native-toast-message';

export enum ResilienceErrorType {
  TIMEOUT = "TIMEOUT",
  RETRY_EXHAUSTED = "RETRY_EXHAUSTED",
  CIRCUIT_OPEN = "CIRCUIT_OPEN",
}

export class ResilienceError extends Error {
  type: ResilienceErrorType;
  originalError?: Error;
  constructor(type: ResilienceErrorType, message: string, originalError?: Error) {
    super(message);
    this.type = type;
    this.originalError = originalError;
    this.name = "ResilienceError";
  }
}

export interface ResilienceConfig {
  requestTimeoutMs: number;
  retry: {
    maxRetries: number;
    initialBackoffMs: number;
    maxBackoffMs: number;
  };
  circuitBreaker: {
    failureThreshold: number;
    openCircuitDurationMs: number;
    halfOpenRequestPercent: number;
  };
}

const defaultConfig: ResilienceConfig = {
  requestTimeoutMs: parseInt(process.env.RESILIENCE_REQUEST_TIMEOUT_MS || '10000', 10),
  retry: {
    maxRetries: parseInt(process.env.RESILIENCE_RETRY_MAX_RETRIES || '3', 10),
    initialBackoffMs: parseInt(process.env.RESILIENCE_RETRY_INITIAL_BACKOFF_MS || '100', 10),
    maxBackoffMs: parseInt(process.env.RESILIENCE_RETRY_MAX_BACKOFF_MS || '2000', 10),
  },
  circuitBreaker: {
    failureThreshold: parseInt(process.env.RESILIENCE_CIRCUIT_BREAKER_FAILURE_THRESHOLD || '5', 10),
    openCircuitDurationMs: parseInt(process.env.RESILIENCE_CIRCUIT_BREAKER_OPEN_DURATION_MS || '30000', 10),
    halfOpenRequestPercent: parseInt(process.env.RESILIENCE_CIRCUIT_BREAKER_HALF_OPEN_PERCENT || '10', 10),
  },
};

let circuitBreaker: CircuitBreaker<any, any[]> | null = null;

function getCircuitBreaker(config: ResilienceConfig) {
  if (!circuitBreaker) {
    circuitBreaker = new CircuitBreaker(async (fn: () => Promise<any>) => fn(), {
      timeout: config.requestTimeoutMs,
      errorThresholdPercentage: 100,
      resetTimeout: config.circuitBreaker.openCircuitDurationMs,
      rollingCountTimeout: 10000,
      rollingCountBuckets: 10,
      capacity: Infinity,
    });

    circuitBreaker.on('open', () => {
      console.log('Circuit breaker opened');
    });

    circuitBreaker.on('halfOpen', () => {
      console.log('Circuit breaker half-open');
    });

    circuitBreaker.on('close', () => {
      console.log('Circuit breaker closed');
    });
  }
  return circuitBreaker;
}

function isTransientError(error: any): boolean {
  if (!error) return false;
  if (error.message && error.message.includes('Network Error')) return true;
  if (error.response && error.response.status >= 500 && error.response.status < 600) return true;
  
  const grpcStatus = error.code;
  if (
    grpcStatus === 14 ||
    grpcStatus === 8 ||
    grpcStatus === 4 ||
    grpcStatus === 10 ||
    grpcStatus === 13
  ) return true;
  
  if (error.message && (error.message.includes('500') || error.message.includes('Internal Server Error'))) return true;
  
  return false;
}

async function withRetry<T>(
  requestFn: () => Promise<T>,
  config: ResilienceConfig,
  isIdempotent: boolean
): Promise<T> {
  if (!isIdempotent) {
    return requestFn();
  }

  let attempt = 0;
  let lastError: any;

  while (attempt <= config.retry.maxRetries) {
    try {
      return await requestFn();
    } catch (error: any) {
      lastError = error;
      attempt++;
      
      if (attempt > config.retry.maxRetries) break;
      if (!isTransientError(error)) break;
      
      const backoffMs = Math.min(
        config.retry.initialBackoffMs * Math.pow(2, attempt - 1),
        config.retry.maxBackoffMs
      );
      
      await new Promise(resolve => setTimeout(resolve, backoffMs));
    }
  }

  throw new ResilienceError(
    ResilienceErrorType.RETRY_EXHAUSTED,
    `Maximum retries (${config.retry.maxRetries}) exceeded`,
    lastError
  );
}

export async function withResilience<T>(
  requestFn: () => Promise<T>,
  isIdempotent: boolean,
  configOverride?: Partial<ResilienceConfig>
): Promise<T> {
  const config: ResilienceConfig = {
    ...defaultConfig,
    ...configOverride,
    retry: {
      ...defaultConfig.retry,
      ...configOverride?.retry,
    },
    circuitBreaker: {
      ...defaultConfig.circuitBreaker,
      ...configOverride?.circuitBreaker,
    },
  };

  const breaker = getCircuitBreaker(config);

  try {
    const result = await breaker.fire(async () => {
      return await withRetry(requestFn, config, isIdempotent);
    });
    return result;
  } catch (error: any) {
    if (error.type === 'OpenCircuitError') {
      const resilienceError = new ResilienceError(
        ResilienceErrorType.CIRCUIT_OPEN,
        'Circuit is open, requests are blocked',
        error
      );
      Toast.show({
        type: 'error',
        text1: "We're having trouble connecting to our services. Please try again later."
      });
      throw resilienceError;
    }

    if (error.type === 'TimeoutError') {
      const resilienceError = new ResilienceError(
        ResilienceErrorType.TIMEOUT,
        `Request timed out after ${config.requestTimeoutMs}ms`,
        error
      );
      Toast.show({
        type: 'error',
        text1: "We're having trouble connecting to our services. Please try again later."
      });
      throw resilienceError;
    }

    if (error instanceof ResilienceError) {
      Toast.show({
        type: 'error',
        text1: "We're having trouble connecting to our services. Please try again later."
      });
      throw error;
    }

    throw error;
  }
}
