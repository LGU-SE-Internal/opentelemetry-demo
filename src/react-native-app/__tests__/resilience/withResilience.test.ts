import { describe, it, expect, jest, beforeEach } from '@jest/globals';
import { withResilience, ResilienceError, ResilienceErrorType, ResilienceConfig } from '../../utils/resilience';
import Toast from 'react-native-toast-message';

// Mock toast message library
jest.mock('react-native-toast-message', () => ({
  show: jest.fn(),
}));

describe('Resilience Layer Acceptance Criteria Tests', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Reset any module state between tests
    jest.resetModules();
  });

  // AC-1: Timeout behavior
  it('test_ac1_timeout_aborts_request_after_configured_time', async () => {
    // Mock request that takes 11s to complete
    const slowRequest = jest.fn(() => new Promise(resolve => setTimeout(() => resolve('success'), 11000)));
    
    await expect(withResilience(slowRequest, true)).rejects.toThrow(ResilienceError);
    await expect(withResilience(slowRequest, true)).rejects.toHaveProperty('type', ResilienceErrorType.TIMEOUT);
    
    // Verify request was aborted (should be called once, but not completed)
    expect(slowRequest).toHaveBeenCalledTimes(1);
  });

  // AC-2: Retry for idempotent requests
  it('test_ac2_retry_idempotent_request_succeeds_after_retry', async () => {
    // Mock request that fails 3 times then succeeds
    let attempt = 0;
    const flakyRequest = jest.fn(() => {
      attempt++;
      if (attempt <= 3) return Promise.reject(new Error('500 Internal Server Error'));
      return Promise.resolve('success');
    });

    const result = await withResilience(flakyRequest, true);
    expect(result).toBe('success');
    expect(flakyRequest).toHaveBeenCalledTimes(4); // 1 initial + 3 retries
  });

  it('test_ac2_retry_exhausted_after_max_retries', async () => {
    // Mock request that fails 4 times
    const alwaysFailingRequest = jest.fn(() => Promise.reject(new Error('500 Internal Server Error')));

    await expect(withResilience(alwaysFailingRequest, true)).rejects.toThrow(ResilienceError);
    await expect(withResilience(alwaysFailingRequest, true)).rejects.toHaveProperty('type', ResilienceErrorType.RETRY_EXHAUSTED);
    
    expect(alwaysFailingRequest).toHaveBeenCalledTimes(4); // 1 initial + 3 retries
  });

  // AC-3: No retry for non-idempotent requests
  it('test_ac3_no_retry_for_non_idempotent_requests', async () => {
    const failingRequest = jest.fn(() => Promise.reject(new Error('Request failed')));

    await expect(withResilience(failingRequest, false)).rejects.toThrow(Error);
    await expect(withResilience(failingRequest, false)).rejects.not.toBeInstanceOf(ResilienceError);
    
    expect(failingRequest).toHaveBeenCalledTimes(1); // No retries
  });

  // AC-4: Circuit breaker open state
  it('test_ac4_circuit_opens_after_failure_threshold', async () => {
    const failingRequest = jest.fn(() => Promise.reject(new Error('500 Internal Server Error')));

    // First 5 requests should fail normally (circuit closed)
    for (let i = 0; i < 5; i++) {
      await expect(withResilience(failingRequest, true)).rejects.toThrow();
    }

    // 6th request should immediately throw CIRCUIT_OPEN
    await expect(withResilience(failingRequest, true)).rejects.toThrow(ResilienceError);
    await expect(withResilience(failingRequest, true)).rejects.toHaveProperty('type', ResilienceErrorType.CIRCUIT_OPEN);
    
    // Verify 6th request didn't actually execute the request function
    expect(failingRequest).toHaveBeenCalledTimes(5);
  });

  // AC-5: Circuit breaker half-open state
  it('test_ac5_half_open_allows_percentage_of_requests', async () => {
    jest.useFakeTimers();
    const failingRequest = jest.fn(() => Promise.reject(new Error('500 Internal Server Error')));

    // Open circuit by failing 5 times
    for (let i = 0; i < 5; i++) {
      await expect(withResilience(failingRequest, true)).rejects.toThrow();
    }

    // Advance time past open circuit duration (30s)
    jest.advanceTimersByTime(30001);

    // Send 10 requests
    let circuitOpenCount = 0;
    let executedCount = 0;
    for (let i = 0; i < 10; i++) {
      try {
        await withResilience(failingRequest, true);
      } catch (err: any) {
        if (err.type === ResilienceErrorType.CIRCUIT_OPEN) {
          circuitOpenCount++;
        } else {
          executedCount++;
        }
      }
    }

    // Only ~10% (1 request) should be executed, others circuit open
    expect(executedCount).toBe(1);
    expect(circuitOpenCount).toBe(9);
    expect(failingRequest).toHaveBeenCalledTimes(5 + 1); // 5 initial failures + 1 half-open test request

    jest.useRealTimers();
  });

  it('test_ac5_half_open_success_closes_circuit', async () => {
    jest.useFakeTimers();
    let attempt = 0;
    const request = jest.fn(() => {
      attempt++;
      if (attempt <=5) return Promise.reject(new Error('500 error'));
      return Promise.resolve('success');
    });

    // Open circuit
    for (let i = 0; i < 5; i++) {
      await expect(withResilience(request, true)).rejects.toThrow();
    }

    // Advance time
    jest.advanceTimersByTime(30001);

    // First half-open request succeeds
    const result = await withResilience(request, true);
    expect(result).toBe('success');

    // Next requests should all execute (circuit closed)
    for (let i = 0; i < 5; i++) {
      await withResilience(request, true);
    }

    expect(request).toHaveBeenCalledTimes(5 + 1 +5);
    jest.useRealTimers();
  });

  // AC-6: Configurable via environment variables
  it('test_ac6_env_variables_override_default_config', async () => {
    // Set environment variables before importing the module
    process.env.RESILIENCE_REQUEST_TIMEOUT_MS = '5000';
    process.env.RESILIENCE_RETRY_MAX_RETRIES = '2';
    
    // Import fresh module to read env vars
    const { withResilience: withResilienceEnv } = require('../../utils/resilience');

    const slowRequest = jest.fn(() => new Promise(resolve => setTimeout(() => resolve('success'), 6000)));
    await expect(withResilienceEnv(slowRequest, true)).rejects.toHaveProperty('type', ResilienceErrorType.TIMEOUT);

    const failingRequest = jest.fn(() => Promise.reject(new Error('500 error')));
    await expect(withResilienceEnv(failingRequest, true)).rejects.toThrow();
    expect(failingRequest).toHaveBeenCalledTimes(3); // 1 initial + 2 retries
  });

  // AC-7: User-facing error handling
  it('test_ac7_toast_shown_on_resilience_errors', async () => {
    const failingRequest = jest.fn(() => Promise.reject(new Error('500 error')));

    await expect(withResilience(failingRequest, true)).rejects.toThrow(ResilienceError);

    expect(Toast.show).toHaveBeenCalledWith(
      expect.objectContaining({
        text1: "We're having trouble connecting to our services. Please try again later."
      })
    );
  });
});
