// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
/**
 * Copied with modification from src/frontend/utils/Request.ts
 */
import getFrontendProxyURL from "@/utils/Settings";
import { withRetry } from "@/resilience/retry-wrapper";
import { RetryConfiguration } from "@/resilience/retry-config";

interface IRequestParams {
  url: string;
  body?: object;
  method?: "GET" | "POST" | "PUT" | "DELETE";
  queryParams?: Record<string, any>;
  headers?: Record<string, string>;
  retryable?: boolean;
  retryConfig?: Partial<RetryConfiguration>;
}

const request = async <T>({
  url = "",
  method = "GET",
  body,
  queryParams = {},
  headers = {
    "content-type": "application/json",
  },
  retryable,
  retryConfig,
}: IRequestParams): Promise<T> => {
  const proxyURL = await getFrontendProxyURL();
  const requestURL = `${proxyURL}${url}?${new URLSearchParams(queryParams).toString()}`;
  const requestBody = body ? JSON.stringify(body) : undefined;

  // Determine if request is retryable
  const isIdempotent = retryable !== undefined 
    ? retryable 
    : ['GET', 'HEAD'].includes(method.toUpperCase());

  const performRequest = async () => {
    const response = await fetch(requestURL, {
      method,
      body: requestBody,
      headers,
    });

    const responseText = await response.text();

    if (!response.ok) {
      const error: any = new Error(`Request failed with status ${response.status}`);
      error.response = { status: response.status, statusText: response.statusText };
      throw error;
    }

    if (!!responseText) return JSON.parse(responseText);

    return undefined as unknown as T;
  };

  if (!isIdempotent) {
    return performRequest();
  }

  return withRetry(performRequest, retryConfig);
};

export default request;
