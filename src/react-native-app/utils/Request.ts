// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
/**
 * Copied with modification from src/frontend/utils/Request.ts
 */
import getFrontendProxyURL from "@/utils/Settings";
import { withResilience } from "@/utils/resilience";

interface IRequestParams {
  url: string;
  body?: object;
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  queryParams?: Record<string, any>;
  headers?: Record<string, string>;
  /**
   * If true, enables retries for non-idempotent requests (POST/DELETE/PATCH)
   * Only set this if the target endpoint is known to be safe for repeated execution
   * Default: false for all non-GET requests, true for GET requests
   */
  allowRetry?: boolean;
  /**
   * Optional custom base delay for exponential backoff in milliseconds
   * Default: 100
   */
  retryBaseDelay?: number;
  /**
   * Optional custom maximum number of retry attempts
   * Default: 3
   */
  maxRetries?: number;
}

const request = async <T>({
  url = "",
  method = "GET",
  body,
  queryParams = {},
  headers = {
    "content-type": "application/json",
  },
  allowRetry,
  retryBaseDelay,
  maxRetries,
}: IRequestParams): Promise<T> => {
  const proxyURL = await getFrontendProxyURL();
  const requestURL = `${proxyURL}${url}?${new URLSearchParams(queryParams).toString()}`;
  const requestBody = body ? JSON.stringify(body) : undefined;

  // Determine if request is retryable
  const isIdempotent = allowRetry !== undefined 
    ? allowRetry 
    : ['GET', 'HEAD'].includes(method.toUpperCase());

  const configOverride: any = {};
  if (retryBaseDelay !== undefined) {
    configOverride.retry = {
      initialDelayMs: retryBaseDelay
    };
  }
  if (maxRetries !== undefined) {
    configOverride.retry = {
      ...configOverride.retry,
      maxRetries: maxRetries
    };
  }

  const performRequest = async () => {
    const response = await fetch(requestURL, {
      method,
      body: requestBody,
      headers,
    });

    const responseText = await response.text();

    if (!response.ok) {
      const error: any = new Error(`Request failed with status ${response.status}`);
      error.response = { status: response.status, statusText: response.statusText, headers: response.headers };
      throw error;
    }

    if (!!responseText) return JSON.parse(responseText);

    return undefined as unknown as T;
  };

  return withResilience(performRequest, isIdempotent, configOverride, { url: requestURL, method });
};

export default request;
