import { withRetry, RetryConfiguration, DEFAULT_RETRY_CONFIG } from '../../utils/retry-wrapper';

// Mock timers and random
jest.useFakeTimers();

describe('Retry wrapper acceptance criteria tests', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Reset environment variables before each test
    delete process.env.EXPO_PUBLIC_API_RETRY_ENABLED;
    delete process.env.EXPO_PUBLIC_API_RETRY_MAX_RETRIES;
    delete process.env.EXPO_PUBLIC_API_RETRY_INITIAL_BACKOFF_MS;
    delete process.env.EXPO_PUBLIC_API_RETRY_MAX_BACKOFF_MS;
    delete process.env.EXPO_PUBLIC_API_RETRY_JITTER_ENABLED;
    Math.random = jest.fn(() => 0.5); // Default random value for jitter tests
  });

  // AC-1: Retry on 429 status code up to max retries with exponential backoff
  test('ac1_retry_on_429_status_code', async () => {
    const mockApiCall = jest.fn(() => Promise.reject({ response: { status: 429 } }));
    const resultPromise = withRetry(mockApiCall, { maxRetries: 3 });

    // Advance timers for each retry attempt
    for (let i = 0; i < 3; i++) {
      jest.advanceTimersByTime(1000 * Math.pow(2, i));
      await Promise.resolve();
    }

    await expect(resultPromise).rejects.toEqual({ response: { status: 429 } });
    expect(mockApiCall).toHaveBeenCalledTimes(4); // 1 initial + 3 retries
  });

  // AC-2: Retry on 5xx status codes up to max retries
  test('ac2_retry_on_5xx_status_codes', async () => {
    const test5xxStatuses = [500, 502, 503, 504];
    
    for (const status of test5xxStatuses) {
      const mockApiCall = jest.fn(() => Promise.reject({ response: { status } }));
      const resultPromise = withRetry(mockApiCall, { maxRetries: 2 });

      for (let i = 0; i < 2; i++) {
        jest.advanceTimersByTime(1000 * Math.pow(2, i));
        await Promise.resolve();
      }

      await expect(resultPromise).rejects.toEqual({ response: { status } });
      expect(mockApiCall).toHaveBeenCalledTimes(3); // 1 initial + 2 retries
    }
  });

  // AC-3: Retry on network timeout/connection errors
  test('ac3_retry_on_network_errors', async () => {
    const networkErrors = [
      new TypeError('Network request failed'),
      new Error('Connection timed out'),
      new Error('Failed to fetch')
    ];

    for (const error of networkErrors) {
      const mockApiCall = jest.fn(() => Promise.reject(error));
      const resultPromise = withRetry(mockApiCall, { maxRetries: 1 });

      jest.advanceTimersByTime(1000);
      await Promise.resolve();

      await expect(resultPromise).rejects.toEqual(error);
      expect(mockApiCall).toHaveBeenCalledTimes(2); // 1 initial + 1 retry
    }
  });

  // AC-4: Jitter adds randomness to retry delays
  test('ac4_jitter_applies_random_delay_between_0_and_calculated_backoff', async () => {
    const mockApiCall = jest.fn(() => Promise.reject({ response: { status: 500 } }));
    Math.random = jest.fn(() => 0.75); // Return 75% for jitter calculation

    const resultPromise = withRetry(mockApiCall, { maxRetries: 1, enableJitter: true, initialBackoffMs: 1000 });

    // 1000 * 2^0 * 0.75 = 750ms delay expected
    jest.advanceTimersByTime(749);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(1); // No retry yet

    jest.advanceTimersByTime(1);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(2); // Retry happened at 750ms

    await expect(resultPromise).rejects.toBeDefined();
  });

  // AC-5: No jitter uses exact exponential backoff
  test('ac5_no_jitter_uses_exact_exponential_backoff', async () => {
    const mockApiCall = jest.fn(() => Promise.reject({ response: { status: 500 } }));
    const resultPromise = withRetry(mockApiCall, { maxRetries: 2, enableJitter: false, initialBackoffMs: 1000 });

    // First retry at 1000ms
    jest.advanceTimersByTime(999);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(1);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(2);

    // Second retry at 1000 + 2000 = 3000ms
    jest.advanceTimersByTime(1999);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(2);
    jest.advanceTimersByTime(1);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(3);

    await expect(resultPromise).rejects.toBeDefined();
  });

  // AC-6: Max retries 0 means no retries
  test('ac6_max_retries_0_no_retries', async () => {
    const mockApiCall = jest.fn(() => Promise.reject({ response: { status: 500 } }));
    const resultPromise = withRetry(mockApiCall, { maxRetries: 0 });

    jest.advanceTimersByTime(10000);
    await Promise.resolve();

    await expect(resultPromise).rejects.toBeDefined();
    expect(mockApiCall).toHaveBeenCalledTimes(1); // Only initial call
  });

  // AC-7: Retry disabled means no retries regardless of other config
  test('ac7_retry_disabled_no_retries', async () => {
    const mockApiCall = jest.fn(() => Promise.reject({ response: { status: 500 } }));
    const resultPromise = withRetry(mockApiCall, { enabled: false, maxRetries: 5 });

    jest.advanceTimersByTime(10000);
    await Promise.resolve();

    await expect(resultPromise).rejects.toBeDefined();
    expect(mockApiCall).toHaveBeenCalledTimes(1); // Only initial call
  });

  // AC-8: Non-retryable 4xx errors are not retried
  test('ac8_non_retryable_4xx_errors_not_retried', async () => {
    const nonRetryableStatuses = [400, 401, 403, 404, 405, 409, 410];

    for (const status of nonRetryableStatuses) {
      const mockApiCall = jest.fn(() => Promise.reject({ response: { status } }));
      const resultPromise = withRetry(mockApiCall, { maxRetries: 3 });

      jest.advanceTimersByTime(10000);
      await Promise.resolve();

      await expect(resultPromise).rejects.toEqual({ response: { status } });
      expect(mockApiCall).toHaveBeenCalledTimes(1); // No retries
    }
  });

  // AC-10: Original error is propagated after retries exhausted
  test('ac10_original_error_propagated_after_retries_exhausted', async () => {
    const testError = new Error('Test network error with custom data');
    // @ts-ignore
    testError.customProperty = 'test value';
    const mockApiCall = jest.fn(() => Promise.reject(testError));

    const resultPromise = withRetry(mockApiCall, { maxRetries: 2 });
    for (let i = 0; i < 2; i++) {
      jest.advanceTimersByTime(1000 * Math.pow(2, i));
      await Promise.resolve();
    }

    const caughtError = await resultPromise.catch(e => e);
    expect(caughtError).toBe(testError);
    // @ts-ignore
    expect(caughtError.customProperty).toBe('test value');
  });

  // AC-11: Retry delay never exceeds maxBackoffMs
  test('ac11_retry_delay_clamped_to_max_backoff', async () => {
    const mockApiCall = jest.fn(() => Promise.reject({ response: { status: 500 } }));
    // Attempt 0: 1000ms, Attempt1: 2000ms, Attempt2:4000ms, Attempt3: 8000ms, Attempt4: 16000ms clamped to 10000ms
    const resultPromise = withRetry(mockApiCall, { 
      maxRetries: 5, 
      initialBackoffMs: 1000, 
      maxBackoffMs: 10000,
      enableJitter: false 
    });

    // First 4 retries follow 1k, 2k, 4k, 8k delays
    for (let i = 0; i < 4; i++) {
      jest.advanceTimersByTime(1000 * Math.pow(2, i));
      await Promise.resolve();
    }
    expect(mockApiCall).toHaveBeenCalledTimes(5); // Initial + 4 retries

    // Next retry should be after 10k (not 16k)
    jest.advanceTimersByTime(9999);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(5);
    jest.advanceTimersByTime(1);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(6);

    // Subsequent retries also use 10k delay
    jest.advanceTimersByTime(9999);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(6);
    jest.advanceTimersByTime(1);
    await Promise.resolve();
    expect(mockApiCall).toHaveBeenCalledTimes(7);

    await expect(resultPromise).rejects.toBeDefined();
  });
});
