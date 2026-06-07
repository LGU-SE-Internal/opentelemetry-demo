// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
/**
 * Copied with modification from src/frontend/utils/Request.ts
 */
import getFrontendProxyURL from "./Settings";

// Base error class for all API request failures
export class ApiError extends Error {
  override name: string = 'ApiError';
  cause?: unknown;
  constructor(message: string, cause?: unknown) {
    super(message);
    this.cause = cause;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

// Error for non-2xx HTTP responses
export class HttpError extends ApiError {
  override name = 'HttpError' as const;
  status: number;
  responseBody: unknown;
  constructor(status: number, responseBody: unknown, message?: string, cause?: unknown) {
    super(message ?? `Request failed with status code ${status}`, cause);
    this.status = status;
    this.responseBody = responseBody;
  }
}

// Error for network connectivity failures (no internet, DNS failure, connection refused)
export class NetworkError extends ApiError {
  override name = 'NetworkError' as const;
  constructor(message = 'Network connection failed', cause?: unknown) {
    super(message, cause);
  }
}

// Error for request timeout
export class TimeoutError extends ApiError {
  override name = 'TimeoutError' as const;
  timeoutMs: number;
  constructor(timeoutMs: number, message?: string, cause?: unknown) {
    super(message ?? `Request timed out after ${timeoutMs}ms`, cause);
    this.timeoutMs = timeoutMs;
  }
}

// Error for JSON parsing failures of 2xx responses
export class ParseError extends ApiError {
  override name = 'ParseError' as const;
  rawResponse: string;
  constructor(rawResponse: string, message = 'Failed to parse response JSON', cause?: unknown) {
    super(message, cause);
    this.rawResponse = rawResponse;
  }
}

// Retry configuration options
export interface RetryConfig {
  maxRetries: number; // Default: 3
  backoffFactor: number; // Default: 2 (exponential: 1s, 2s, 4s)
  retryableStatuses: number[]; // Default: [429, 500, 502, 503, 504]
  retryNetworkErrors: boolean; // Default: true
}

// Request options (extends standard fetch RequestInit)
export interface ApiRequestOptions extends RequestInit {
  timeout?: number; // Default: 10000 (10 seconds)
  retry?: Partial<RetryConfig>;
}

const DEFAULT_RETRY_CONFIG: RetryConfig = {
  maxRetries: 3,
  backoffFactor: 2,
  retryableStatuses: [429, 500, 502, 503, 504],
  retryNetworkErrors: true,
};

const DEFAULT_TIMEOUT = 10000;

async function wait(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export async function apiRequest<T>(
  url: string,
  options?: ApiRequestOptions
): Promise<T> {
  const retryConfig: RetryConfig = {
    ...DEFAULT_RETRY_CONFIG,
    ...options?.retry,
  };
  const timeout = options?.timeout ?? DEFAULT_TIMEOUT;

  let attempt = 0;
  let lastError: unknown;

  while (attempt <= retryConfig.maxRetries) {
    attempt++;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
      const proxyURL = await getFrontendProxyURL();
      const requestURL = `${proxyURL}${url}`;
      
      const response = await fetch(requestURL, {
        ...options,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      const responseText = await response.text();

      if (!response.ok) {
        // Non-2xx status code
        if (!retryConfig.retryableStatuses.includes(response.status) || attempt > retryConfig.maxRetries) {
          // Not retryable or no more retries left
          let responseBody: unknown;
          try {
            responseBody = JSON.parse(responseText);
          } catch {
            responseBody = responseText;
          }
          throw new HttpError(response.status, responseBody);
        }
        
        // Retryable status, wait before next attempt
        const delay = Math.min(1000 * Math.pow(retryConfig.backoffFactor, attempt - 1), 4000);
        await wait(delay);
        continue;
      }

      // 2xx response, try to parse JSON
      try {
        return responseText ? JSON.parse(responseText) : (undefined as unknown as T);
      } catch (parseErr) {
        throw new ParseError(responseText, undefined, parseErr);
      }

    } catch (err) {
      clearTimeout(timeoutId);
      lastError = err;

      // Check if error is timeout
      if (err instanceof Error && err.name === 'AbortError') {
        throw new TimeoutError(timeout, undefined, err);
      }

      // Check if error is network error
      const isNetworkError = err instanceof Error && (
        err.name === 'TypeError' || // fetch throws TypeError for network failures
        err.name === 'NetworkError'
      );

      if (isNetworkError) {
        if (!retryConfig.retryNetworkErrors || attempt > retryConfig.maxRetries) {
          throw new NetworkError(undefined, err);
        }
        
        // Retry network error
        const delay = Math.min(1000 * Math.pow(retryConfig.backoffFactor, attempt - 1), 4000);
        await wait(delay);
        continue;
      }

      // If it's one of our custom errors, rethrow immediately
      if (err instanceof ApiError) {
        throw err;
      }

      // Unhandled error type, wrap as ApiError
      throw new ApiError('Unexpected request failure', err);
    }
  }

  // If we exit the loop, all retries failed
  if (lastError instanceof HttpError) {
    throw lastError;
  }
  if (lastError instanceof NetworkError) {
    throw lastError;
  }
  throw new ApiError('All retry attempts failed', lastError);
}

// Legacy request function for backwards compatibility
interface IRequestParams {
  url: string;
  body?: object;
  method?: "GET" | "POST" | "PUT" | "DELETE";
  queryParams?: Record<string, any>;
  headers?: Record<string, string>;
}

const request = async <T>({
  url = "",
  method = "GET",
  body,
  queryParams = {},
  headers = {
    "content-type": "application/json",
  },
}: IRequestParams): Promise<T> => {
  const queryString = new URLSearchParams(queryParams).toString();
  const fullUrl = queryString ? `${url}?${queryString}` : url;
  
  return apiRequest(fullUrl, {
    method,
    body: body ? JSON.stringify(body) : undefined,
    headers,
  });
};

export default request;
