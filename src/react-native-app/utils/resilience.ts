import CircuitBreaker from 'opossum';
import Toast from 'react-native-toast-message';
import { trace } from '@opentelemetry/api';

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
    initialDelayMs: number;
    backoffFactor: number;
    maxDelayMs: number;
    retryableMethods: string[];
    retryableStatusCodes: number[];
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
    initialDelayMs: parseInt(process.env.RESILIENCE_RETRY_INITIAL_DELAY_MS || '100', 10),
    backoffFactor: parseInt(process.env.RESILIENCE_RETRY_BACKOFF_FACTOR || '2', 10),
    maxDelayMs: parseInt(process.env.RESILIENCE_RETRY_MAX_DELAY_MS || '5000', 10),
    retryableMethods: ['GET', 'HEAD'],
    retryableStatusCodes: [429, 500, 502, 503, 504],
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

function isTransientError(error: any, retryableStatusCodes: number[]): boolean {
  if (!error) return false;
  if (error.message && (error.message.includes('Network Error') || error.message.includes('timeout') || error.message.includes('Failed to fetch') || error.message.includes('connectivity'))) return true;
  if (error.response && retryableStatusCodes.includes(error.response.status)) return true;
  
  const grpcStatus = error.code;
  if (
    grpcStatus === 14 ||
    grpcStatus === 8 ||
    grpcStatus === 4 ||
    grpcStatus === 10 ||
    grpcStatus === 13
  ) return true;
  
  return false;
}

async function withRetry<T>(
  requestFn: () => Promise<T>,
  config: ResilienceConfig,
  isIdempotent: boolean,
  requestMeta?: { url: string; method: string }
): Promise<T> {
  if (!isIdempotent) {
    return requestFn();
  }

  let attempt = 0;
  let lastError: any;
  const activeSpan = trace.getActiveSpan();

  while (attempt <= config.retry.maxRetries) {
    try {
      return await requestFn();
    } catch (error: any) {
      lastError = error;
      attempt++;
      
      if (attempt > config.retry.maxRetries) break;
      if (!isTransientError(error, config.retry.retryableStatusCodes)) break;
      
      const baseDelay = config.retry.initialDelayMs * Math.pow(config.retry.backoffFactor, attempt - 1);
      const jitteredDelay = baseDelay * (0.8 + Math.random() * 0.4);
      const backoffMs = Math.min(jitteredDelay, config.retry.maxDelayMs);
      
      // Log retry event
      console.log(JSON.stringify({
        timestamp: new Date().toISOString(),
        "request.url": requestMeta?.url,
        "request.method": requestMeta?.method,
        "retry.attempt": attempt,
        "retry.error_details": error.message || String(error),
        "retry.delay_before_next_retry_ms": backoffMs
      }));
      
      // Add OTel span event
      if (activeSpan) {
        activeSpan.addEvent('request.retry', {
          "retry.attempt_number": attempt,
          "retry.error_type": error.response ? `${error.response.status}_server_error` : error.message?.includes('timeout') ? 'network_timeout' : 'connectivity_drop',
          "retry.error_message": error.message || String(error),
          "retry.next_delay_ms": backoffMs
        });
      }
      
      await new Promise(resolve => setTimeout(resolve, backoffMs));
    }
  }

  throw lastError;
}

export async function withResilience<T>(
  requestFn: () => Promise<T>,
  isIdempotent: boolean,
  configOverride?: Partial<ResilienceConfig>,
  requestMeta?: { url: string; method: string }
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
      return await withRetry(requestFn, config, isIdempotent, requestMeta);
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
