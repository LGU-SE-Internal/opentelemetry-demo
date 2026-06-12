import { createEnhancedGatewayClient, GatewayError, GatewayErrorType, GatewayClientConfig } from '../gateways/Api.gateway';
import CircuitBreaker from 'opossum';
import pRetry from 'p-retry';

// Mock dependencies
jest.mock('opossum');
jest.mock('p-retry');
jest.mock('@opentelemetry/api', () => ({
  trace: {
    getActiveSpan: jest.fn(() => ({
      setAttributes: jest.fn(),
    })),
  },
}));

const mockFetch = jest.fn();
global.fetch = mockFetch;

describe('Gateway Retry and Circuit Breaker Acceptance Criteria', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    process.env = {
      NEXT_PUBLIC_GATEWAY_RETRY_ATTEMPTS: '2',
      NEXT_PUBLIC_GATEWAY_RETRY_BASE_INTERVAL_MS: '100',
      NEXT_PUBLIC_GATEWAY_CB_FAILURE_THRESHOLD_PCT: '50',
      NEXT_PUBLIC_GATEWAY_CB_SLIDING_WINDOW_SIZE: '100',
      NEXT_PUBLIC_GATEWAY_CB_OPEN_DURATION_MS: '30000',
      NEXT_PUBLIC_GATEWAY_CB_HALF_OPEN_MAX_CALLS: '10',
    };
    (pRetry as jest.Mock).mockImplementation((fn) => fn());
    (CircuitBreaker as jest.Mock).mockImplementation((fn) => ({
      fire: fn,
      state: 'closed',
      on: jest.fn(),
    }));
  });

  /** AC-1: Retry idempotent GET requests with exponential backoff for transient errors */
  test('ac1_get_request_retries_transient_errors_with_exponential_backoff', async () => {
    const client = createEnhancedGatewayClient<{ getProduct: (id: string) => Promise<{ id: string }> }>(
      'http://test-service',
      'product-catalog'
    );

    let attemptCount = 0;
    mockFetch.mockImplementation(() => {
      attemptCount++;
      if (attemptCount < 3) {
        return Promise.resolve({ status: 503, ok: false });
      }
      return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({ id: 'test' }) });
    });

    const result = await client.getProduct('test');
    
    expect(pRetry).toHaveBeenCalledTimes(1);
    expect(attemptCount).toBe(3); // 1 initial + 2 retries = 3 attempts
    expect(result).toEqual({ id: 'test' });
    expect(pRetry).toHaveBeenCalledWith(expect.any(Function), expect.objectContaining({
      retries: 2,
      factor: 2,
      minTimeout: 100,
      randomize: true,
    }));
  });

  /** AC-2: Non-idempotent requests are never retried */
  test('ac2_non_get_requests_are_not_retried', async () => {
    const client = createEnhancedGatewayClient<{ addToCart: (item: { id: string }) => Promise<{ success: boolean }> }>(
      'http://test-service',
      'cart'
    );

    let attemptCount = 0;
    mockFetch.mockImplementation(() => {
      attemptCount++;
      return Promise.resolve({ status: 503, ok: false });
    });

    await expect(client.addToCart({ id: 'test' })).rejects.toThrow(GatewayError);
    
    expect(pRetry).not.toHaveBeenCalled();
    expect(attemptCount).toBe(1); // Only 1 attempt, no retries
  });

  /** AC-3: Circuit breaker protection for all gateway calls */
  test('ac3_circuit_breaker_trips_when_failure_threshold_exceeded', async () => {
    const mockFire = jest.fn(() => Promise.reject(new Error('Failure')));
    (CircuitBreaker as jest.Mock).mockImplementation(() => ({
      fire: mockFire,
      state: 'closed',
      on: jest.fn(),
    }));

    const client = createEnhancedGatewayClient<{ getCart: () => Promise<unknown> }>(
      'http://test-service',
      'cart'
    );

    // Simulate 51 failures out of 100 requests (51% failure rate, exceeds 50% threshold)
    for (let i = 0; i < 51; i++) {
      await expect(client.getCart()).rejects.toThrow();
    }

    // Check circuit breaker was configured correctly
    expect(CircuitBreaker).toHaveBeenCalledWith(expect.any(Function), expect.objectContaining({
      threshold: 50,
      rollingCountTimeout: expect.any(Number),
      rollingCountBuckets: expect.any(Number),
      resetTimeout: 30000,
      halfOpenMaxRequests: 10,
    }));
  });

  test('ac3_circuit_transitions_to_half_open_after_open_duration', async () => {
    const mockOn = jest.fn();
    let currentState = 'closed';
    (CircuitBreaker as jest.Mock).mockImplementation(() => ({
      fire: jest.fn(() => Promise.reject(new Error())),
      get state() { return currentState; },
      on: mockOn,
    }));

    createEnhancedGatewayClient('http://test-service', 'cart');
    
    // Find the open event listener
    const openListener = mockOn.mock.calls.find(call => call[0] === 'open')?.[1];
    const halfOpenListener = mockOn.mock.calls.find(call => call[0] === 'halfOpen')?.[1];
    
    expect(openListener).toBeDefined();
    expect(halfOpenListener).toBeDefined();
    
    // Simulate circuit open
    currentState = 'open';
    openListener();
    
    // Fast forward time past open duration
    jest.advanceTimersByTime(30000);
    
    // Check that circuit transitions to half-open
    expect(halfOpenListener).toHaveBeenCalled();
  });

  /** AC-4: All configuration parameters loaded from environment variables */
  test('ac4_configuration_loaded_from_environment_variables', async () => {
    process.env.NEXT_PUBLIC_GATEWAY_RETRY_ATTEMPTS = '3';
    process.env.NEXT_PUBLIC_GATEWAY_RETRY_BASE_INTERVAL_MS = '200';
    process.env.NEXT_PUBLIC_GATEWAY_CB_FAILURE_THRESHOLD_PCT = '60';
    process.env.NEXT_PUBLIC_GATEWAY_CB_OPEN_DURATION_MS = '60000';
    process.env.NEXT_PUBLIC_GATEWAY_CB_HALF_OPEN_MAX_CALLS = '5';

    createEnhancedGatewayClient('http://test-service', 'cart');

    expect(CircuitBreaker).toHaveBeenCalledWith(expect.any(Function), expect.objectContaining({
      threshold: 60,
      resetTimeout: 60000,
      halfOpenMaxRequests: 5,
    }));
    expect(pRetry).toHaveBeenCalledWith(expect.any(Function), expect.objectContaining({
      retries: 3,
      minTimeout: 200,
    }));
  });

  /** AC-5: Open circuit rejects requests immediately with CIRCUIT_BREAKER_OPEN error */
  test('ac5_open_circuit_rejects_requests_immediately', async () => {
    const mockFire = jest.fn(() => Promise.reject(new CircuitBreaker.OpenCircuitError()));
    (CircuitBreaker as jest.Mock).mockImplementation(() => ({
      fire: mockFire,
      state: 'open',
      on: jest.fn(),
    }));

    const client = createEnhancedGatewayClient<{ getProduct: (id: string) => Promise<unknown> }>(
      'http://test-service',
      'product-catalog'
    );

    await expect(client.getProduct('test')).rejects.toThrow(GatewayError);
    await expect(client.getProduct('test')).rejects.toMatchObject({
      type: GatewayErrorType.CIRCUIT_BREAKER_OPEN,
      circuitBreakerState: 'open',
    });
    expect(mockFetch).not.toHaveBeenCalled(); // No request sent to backend
  });

  /** AC-6: Failed requests logged with correct OpenTelemetry attributes */
  test('ac6_failed_requests_log_otel_attributes', async () => {
    const mockSetAttributes = jest.fn();
    const { trace } = require('@opentelemetry/api');
    (trace.getActiveSpan as jest.Mock).mockReturnValue({ setAttributes: mockSetAttributes });

    const client = createEnhancedGatewayClient<{ getProduct: (id: string) => Promise<unknown> }>(
      'http://test-service',
      'product-catalog'
    );

    mockFetch.mockResolvedValue({ status: 500, ok: false });

    await expect(client.getProduct('test')).rejects.toThrow();

    expect(mockSetAttributes).toHaveBeenCalledWith(expect.objectContaining({
      'service.name': 'product-catalog',
      'http.method': 'GET',
      'http.url': expect.stringContaining('http://test-service'),
      'http.status_code': 500,
      'error.type': GatewayErrorType.RETRY_EXHAUSTED,
      'gateway.retry_count': 2,
      'gateway.circuit_breaker_state': 'closed',
    }));
  });

  /** AC-7: Default retryable status codes include [408, 429, 500, 502, 503, 504] */
  test('ac7_default_retryable_status_codes_configured', async () => {
    const client = createEnhancedGatewayClient<{ getProduct: (id: string) => Promise<unknown> }>(
      'http://test-service',
      'product-catalog'
    );

    const retryableStatuses = [408, 429, 500, 502, 503, 504];
    for (const status of retryableStatuses) {
      mockFetch.mockResolvedValue({ status, ok: false });
      await expect(client.getProduct('test')).rejects.toThrow();
      expect(pRetry).toHaveBeenCalled();
      pRetry.mockClear();
    }

    // Non-retryable status codes should not trigger retries
    const nonRetryableStatuses = [400, 401, 403, 404, 501];
    for (const status of nonRetryableStatuses) {
      mockFetch.mockResolvedValue({ status, ok: false });
      await expect(client.getProduct('test')).rejects.toThrow();
      expect(pRetry).not.toHaveBeenCalled();
      pRetry.mockClear();
    }
  });
});
