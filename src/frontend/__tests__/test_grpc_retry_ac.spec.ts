import { jest, describe, test, expect, beforeEach, beforeAll } from '@jest/globals';
import { status as GrpcStatus } from '@grpc/grpc-js';
import { withGrpcRetry, GrpcRetryConfig, READ_ONLY_GRPC_METHODS, RetryMetrics, CircuitBreakerOpenError } from '../utils/grpcRetry';

// Mock dependencies
jest.mock('@opentelemetry/api', () => ({
  metrics: {
    getMeter: () => ({
      createCounter: () => ({ add: jest.fn() })
    })
  }
}));

jest.mock('opossum', () => {
  const mockCircuitBreaker = jest.fn().mockImplementation((fn, options) => {
    const cb = {
      fire: jest.fn().mockImplementation((...args) => fn(...args)),
      on: jest.fn(),
      status: { stats: { failures: 0, successes: 0 } }
    };
    return cb;
  });
  return { default: mockCircuitBreaker };
});

describe('gRPC Retry Acceptance Criteria Tests', () => {
  const testConfig: GrpcRetryConfig = {
    maxAttempts: 3,
    initialDelayMs: 100,
    maxDelayMs: 2000,
    circuitBreaker: {
      errorThresholdPercentage: 50,
      resetTimeoutMs: 10000
    }
  };

  const mockMetrics: RetryMetrics = {
    incrementRetryAttempts: jest.fn(),
    incrementSuccessfulRetries: jest.fn(),
    incrementFailedRetries: jest.fn(),
    incrementCircuitBreakerTripped: jest.fn()
  };

  // Override config for tests
  beforeAll(() => {
    process.env.FRONTEND_GRPC_RETRY_MAX_ATTEMPTS = '3';
    process.env.FRONTEND_GRPC_RETRY_INITIAL_DELAY_MS = '100';
    process.env.FRONTEND_GRPC_RETRY_MAX_DELAY_MS = '2000';
    process.env.FRONTEND_GRPC_CIRCUIT_BREAKER_ERROR_THRESHOLD = '50';
    process.env.FRONTEND_GRPC_CIRCUIT_BREAKER_RESET_TIMEOUT_MS = '10000';
    jest.useFakeTimers();
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('AC1_retry_readonly_transient_error_with_exponential_backoff', async () => {
    let callCount = 0;
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    const mockGrpcCall = jest.fn(async () => {
      callCount++;
      if (callCount < 3) {
        throw transientError;
      }
      return 'success';
    });

    const result = await withGrpcRetry('product-catalog', 'GetProduct', mockGrpcCall, true);
    
    expect(result).toBe('success');
    expect(mockGrpcCall).toHaveBeenCalledTimes(3);
    // Verify retry attempts metric
    expect(mockMetrics.incrementRetryAttempts).toHaveBeenCalledTimes(2);
    // Verify successful retry metric
    expect(mockMetrics.incrementSuccessfulRetries).toHaveBeenCalledWith('product-catalog', 'GetProduct');
  });

  test('AC2_no_retry_for_non_readonly_calls', async () => {
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    const mockGrpcCall = jest.fn(async () => {
      throw transientError;
    });

    await expect(withGrpcRetry('payment', 'SubmitPayment', mockGrpcCall, false)).rejects.toThrow(transientError);
    
    expect(mockGrpcCall).toHaveBeenCalledTimes(1);
    expect(mockMetrics.incrementRetryAttempts).not.toHaveBeenCalled();
  });

  test('AC3_use_configured_retry_values_from_env', async () => {
    // Override env vars for this test
    process.env.FRONTEND_GRPC_RETRY_MAX_ATTEMPTS = '5';
    process.env.FRONTEND_GRPC_RETRY_INITIAL_DELAY_MS = '200';
    process.env.FRONTEND_GRPC_RETRY_MAX_DELAY_MS = '5000';

    let callCount = 0;
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    const mockGrpcCall = jest.fn(async () => {
      callCount++;
      throw transientError;
    });

    await expect(withGrpcRetry('cart', 'GetCart', mockGrpcCall, true)).rejects.toThrow(transientError);
    
    expect(mockGrpcCall).toHaveBeenCalledTimes(5); // Should use 5 from env, not default 3
  });

  test('AC4_retry_attempts_metric_incremented_per_attempt', async () => {
    let callCount = 0;
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    const mockGrpcCall = jest.fn(async () => {
      callCount++;
      if (callCount < 4) {
        throw transientError;
      }
      return 'success';
    });

    await withGrpcRetry('shipping', 'GetShippingQuote', mockGrpcCall, true);
    
    expect(mockMetrics.incrementRetryAttempts).toHaveBeenCalledTimes(3);
    expect(mockMetrics.incrementRetryAttempts).toHaveBeenNthCalledWith(1, 'shipping', 'GetShippingQuote');
    expect(mockMetrics.incrementRetryAttempts).toHaveBeenNthCalledWith(2, 'shipping', 'GetShippingQuote');
    expect(mockMetrics.incrementRetryAttempts).toHaveBeenNthCalledWith(3, 'shipping', 'GetShippingQuote');
  });

  test('AC5_successful_retries_metric_incremented_on_nth_try_success', async () => {
    let callCount = 0;
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    const mockGrpcCall = jest.fn(async () => {
      callCount++;
      if (callCount === 1) {
        throw transientError;
      }
      return 'success';
    });

    await withGrpcRetry('recommendation', 'ListRecommendations', mockGrpcCall, true);
    
    expect(mockMetrics.incrementSuccessfulRetries).toHaveBeenCalledTimes(1);
    expect(mockMetrics.incrementSuccessfulRetries).toHaveBeenCalledWith('recommendation', 'ListRecommendations');
  });

  test('AC6_failed_retries_metric_incremented_when_all_attempts_fail', async () => {
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    const mockGrpcCall = jest.fn(async () => {
      throw transientError;
    });

    await expect(withGrpcRetry('user', 'GetUserProfile', mockGrpcCall, true)).rejects.toThrow(transientError);
    
    expect(mockMetrics.incrementFailedRetries).toHaveBeenCalledTimes(1);
    expect(mockMetrics.incrementFailedRetries).toHaveBeenCalledWith('user', 'GetUserProfile');
  });

  test('AC7_circuit_breaker_opens_when_error_threshold_exceeded', async () => {
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    // Simulate enough failed calls to trip circuit breaker
    for (let i = 0; i < 10; i++) {
      const mockGrpcCall = jest.fn(async () => { throw transientError; });
      try {
        await withGrpcRetry('product-catalog', 'ListProducts', mockGrpcCall, true);
      } catch (e) {}
    }

    expect(mockMetrics.incrementCircuitBreakerTripped).toHaveBeenCalledTimes(1);
    expect(mockMetrics.incrementCircuitBreakerTripped).toHaveBeenCalledWith('product-catalog');
  });

  test('AC8_circuit_open_returns_error_immediately', async () => {
    const transientError: any = new Error('UNAVAILABLE');
    transientError.code = GrpcStatus.UNAVAILABLE;

    // First trip the circuit
    for (let i = 0; i < 10; i++) {
      const mockGrpcCall = jest.fn(async () => { throw transientError; });
      try {
        await withGrpcRetry('cart', 'GetCart', mockGrpcCall, true);
      } catch (e) {}
    }

    // Now test that calls immediately return circuit open error
    const mockGrpcCall = jest.fn(async () => { return 'success'; });
    await expect(withGrpcRetry('cart', 'GetCart', mockGrpcCall, true)).rejects.toThrow(CircuitBreakerOpenError);
    
    // The gRPC call should NOT have been made
    expect(mockGrpcCall).not.toHaveBeenCalled();
  });
});
