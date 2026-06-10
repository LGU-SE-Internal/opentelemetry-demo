const { evaluateFlagWithCircuitBreaker, FlagdCircuitBreakerConfig } = require('../feature_flags');
const opossum = require('opossum');
const { metrics } = require('@opentelemetry/api');

describe('Flagd Circuit Breaker Acceptance Criteria Tests', () => {
  const testConfig = {
    failureThreshold: 3,
    coolDownPeriodMs: 1000,
    halfOpenSuccessThreshold: 2,
    defaultFlagValues: {
      'payment-failure-simulation': false,
      'test-flag': 'default-value',
      'numeric-flag': 100
    }
  };

  const mockFlagdSuccess = jest.fn().mockResolvedValue(true);
  const mockFlagdFailure = jest.fn().mockRejectedValue(new Error('flagd connection error'));

  beforeEach(() => {
    jest.clearAllMocks();
    // Reset circuit breaker state between tests
    if (evaluateFlagWithCircuitBreaker.circuit) {
      evaluateFlagWithCircuitBreaker.circuit.close();
    }
    // Reset metric counters
    const meter = metrics.getMeter('payment-service');
    ['payment.flagd.circuit_breaker.state', 'payment.flagd.circuit_breaker.failure_count', 'payment.flagd.circuit_breaker.circuit_open_count', 'payment.flagd.circuit_breaker.fallback_used_count'].forEach(metricName => {
      const metric = meter.getMetric(metricName);
      if (metric) {
        metric.reset();
      }
    });
  });

  // AC-1: When failure threshold reached, circuit transitions to open, state gauge updates
  test('ac1_circuit_opens_after_failure_threshold', async () => {
    // Configure circuit to use failing flagd
    evaluateFlagWithCircuitBreaker.mockImplementation(() => mockFlagdFailure());

    // Make failureThreshold + 1 calls
    for (let i = 0; i < testConfig.failureThreshold; i++) {
      try {
        await evaluateFlagWithCircuitBreaker('payment-failure-simulation', false);
      } catch (e) { /* expected */ }
    }

    // Check circuit is now open
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.OPEN);
    // Check state gauge for open is 1
    const stateGauge = metrics.getMeter('payment-service').getGauge('payment.flagd.circuit_breaker.state');
    const openPoints = stateGauge.collect().find(p => p.attributes.state === 'open').points;
    expect(openPoints[0].value).toBe(1);
    // Check closed and half_open are 0
    const closedPoints = stateGauge.collect().find(p => p.attributes.state === 'closed').points;
    expect(closedPoints[0].value).toBe(0);
    const halfOpenPoints = stateGauge.collect().find(p => p.attributes.state === 'half_open').points;
    expect(halfOpenPoints[0].value).toBe(0);
  });

  // AC-2: Open circuit returns defaults immediately, no flagd calls, fallback counter increments
  test('ac2_open_circuit_returns_defaults_no_flagd_calls', async () => {
    // Force circuit open
    evaluateFlagWithCircuitBreaker.circuit.open();
    mockFlagdSuccess.mockClear();

    const result = await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
    // Should return configured default not per-call fallback
    expect(result).toBe(testConfig.defaultFlagValues['test-flag']);
    // No flagd calls made
    expect(mockFlagdSuccess).not.toHaveBeenCalled();
    // Check fallback counter incremented
    const fallbackCounter = metrics.getMeter('payment-service').getCounter('payment.flagd.circuit_breaker.fallback_used_count');
    const points = fallbackCounter.collect().points;
    expect(points[0].value).toBe(1);
  });

  // AC-3: After cool down period, circuit transitions to half-open state
  test('ac3_circuit_moves_to_half_open_after_cooldown', async () => {
    jest.useFakeTimers();
    // Force circuit open
    evaluateFlagWithCircuitBreaker.circuit.open();
    
    // Advance time by just under cool down period
    jest.advanceTimersByTime(testConfig.coolDownPeriodMs - 100);
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.OPEN);
    
    // Advance time past cool down period
    jest.advanceTimersByTime(200);
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.HALF_OPEN);
    
    // Check state gauge for half_open is 1
    const stateGauge = metrics.getMeter('payment-service').getGauge('payment.flagd.circuit_breaker.state');
    const halfOpenPoints = stateGauge.collect().find(p => p.attributes.state === 'half_open').points;
    expect(halfOpenPoints[0].value).toBe(1);
    
    jest.useRealTimers();
  });

  // AC-4: Half open state behavior: success threshold met closes circuit, failure reopens
  test('ac4_half_open_success_threshold_closes_circuit', async () => {
    // Force circuit to half open
    evaluateFlagWithCircuitBreaker.circuit.halfOpen();
    evaluateFlagWithCircuitBreaker.mockImplementation(() => mockFlagdSuccess());

    // Make halfOpenSuccessThreshold successful calls
    for (let i = 0; i < testConfig.halfOpenSuccessThreshold; i++) {
      await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
    }

    // Circuit should now be closed
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.CLOSED);
  });

  test('ac4_half_open_failure_reopens_circuit', async () => {
    // Force circuit to half open
    evaluateFlagWithCircuitBreaker.circuit.halfOpen();
    evaluateFlagWithCircuitBreaker.mockImplementation(() => mockFlagdFailure());

    try {
      await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
    } catch (e) { /* expected */ }

    // Circuit should be open again
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.OPEN);
  });

  // AC-5: Failed flagd calls increment failure count counter
  test('ac5_failed_flagd_calls_increment_failure_counter', async () => {
    // Circuit is closed
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.CLOSED);
    evaluateFlagWithCircuitBreaker.mockImplementation(() => mockFlagdFailure());

    // Make 2 failed calls
    for (let i = 0; i < 2; i++) {
      try {
        await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
      } catch (e) { /* expected */ }
    }

    // Check failure count is 2
    const failureCounter = metrics.getMeter('payment-service').getCounter('payment.flagd.circuit_breaker.failure_count');
    const points = failureCounter.collect().points;
    expect(points[0].value).toBe(2);
  });

  // AC-6: Circuit open transitions increment circuit open count counter
  test('ac6_circuit_open_transition_increments_counter', async () => {
    evaluateFlagWithCircuitBreaker.mockImplementation(() => mockFlagdFailure());

    // First transition to open
    for (let i = 0; i < testConfig.failureThreshold; i++) {
      try {
        await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
      } catch (e) { /* expected */ }
    }
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.OPEN);

    // Close circuit and trigger again
    evaluateFlagWithCircuitBreaker.circuit.close();
    for (let i = 0; i < testConfig.failureThreshold; i++) {
      try {
        await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
      } catch (e) { /* expected */ }
    }

    // Check counter is 2
    const openCounter = metrics.getMeter('payment-service').getCounter('payment.flagd.circuit_breaker.circuit_open_count');
    const points = openCounter.collect().points;
    expect(points[0].value).toBe(2);
  });

  // AC-7: Closed circuit behaves as normal, no changes to existing functionality
  test('ac7_closed_circuit_behavior_unchanged', async () => {
    // Circuit is closed
    expect(evaluateFlagWithCircuitBreaker.circuit.state).toBe(opossum.State.CLOSED);
    const expectedValue = 'test-success-value';
    mockFlagdSuccess.mockResolvedValue(expectedValue);
    evaluateFlagWithCircuitBreaker.mockImplementation(() => mockFlagdSuccess());

    const result = await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
    expect(result).toBe(expectedValue);
    expect(mockFlagdSuccess).toHaveBeenCalledTimes(1);
  });

  // AC-8: No payment failures when flagd is down, returns valid defaults
  test('ac8_flagd_downtime_no_payment_failures_returns_defaults', async () => {
    // Simulate flagd completely down, circuit open
    evaluateFlagWithCircuitBreaker.circuit.open();

    // Test all flag types return valid defaults
    const boolResult = await evaluateFlagWithCircuitBreaker('payment-failure-simulation', true);
    expect(boolResult).toBe(false);
    expect(typeof boolResult).toBe('boolean');

    const stringResult = await evaluateFlagWithCircuitBreaker('test-flag', 'fallback');
    expect(stringResult).toBe('default-value');
    expect(typeof stringResult).toBe('string');

    const numericResult = await evaluateFlagWithCircuitBreaker('numeric-flag', 999);
    expect(numericResult).toBe(100);
    expect(typeof numericResult).toBe('number');

    // No errors thrown
    await expect(evaluateFlagWithCircuitBreaker('nonexistent-flag', 'default')).resolves.not.toThrow();
  });
});
