import { RetryConfiguration, DEFAULT_RETRY_CONFIG, getRetryConfigFromEnv } from './retry-config';

const isNetworkError = (error: any): boolean => {
  return (
    error.name === 'TypeError' &&
    (error.message.includes('Network request failed') ||
      error.message.includes('Failed to fetch') ||
      error.message.includes('Network Error') ||
      error.message.includes('timeout'))
  );
};

const isRetryableStatusCode = (status: number, retryableStatusCodes: number[]): boolean => {
  return retryableStatusCodes.includes(status);
};

const calculateDelay = (attempt: number, config: RetryConfiguration): number => {
  const exponentialBackoff = config.initialBackoffMs * Math.pow(2, attempt);
  const clampedBackoff = Math.min(exponentialBackoff, config.maxBackoffMs);
  
  if (config.enableJitter) {
    return Math.floor(Math.random() * clampedBackoff);
  }
  
  return clampedBackoff;
};

const wait = (ms: number): Promise<void> => {
  return new Promise(resolve => setTimeout(resolve, ms));
};

export async function withRetry<T>(
  apiCall: () => Promise<T>,
  config?: Partial<RetryConfiguration>
): Promise<T> {
  const envConfig = getRetryConfigFromEnv();
  const finalConfig: RetryConfiguration = {
    ...DEFAULT_RETRY_CONFIG,
    ...envConfig,
    ...config,
  };

  if (!finalConfig.enabled || finalConfig.maxRetries === 0) {
    return apiCall();
  }

  let attempt = 0;
  let lastError: any;

  while (attempt <= finalConfig.maxRetries) {
    try {
      return await apiCall();
    } catch (error: any) {
      lastError = error;
      
      const isRetryable = 
        isNetworkError(error) || 
        (error.response && isRetryableStatusCode(error.response.status, finalConfig.retryableStatusCodes));

      if (!isRetryable || attempt === finalConfig.maxRetries) {
        throw lastError;
      }

      const delay = calculateDelay(attempt, finalConfig);
      await wait(delay);
      
      attempt++;
    }
  }

  throw lastError;
}
