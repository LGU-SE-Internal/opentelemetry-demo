export interface RetryConfiguration {
  enabled: boolean;
  maxRetries: number;
  initialBackoffMs: number;
  maxBackoffMs: number;
  retryableStatusCodes: number[];
  enableJitter: boolean;
}

export const DEFAULT_RETRY_CONFIG: RetryConfiguration = {
  enabled: true,
  maxRetries: 3,
  initialBackoffMs: 1000,
  maxBackoffMs: 10000,
  retryableStatusCodes: [429, 500, 502, 503, 504],
  enableJitter: true,
};

const parseBooleanEnv = (value: string | undefined, defaultValue: boolean): boolean => {
  if (value === undefined) return defaultValue;
  return value.toLowerCase() === 'true';
};

const parseNumberEnv = (value: string | undefined, defaultValue: number): number => {
  if (value === undefined) return defaultValue;
  const parsed = parseInt(value, 10);
  return isNaN(parsed) ? defaultValue : parsed;
};

export const getRetryConfigFromEnv = (): Partial<RetryConfiguration> => {
  return {
    enabled: parseBooleanEnv(process.env.EXPO_PUBLIC_API_RETRY_ENABLED, DEFAULT_RETRY_CONFIG.enabled),
    maxRetries: parseNumberEnv(process.env.EXPO_PUBLIC_API_RETRY_MAX_RETRIES, DEFAULT_RETRY_CONFIG.maxRetries),
    initialBackoffMs: parseNumberEnv(process.env.EXPO_PUBLIC_API_RETRY_INITIAL_BACKOFF_MS, DEFAULT_RETRY_CONFIG.initialBackoffMs),
    maxBackoffMs: parseNumberEnv(process.env.EXPO_PUBLIC_API_RETRY_MAX_BACKOFF_MS, DEFAULT_RETRY_CONFIG.maxBackoffMs),
    enableJitter: parseBooleanEnv(process.env.EXPO_PUBLIC_API_RETRY_JITTER_ENABLED, DEFAULT_RETRY_CONFIG.enableJitter),
  };
};
