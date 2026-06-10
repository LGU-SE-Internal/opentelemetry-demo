import { apiFetch, RetryLimitExceededError } from '../../gateways/Api.gateway';

// Mock global fetch before each test
global.fetch = jest.fn();
jest.useFakeTimers();

describe('apiFetch retry logic', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.clearAllTimers();
  });

  // AC-1: GET request network timeout retries 3 times with exponential backoff + jitter before throwing RetryLimitExceededError
  test('ac1_get_request_timeout_retries_3_times_with_exponential_backoff_and_jitter', async () => {
    // Mock fetch to throw network timeout error
    (fetch as jest.Mock).mockRejectedValue(new Error('Network timeout'));
    
    const requestPromise = apiFetch('/test-endpoint', { method: 'GET' });
    
    // Fast-forward through all retry intervals
    for (let i = 0; i < 3; i++) {
      jest.advanceTimersByTime(1000 * Math.pow(2, i) * 1.25); // Account for max jitter
      await Promise.resolve(); // Allow pending promises to resolve
    }
    
    await expect(requestPromise).rejects.toThrow(RetryLimitExceededError);
    expect(fetch).toHaveBeenCalledTimes(4); // 1 initial + 3 retries = 4 total attempts
  });

  // AC-2: GET request 5xx response retries 3 times before propagating 5xx
  test('ac2_get_request_5xx_response_retries_3_times_then_returns_5xx', async () => {
    // Mock fetch to return 503 Service Unavailable
    (fetch as jest.Mock).mockResolvedValue({ status: 503, ok: false } as Response);
    
    const response = await apiFetch('/test-endpoint', { method: 'GET' });
    
    expect(fetch).toHaveBeenCalledTimes(4); // 1 initial + 3 retries = 4 total attempts
    expect(response.status).toBe(503);
  });

  // AC-3: POST/PUT/DELETE requests have no retries by default
  test.each(['POST', 'PUT', 'DELETE'])('ac3_%s_request_has_no_retries_by_default', async (method) => {
    (fetch as jest.Mock).mockRejectedValue(new Error('Network error'));
    
    const requestPromise = apiFetch('/test-endpoint', { method });
    
    await expect(requestPromise).rejects.toThrow('Network error');
    expect(fetch).toHaveBeenCalledTimes(1); // No retries, only initial attempt
  });

  // AC-4: maxRetries: 0 disables all retries regardless of request type
  test('ac4_max_retries_0_disables_all_retries', async () => {
    (fetch as jest.Mock).mockRejectedValue(new Error('Network timeout'));
    
    const requestPromise = apiFetch('/test-endpoint', {
      method: 'GET',
      retry: { maxRetries: 0 }
    });
    
    await expect(requestPromise).rejects.toThrow('Network timeout');
    expect(fetch).toHaveBeenCalledTimes(1); // Only initial attempt, no retries
  });

  // AC-5: retryMutating: true enables retries for mutating requests
  test('ac5_retry_mutating_true_enables_retries_for_post_request', async () => {
    (fetch as jest.Mock).mockResolvedValue({ status: 500, ok: false } as Response);
    
    const response = await apiFetch('/test-endpoint', {
      method: 'POST',
      body: JSON.stringify({ test: 'data' }),
      retry: { retryMutating: true }
    });
    
    expect(fetch).toHaveBeenCalledTimes(4); // 1 initial + 3 retries = 4 total attempts
    expect(response.status).toBe(500);
  });

  // AC-6: Jitter ensures concurrent requests have different retry intervals
  test('ac6_jitter_creates_different_retry_intervals_for_concurrent_requests', async () => {
    (fetch as jest.Mock).mockRejectedValue(new Error('Network timeout'));
    
    const retryDelays: number[] = [];
    // Override Date.now to track delay between attempts
    const originalNow = Date.now;
    let lastAttemptTime = Date.now();
    
    (Date.now as jest.Mock) = jest.fn(() => {
      const currentTime = originalNow() + jest.now();
      if (fetch.mock.calls.length > 1) {
        retryDelays.push(currentTime - lastAttemptTime);
      }
      lastAttemptTime = currentTime;
      return currentTime;
    });

    // Make 5 concurrent failing GET requests
    const requests = Array.from({ length: 5 }, () => 
      apiFetch('/test-endpoint', { method: 'GET' })
    );

    // Fast-forward through all possible retry timings
    jest.advanceTimersByTime(10000);
    await Promise.allSettled(requests);

    // Restore original Date.now
    Date.now = originalNow;

    // Calculate standard deviation of delays, should be > 10% of base delay (100ms for 1000ms base)
    const mean = retryDelays.reduce((a, b) => a + b, 0) / retryDelays.length;
    const variance = retryDelays.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / retryDelays.length;
    const stdDev = Math.sqrt(variance);
    
    expect(stdDev).toBeGreaterThan(100); // > 10% of 1000ms base delay
  });

  // AC-7: Custom shouldRetry function overrides default eligibility
  test('ac7_custom_shouldRetry_function_overrides_default_rules', async () => {
    // Custom shouldRetry that retries 404 errors for GET requests
    const customShouldRetry = jest.fn((_error, response) => response?.status === 404);
    
    (fetch as jest.Mock).mockResolvedValue({ status: 404, ok: false } as Response);
    
    const response = await apiFetch('/test-endpoint', {
      method: 'GET',
      retry: {
        shouldRetry: customShouldRetry,
        maxRetries: 2
      }
    });
    
    expect(customShouldRetry).toHaveBeenCalledTimes(2); // Called after each failed attempt before retry
    expect(fetch).toHaveBeenCalledTimes(3); // 1 initial + 2 retries = 3 total attempts
    expect(response.status).toBe(404);
  });
});
