const { chargeWithFaultTolerance, PaymentGatewayError, CircuitBreakerOpenError, RetryAttemptsExhaustedError } = require('../charge');

// Mock payment gateway client
jest.mock('../charge', () => {
  const original = jest.requireActual('../charge');
  const mockChargeImpl = jest.fn();
  return {
    ...original,
    chargeWithFaultTolerance: jest.fn(mockChargeImpl),
    PaymentGatewayError: original.PaymentGatewayError,
    CircuitBreakerOpenError: original.CircuitBreakerOpenError,
    RetryAttemptsExhaustedError: original.RetryAttemptsExhaustedError,
    __setMockChargeImpl: (fn) => mockChargeImpl.mockImplementation(fn)
  };
});

// Mock metrics
const mockCounterIncrement = jest.fn();
const mockGaugeSet = jest.fn();
jest.mock('../metrics', () => ({
  paymentGatewayCallsTotal: {
    add: mockCounterIncrement
  },
  paymentRetryAttemptsTotal: {
    add: mockCounterIncrement
  },
  paymentCircuitBreakerState: {
    set: mockGaugeSet
  }
}));

describe('Payment Fault Tolerance Acceptance Criteria', () => {
  const testPaymentRequest = {
    amount: 100,
    currency: 'USD',
    idempotencyKey: 'test-key-123',
    cardDetails: {
      number: '4111-1111-1111-1111',
      expiry: '12/28',
      cvv: '123'
    }
  };

  beforeEach(() => {
    jest.clearAllMocks();
    // Reset environment variables to defaults before each test
    process.env.PAYMENT_RETRY_MAX_ATTEMPTS = '3';
    process.env.PAYMENT_RETRY_INITIAL_DELAY_MS = '100';
    process.env.PAYMENT_CIRCUIT_BREAKER_ERROR_THRESHOLD = '50';
    process.env.PAYMENT_CIRCUIT_BREAKER_VOLUME_THRESHOLD = '10';
    process.env.PAYMENT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS = '30000';
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  /**
   * AC-1: When a payment gateway call fails with a retryable error (5xx status code, timeout, network error), 
   * the service retries the call up to PAYMENT_RETRY_MAX_ATTEMPTS times with exponential backoff, 
   * with initial delay equal to PAYMENT_RETRY_INITIAL_DELAY_MS
   */
  test('ac1_retry_on_retryable_errors_with_exponential_backoff', async () => {
    let attemptCount = 0;
    const maxAttempts = 3;
    const initialDelay = 100;

    __setMockChargeImpl(async () => {
      attemptCount++;
      if (attemptCount <= maxAttempts) {
        throw new PaymentGatewayError('Service unavailable', 503, true);
      }
      return { success: true, transactionId: 'test-123' };
    });

    const result = await chargeWithFaultTolerance(testPaymentRequest);

    expect(attemptCount).toBe(maxAttempts + 1); // initial call + 3 retries = 4 total calls
    expect(result.success).toBe(true);
    // Verify exponential backoff delays: 100ms, 200ms, 400ms
    expect(jest.getTimerCount()).toBe(3);
    expect(setTimeout).toHaveBeenNthCalledWith(1, expect.any(Function), initialDelay);
    expect(setTimeout).toHaveBeenNthCalledWith(2, expect.any(Function), initialDelay * 2);
    expect(setTimeout).toHaveBeenNthCalledWith(3, expect.any(Function), initialDelay * 4);
  });

  /**
   * AC-2: When a payment gateway call fails with a non-retryable error (4xx status codes except 429), 
   * no retries are attempted and the error is propagated immediately
   */
  test('ac2_no_retry_on_non_retryable_errors', async () => {
    let attemptCount = 0;
    __setMockChargeImpl(async () => {
      attemptCount++;
      throw new PaymentGatewayError('Invalid card details', 400, false);
    });

    await expect(chargeWithFaultTolerance(testPaymentRequest)).rejects.toThrow(PaymentGatewayError);
    expect(attemptCount).toBe(1); // No retries
    expect(setTimeout).not.toHaveBeenCalled();
  });

  /**
   * AC-3: When the percentage of failed payment gateway calls exceeds PAYMENT_CIRCUIT_BREAKER_ERROR_THRESHOLD 
   * over a 10-second rolling window with at least PAYMENT_CIRCUIT_BREAKER_VOLUME_THRESHOLD requests, 
   * the circuit opens and all subsequent payment calls immediately throw CircuitBreakerOpenError
   */
  test('ac3_circuit_opens_when_error_threshold_exceeded', async () => {
    const volumeThreshold = 10;
    // First make 10 failed requests (100% error rate, threshold is 50%)
    __setMockChargeImpl(async () => {
      throw new PaymentGatewayError('Service down', 500, true);
    });

    for (let i = 0; i < volumeThreshold; i++) {
      try {
        await chargeWithFaultTolerance(testPaymentRequest);
      } catch (e) {}
    }

    // Next request should immediately throw CircuitBreakerOpenError
    await expect(chargeWithFaultTolerance(testPaymentRequest)).rejects.toThrow(CircuitBreakerOpenError);
    expect(mockGaugeSet).toHaveBeenCalledWith(1, { state: 'open' }); // open = 1
  });

  /**
   * AC-4: After PAYMENT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS passes with the circuit open, 
   * the circuit transitions to half-open state, allowing a single test request: 
   * if the test succeeds, circuit closes; if it fails, circuit reopens for another recovery period
   */
  test('ac4_circuit_transitions_to_half_open_after_recovery_timeout', async () => {
    const recoveryTimeout = 30000;
    // First trigger circuit open
    __setMockChargeImpl(async () => { throw new PaymentGatewayError('Down', 500, true); });
    for (let i = 0; i < 10; i++) { try { await chargeWithFaultTolerance(testPaymentRequest); } catch(e) {} }

    // Fast forward time to recovery timeout
    jest.advanceTimersByTime(recoveryTimeout);

    expect(mockGaugeSet).toHaveBeenCalledWith(2, { state: 'half_open' }); // half_open = 2

    // Test success case: half-open request succeeds, circuit closes
    __setMockChargeImpl(async () => ({ success: true }));
    await chargeWithFaultTolerance(testPaymentRequest);
    expect(mockGaugeSet).toHaveBeenCalledWith(0, { state: 'closed' }); // closed = 0

    // Reopen circuit
    __setMockChargeImpl(async () => { throw new PaymentGatewayError('Down', 500, true); });
    for (let i = 0; i < 10; i++) { try { await chargeWithFaultTolerance(testPaymentRequest); } catch(e) {} }
    jest.advanceTimersByTime(recoveryTimeout);

    // Test failure case: half-open request fails, circuit reopens
    await expect(chargeWithFaultTolerance(testPaymentRequest)).rejects.toThrow(PaymentGatewayError);
    expect(mockGaugeSet).toHaveBeenCalledWith(1, { state: 'open' });
  });

  /**
   * AC-5: All payment gateway calls, retries, and circuit breaker state transitions are logged 
   * with appropriate error details and request context
   */
  test('ac5_logging_for_errors_retries_and_circuit_state_changes', async () => {
    const mockLogError = jest.spyOn(require('../logger'), 'error').mockImplementation();
    const mockLogInfo = jest.spyOn(require('../logger'), 'info').mockImplementation();
    const mockLogWarn = jest.spyOn(require('../logger'), 'warn').mockImplementation();

    // Trigger retry
    let attempt = 0;
    __setMockChargeImpl(async () => {
      attempt++;
      if (attempt < 2) throw new PaymentGatewayError('Timeout', 504, true);
      return { success: true };
    });
    await chargeWithFaultTolerance(testPaymentRequest);

    expect(mockLogInfo).toHaveBeenCalledWith(expect.objectContaining({
      event: 'payment_retry_attempt',
      attempt: 1,
      idempotencyKey: testPaymentRequest.idempotencyKey
    }));

    // Trigger circuit open
    __setMockChargeImpl(async () => { throw new PaymentGatewayError('Down', 500, true); });
    for (let i = 0; i < 10; i++) { try { await chargeWithFaultTolerance(testPaymentRequest); } catch(e) {} }

    expect(mockLogWarn).toHaveBeenCalledWith(expect.objectContaining({
      event: 'circuit_breaker_opened',
      errorThreshold: 50,
      errorRate: 100
    }));

    mockLogError.mockRestore();
    mockLogInfo.mockRestore();
    mockLogWarn.mockRestore();
  });

  /**
   * AC-6: The payment_gateway_calls_total counter increments by 1 for each call to the payment gateway API, 
   * with correct status label (success/failed/retried)
   */
  test('ac6_payment_gateway_calls_total_metric_counts_correctly', async () => {
    // Successful call
    __setMockChargeImpl(async () => ({ success: true }));
    await chargeWithFaultTolerance(testPaymentRequest);
    expect(mockCounterIncrement).toHaveBeenCalledWith(1, { status: 'success', error_type: '' });

    // Failed call no retry
    __setMockChargeImpl(async () => { throw new PaymentGatewayError('Invalid card', 400, false); });
    try { await chargeWithFaultTolerance(testPaymentRequest); } catch(e) {}
    expect(mockCounterIncrement).toHaveBeenCalledWith(1, { status: 'failed', error_type: 'PaymentGatewayError' });

    // Retried call
    let attempt = 0;
    __setMockChargeImpl(async () => {
      attempt++;
      if (attempt < 2) throw new PaymentGatewayError('503', 503, true);
      return { success: true };
    });
    await chargeWithFaultTolerance(testPaymentRequest);
    expect(mockCounterIncrement).toHaveBeenCalledWith(1, { status: 'retried', error_type: 'PaymentGatewayError' });
  });

  /**
   * AC-7: The payment_retry_attempts_total counter increments by 1 for each retry attempt made
   */
  test('ac7_retry_attempts_metric_increments_correctly', async () => {
    const maxRetries = 3;
    let attempt = 0;
    __setMockChargeImpl(async () => {
      attempt++;
      if (attempt <= maxRetries) throw new PaymentGatewayError('Retryable', 500, true);
      return { success: true };
    });

    await chargeWithFaultTolerance(testPaymentRequest);
    expect(mockCounterIncrement).toHaveBeenCalledTimes(maxRetries); // 3 retries = 3 increments
  });

  /**
   * AC-8: The payment_circuit_breaker_state gauge updates correctly when the circuit changes state 
   * (closed=0, open=1, half_open=2)
   */
  test('ac8_circuit_breaker_state_gauge_updates_correctly', async () => {
    // Initial state should be closed
    expect(mockGaugeSet).toHaveBeenCalledWith(0, { state: 'closed' });

    // Open circuit
    __setMockChargeImpl(async () => { throw new Error('Fail'); });
    for (let i = 0; i < 10; i++) { try { await chargeWithFaultTolerance(testPaymentRequest); } catch(e) {} }
    expect(mockGaugeSet).toHaveBeenCalledWith(1, { state: 'open' });

    // Half open
    jest.advanceTimersByTime(30000);
    expect(mockGaugeSet).toHaveBeenCalledWith(2, { state: 'half_open' });

    // Close again
    __setMockChargeImpl(async () => ({ success: true }));
    await chargeWithFaultTolerance(testPaymentRequest);
    expect(mockGaugeSet).toHaveBeenCalledWith(0, { state: 'closed' });
  });

  /**
   * AC-9: All configuration values are read from environment variables, with correct default values applied if variables are not set
   */
  test('ac9_config_values_read_from_env_with_defaults', async () => {
    // Delete env vars to test defaults
    delete process.env.PAYMENT_RETRY_MAX_ATTEMPTS;
    delete process.env.PAYMENT_RETRY_INITIAL_DELAY_MS;
    delete process.env.PAYMENT_CIRCUIT_BREAKER_ERROR_THRESHOLD;
    delete process.env.PAYMENT_CIRCUIT_BREAKER_VOLUME_THRESHOLD;
    delete process.env.PAYMENT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_MS;

    const config = require('../config');
    expect(config.payment.retry.maxAttempts).toBe(3);
    expect(config.payment.retry.initialDelayMs).toBe(100);
    expect(config.payment.circuitBreaker.errorThreshold).toBe(50);
    expect(config.payment.circuitBreaker.volumeThreshold).toBe(10);
    expect(config.payment.circuitBreaker.recoveryTimeoutMs).toBe(30000);

    // Test custom env values
    process.env.PAYMENT_RETRY_MAX_ATTEMPTS = '5';
    process.env.PAYMENT_RETRY_INITIAL_DELAY_MS = '200';
    jest.resetModules();
    const customConfig = require('../config');
    expect(customConfig.payment.retry.maxAttempts).toBe(5);
    expect(customConfig.payment.retry.initialDelayMs).toBe(200);
  });

  /**
   * AC-10: When all retry attempts are exhausted, RetryAttemptsExhaustedError is thrown 
   * with the total number of attempts and the last error encountered
   */
  test('ac10_throws_retry_exhausted_error_when_all_attempts_fail', async () => {
    const maxAttempts = 3;
    const testError = new PaymentGatewayError('Permanent failure', 500, true);
    __setMockChargeImpl(async () => {
      throw testError;
    });

    await expect(chargeWithFaultTolerance(testPaymentRequest)).rejects.toThrow(RetryAttemptsExhaustedError);
    try {
      await chargeWithFaultTolerance(testPaymentRequest);
    } catch (e) {
      expect(e.attemptCount).toBe(maxAttempts + 1); // initial + 3 retries = 4 total attempts
      expect(e.lastError).toBe(testError);
    }
  });
});
