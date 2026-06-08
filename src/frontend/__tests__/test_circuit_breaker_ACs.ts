import { getCircuitBreaker, CircuitState, CircuitOpenError } from '../utils/circuitBreaker';

describe('Circuit Breaker Acceptance Criteria', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test('ac1_transitions_to_open_when_failure_threshold_exceeded_in_window', async () => {
    const consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation();
    const serviceName = 'test-service-ac1';
    const cb = getCircuitBreaker({
      serviceName,
      failureThreshold: 2,
      failureThresholdWindowMs: 10000,
      timeoutMs: 1000,
      recoveryDelayMs: 30000
    });

    const failingRequest = jest.fn(() => Promise.reject(new Error('Request failed')));

    // First failure
    await expect(cb.execute(failingRequest)).rejects.toThrow('Request failed');
    expect(cb.getState()).toBe(CircuitState.CLOSED);

    // Second failure
    await expect(cb.execute(failingRequest)).rejects.toThrow('Request failed');

    // Wait for circuit to open
    jest.runAllTimers();
    await new Promise(process.nextTick);

    expect(cb.getState()).toBe(CircuitState.OPEN);
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"event":"circuit_state_change"'));
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining(`"service":"${serviceName}"`));
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"old_state":"CLOSED"'));
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"new_state":"OPEN"'));
  });

  test('ac2_open_state_rejects_all_requests_with_circuit_open_error', async () => {
    const consoleInfoSpy = jest.spyOn(console, 'info').mockImplementation();
    const serviceName = 'test-service-ac2';
    const cb = getCircuitBreaker({
      serviceName,
      failureThreshold: 1,
      failureThresholdWindowMs: 10000,
      timeoutMs: 1000,
      recoveryDelayMs: 30000
    });

    const failingRequest = jest.fn(() => Promise.reject(new Error('Request failed')));

    // Trigger circuit open
    await expect(cb.execute(failingRequest)).rejects.toThrow('Request failed');
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.OPEN);

    // Next request should be rejected immediately
    const testRequest = jest.fn(() => Promise.resolve('success'));
    await expect(cb.execute(testRequest)).rejects.toThrow(CircuitOpenError);
    expect(testRequest).not.toHaveBeenCalled();
    expect(consoleInfoSpy).toHaveBeenCalledWith(expect.stringContaining('"event":"circuit_request_rejected"'));
    expect(consoleInfoSpy).toHaveBeenCalledWith(expect.stringContaining(`"service":"${serviceName}"`));
  });

  test('ac3_transitions_to_half_open_after_recovery_delay', async () => {
    const consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation();
    const recoveryDelayMs = 5000;
    const serviceName = 'test-service-ac3';
    const cb = getCircuitBreaker({
      serviceName,
      failureThreshold: 1,
      failureThresholdWindowMs: 10000,
      timeoutMs: 1000,
      recoveryDelayMs
    });

    const failingRequest = jest.fn(() => Promise.reject(new Error('Request failed')));

    // Trigger circuit open
    await expect(cb.execute(failingRequest)).rejects.toThrow('Request failed');
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.OPEN);

    // Fast forward recovery delay
    jest.advanceTimersByTime(recoveryDelayMs);
    await new Promise(process.nextTick);

    expect(cb.getState()).toBe(CircuitState.HALF_OPEN);
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"old_state":"OPEN"'));
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"new_state":"HALF_OPEN"'));
  });

  test('ac4_half_open_allows_one_request_then_transitions_on_result', async () => {
    const consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation();
    const serviceName = 'test-service-ac4';
    const cb = getCircuitBreaker({
      serviceName,
      failureThreshold: 1,
      failureThresholdWindowMs: 10000,
      timeoutMs: 1000,
      recoveryDelayMs: 5000
    });

    // First open circuit
    await expect(cb.execute(() => Promise.reject(new Error()))).rejects.toThrow();
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.OPEN);

    // Move to half open
    jest.advanceTimersByTime(5000);
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.HALF_OPEN);

    // Test success case
    const successRequest = jest.fn(() => Promise.resolve('success'));
    expect(await cb.execute(successRequest)).toBe('success');
    expect(successRequest).toHaveBeenCalledTimes(1);
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.CLOSED);
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"old_state":"HALF_OPEN"'));
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"new_state":"CLOSED"'));

    // Open circuit again for failure test
    await expect(cb.execute(() => Promise.reject(new Error()))).rejects.toThrow();
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.OPEN);

    // Move to half open again
    jest.advanceTimersByTime(5000);
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.HALF_OPEN);

    // Test failure case
    const failureRequest = jest.fn(() => Promise.reject(new Error('Failed again')));
    await expect(cb.execute(failureRequest)).rejects.toThrow('Failed again');
    expect(failureRequest).toHaveBeenCalledTimes(1);
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.OPEN);
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"old_state":"HALF_OPEN"'));
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('"new_state":"OPEN"'));
  });

  test('ac5_timeouts_count_as_failures_towards_threshold', async () => {
    const serviceName = 'test-service-ac5';
    const cb = getCircuitBreaker({
      serviceName,
      failureThreshold: 1,
      failureThresholdWindowMs: 10000,
      timeoutMs: 1000,
      recoveryDelayMs: 30000
    });

    const slowRequest = jest.fn(() => new Promise((resolve) => {
      setTimeout(() => resolve('success'), 2000);
    }));

    // Execute slow request
    const requestPromise = cb.execute(slowRequest);
    jest.advanceTimersByTime(1000);
    await expect(requestPromise).rejects.toThrow('Timed out after 1000ms');

    // Circuit should be open now
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(cb.getState()).toBe(CircuitState.OPEN);
  });

  test('ac6_circuits_are_independent_per_service', async () => {
    const cb1 = getCircuitBreaker({ serviceName: 'service-1', failureThreshold: 1 });
    const cb2 = getCircuitBreaker({ serviceName: 'service-2', failureThreshold: 1 });

    // Fail service 1
    await expect(cb1.execute(() => Promise.reject(new Error()))).rejects.toThrow();
    jest.runAllTimers();
    await new Promise(process.nextTick);

    expect(cb1.getState()).toBe(CircuitState.OPEN);
    expect(cb2.getState()).toBe(CircuitState.CLOSED);

    // Service 2 should still work
    const successRequest = jest.fn(() => Promise.resolve('success'));
    expect(await cb2.execute(successRequest)).toBe('success');
  });

  test('ac8_all_circuit_logs_include_service_name', async () => {
    const consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation();
    const consoleInfoSpy = jest.spyOn(console, 'info').mockImplementation();
    const serviceName = 'test-log-service';

    const cb = getCircuitBreaker({ serviceName, failureThreshold: 1, recoveryDelayMs: 1000 });

    // Trigger open
    await expect(cb.execute(() => Promise.reject(new Error()))).rejects.toThrow();
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining(`"service":"${serviceName}"`));

    // Trigger reject
    await expect(cb.execute(() => Promise.resolve())).rejects.toThrow(CircuitOpenError);
    expect(consoleInfoSpy).toHaveBeenCalledWith(expect.stringContaining(`"service":"${serviceName}"`));

    // Trigger half open
    jest.advanceTimersByTime(1000);
    await new Promise(process.nextTick);
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining(`"service":"${serviceName}"`));

    // Trigger close
    await cb.execute(() => Promise.resolve('success'));
    jest.runAllTimers();
    await new Promise(process.nextTick);
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining(`"service":"${serviceName}"`));
  });
});
