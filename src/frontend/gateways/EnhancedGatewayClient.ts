// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import CircuitBreaker from 'opossum';
import pRetry, { AbortError } from 'p-retry';
import { trace } from '@opentelemetry/api';

// Retry configuration
export interface RetryConfig {
  /** Maximum number of retry attempts for transient errors */
  attempts: number;
  /** Base interval in milliseconds for exponential backoff */
  baseIntervalMs: number;
  /** HTTP status codes that are considered retryable */
  retryableStatusCodes: number[];
}

// Circuit Breaker configuration
export interface CircuitBreakerConfig {
  /** Percentage of failed requests required to trip the circuit breaker */
  failureThresholdPercent: number;
  /** Number of requests in the sliding window to calculate failure rate */
  slidingWindowSize: number;
  /** Time in milliseconds the circuit remains open before testing recovery */
  openDurationMs: number;
  /** Maximum number of test calls allowed when circuit is half-open */
  halfOpenMaxCalls: number;
}

// Combined gateway client configuration
export interface GatewayClientConfig {
  retry: RetryConfig;
  circuitBreaker: CircuitBreakerConfig;
  /** Enable OpenTelemetry attribute logging for failed requests */
  enableOtelLogging: boolean;
}

// Gateway Error Type
export enum GatewayErrorType {
  /** Request failed after all retry attempts */
  RETRY_EXHAUSTED = 'RETRY_EXHAUSTED',
  /** Circuit is open, request rejected immediately */
  CIRCUIT_BREAKER_OPEN = 'CIRCUIT_BREAKER_OPEN',
  /** Permanent non-retryable error */
  PERMANENT_ERROR = 'PERMANENT_ERROR'
}

// Custom Gateway Error class
export class GatewayError extends Error {
  type: GatewayErrorType;
  /** HTTP status code from the backend response, if available */
  httpStatusCode?: number;
  /** Number of retries attempted before failure */
  retryCount: number;
  /** State of the circuit breaker at time of request */
  circuitBreakerState: 'closed' | 'open' | 'half-open';
  /** Target backend service name */
  serviceName: string;
  /** HTTP method of the failed request */
  httpMethod: string;

  constructor(
    message: string,
    type: GatewayErrorType,
    serviceName: string,
    httpMethod: string,
    retryCount: number,
    circuitBreakerState: 'closed' | 'open' | 'half-open',
    httpStatusCode?: number
  ) {
    super(message);
    this.name = 'GatewayError';
    this.type = type;
    this.serviceName = serviceName;
    this.httpMethod = httpMethod;
    this.retryCount = retryCount;
    this.circuitBreakerState = circuitBreakerState;
    this.httpStatusCode = httpStatusCode;
  }
}

// Default configuration
const DEFAULT_RETRYABLE_STATUSES = [408, 429, 500, 502, 503, 504];

export const defaultConfig: GatewayClientConfig = {
  retry: {
    attempts: parseInt(process.env.NEXT_PUBLIC_GATEWAY_RETRY_ATTEMPTS || '2', 10),
    baseIntervalMs: parseInt(process.env.NEXT_PUBLIC_GATEWAY_RETRY_BASE_INTERVAL_MS || '100', 10),
    retryableStatusCodes: DEFAULT_RETRYABLE_STATUSES,
  },
  circuitBreaker: {
    failureThresholdPercent: parseInt(process.env.NEXT_PUBLIC_GATEWAY_CB_FAILURE_THRESHOLD_PCT || '50', 10),
    slidingWindowSize: parseInt(process.env.NEXT_PUBLIC_GATEWAY_CB_SLIDING_WINDOW_SIZE || '100', 10),
    openDurationMs: parseInt(process.env.NEXT_PUBLIC_GATEWAY_CB_OPEN_DURATION_MS || '30000', 10),
    halfOpenMaxCalls: parseInt(process.env.NEXT_PUBLIC_GATEWAY_CB_HALF_OPEN_MAX_CALLS || '10', 10),
  },
  enableOtelLogging: true,
};

// Circuit breaker instances per service
const circuitBreakers = new Map<string, CircuitBreaker>();

function logErrorToOtel(error: GatewayError, url: string) {
  if (!defaultConfig.enableOtelLogging) return;

  const activeSpan = trace.getActiveSpan();
  if (!activeSpan) return;

  activeSpan.setAttribute('service.name', error.serviceName);
  activeSpan.setAttribute('http.method', error.httpMethod);
  activeSpan.setAttribute('http.url', url);
  if (error.httpStatusCode) {
    activeSpan.setAttribute('http.status_code', error.httpStatusCode);
  }
  activeSpan.setAttribute('error.type', error.type);
  activeSpan.setAttribute('gateway.retry_count', error.retryCount);
  activeSpan.setAttribute('gateway.circuit_breaker_state', error.circuitBreakerState);
  activeSpan.recordException(error);
  activeSpan.setStatus({ code: 2, message: error.message });
}

interface RequestOptions {
  url: string;
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  body?: any;
  headers?: Record<string, string>;
  queryParams?: Record<string, any>;
}

/**
 * Factory function to create an enhanced gateway client with retry and circuit breaker
 * @param baseUrl Base URL of the backend service
 * @param serviceName Name of the backend service for telemetry
 * @param customConfig Optional override of default configuration
 * @returns Wrapped gateway client with identical interface to existing clients
 */
export function createEnhancedGatewayClient<T>(
  baseUrl: string,
  serviceName: string,
  customConfig?: Partial<GatewayClientConfig>
): { request: <R>(opts: RequestOptions) => Promise<R> } & T {
  const config = { ...defaultConfig, ...customConfig };
  if (config.retry) config.retry = { ...defaultConfig.retry, ...customConfig?.retry };
  if (config.circuitBreaker) config.circuitBreaker = { ...defaultConfig.circuitBreaker, ...customConfig?.circuitBreaker };

  // Create circuit breaker for this service if it doesn't exist
  if (!circuitBreakers.has(serviceName)) {
    const circuitBreaker = new CircuitBreaker(
      async (requestFn: () => Promise<any>) => requestFn(),
      {
        timeout: false,
        errorThresholdPercentage: config.circuitBreaker.failureThresholdPercent,
        rollingCountTimeout: config.circuitBreaker.openDurationMs,
        rollingCountBuckets: 10, // Fixed bucket count for sliding window
        resetTimeout: config.circuitBreaker.openDurationMs,
        halfOpenMaxRequests: config.circuitBreaker.halfOpenMaxCalls,
        volumeThreshold: Math.min(5, config.circuitBreaker.slidingWindowSize),
      }
    );

    circuitBreaker.fallback(() => {
      throw new GatewayError(
        'Circuit breaker is open',
        GatewayErrorType.CIRCUIT_BREAKER_OPEN,
        serviceName,
        'UNKNOWN',
        0,
        'open'
      );
    });

    circuitBreakers.set(serviceName, circuitBreaker);
  }

  const circuitBreaker = circuitBreakers.get(serviceName)!;

  const request = async <R>({
    url,
    method = 'GET',
    body,
    headers = { 'content-type': 'application/json' },
    queryParams = {},
  }: RequestOptions): Promise<R> => {
    const queryString = new URLSearchParams(queryParams).toString();
    const fullUrl = `${baseUrl}${url}${queryString ? `?${queryString}` : ''}`;
    let retryCount = 0;

    const executeRequest = async () => {
      const response = await fetch(fullUrl, {
        method,
        body: body ? JSON.stringify(body) : undefined,
        headers,
      });

      if (response.ok) {
        const responseText = await response.text();
        return !!responseText ? JSON.parse(responseText) : undefined as unknown as R;
      }

      // Check if error is retryable
      const isIdempotentGet = method === 'GET';
      const isRetryableStatus = config.retry.retryableStatusCodes.includes(response.status);

      if (isIdempotentGet && isRetryableStatus) {
        retryCount++;
        throw new Error(`Transient error: ${response.status} ${response.statusText}`);
      }

      // Permanent error
      const error = new GatewayError(
        `Permanent error: ${response.status} ${response.statusText}`,
        GatewayErrorType.PERMANENT_ERROR,
        serviceName,
        method,
        retryCount,
        circuitBreaker.status.state as any,
        response.status
      );
      logErrorToOtel(error, fullUrl);
      throw new AbortError(error);
    };

    try {
      const result = await circuitBreaker.fire(async () => {
        if (method !== 'GET') {
          return executeRequest();
        }

        return pRetry(executeRequest, {
          retries: config.retry.attempts,
          factor: 2,
          minTimeout: config.retry.baseIntervalMs,
          randomize: true,
          onFailedAttempt: (error) => {
            retryCount = error.attemptNumber;
          },
        });
      });

      return result as R;
    } catch (error: any) {
      if (error instanceof GatewayError) {
        logErrorToOtel(error, fullUrl);
        throw error;
      }

      // Handle retry exhausted error
      if (error.name === 'RetryError') {
        const gatewayError = new GatewayError(
          `Retry exhausted after ${config.retry.attempts} attempts`,
          GatewayErrorType.RETRY_EXHAUSTED,
          serviceName,
          method,
          config.retry.attempts,
          circuitBreaker.status.state as any,
          error.cause?.response?.status
        );
        logErrorToOtel(gatewayError, fullUrl);
        throw gatewayError;
      }

      // Handle other errors
      const gatewayError = new GatewayError(
        error.message || 'Unknown error',
        GatewayErrorType.PERMANENT_ERROR,
        serviceName,
        method,
        retryCount,
        circuitBreaker.status.state as any
      );
      logErrorToOtel(gatewayError, fullUrl);
      throw gatewayError;
    }
  };

  return { request } as { request: <R>(opts: RequestOptions) => Promise<R> } & T;
}
