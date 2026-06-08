const { expect } = require('chai');
const sinon = require('sinon');
const { OpenFeature } = require('@openfeature/server-sdk');
const promClient = require('prom-client');
const { getFlagValue } = require('../feature_flags'); // This will exist once implementation is done

describe('Feature Flag Circuit Breaker Acceptance Criteria', () => {
  let sandbox;
  let flagEvalStub;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    promClient.register.clear();
    
    // Stub OpenFeature client evaluation
    const client = OpenFeature.getClient('paymentservice');
    flagEvalStub = sandbox.stub(client, 'getBooleanValue');
  });

  afterEach(() => {
    sandbox.restore();
  });

  /**
   * AC-1: Given the circuit is closed, when 5 consecutive flagd API calls fail
   * then the circuit transitions to open state, all subsequent flag calls return
   * the provided defaultValue immediately for 30 seconds with no network calls to flagd.
   */
  it('test_ac1_circuit_opens_after_5_consecutive_failures', async () => {
    // Arrange
    flagEvalStub.rejects(new Error('flagd connection error'));
    const defaultValue = false;

    // Act: Call 5 times, all should fail but return default
    for (let i = 0; i < 5; i++) {
      const result = await getFlagValue('test-flag', defaultValue);
      expect(result).to.equal(defaultValue);
    }

    // Verify stub was called 5 times
    expect(flagEvalStub.callCount).to.equal(5);

    // Act: 6th call, circuit should be open, no more calls to flagd
    const result = await getFlagValue('test-flag', defaultValue);
    expect(result).to.equal(defaultValue);
    expect(flagEvalStub.callCount).to.equal(5); // No additional call

    // Verify metrics
    const stateMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.state').get();
    expect(stateMetric.values[0].value).to.equal(1); // 1 = open state

    const opensMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.opens_total').get();
    expect(opensMetric.values[0].value).to.equal(1);

    const fallbackMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.fallback_calls_total').get();
    expect(fallbackMetric.values[0].value).to.equal(6); // 5 failures + 1 open circuit
  });

  /**
   * AC-2: Given the circuit has been open for 30 seconds, then the circuit transitions
   * to half-open state: the next flag call will attempt to connect to flagd, if the call
   * succeeds the circuit transitions to closed state; if the call fails the circuit re-opens for another 30 seconds.
   */
  it('test_ac2_circuit_transitions_to_half_open_after_30s', async () => {
    // Arrange: Open the circuit first
    flagEvalStub.rejects(new Error('flagd connection error'));
    const defaultValue = false;
    for (let i = 0; i < 5; i++) {
      await getFlagValue('test-flag', defaultValue);
    }
    expect(flagEvalStub.callCount).to.equal(5);

    // Fast forward time 30 seconds
    sandbox.clock.tick(30000);

    // Now stub should succeed
    flagEvalStub.resolves(true);

    // Act: Call flag
    const result = await getFlagValue('test-flag', defaultValue);
    expect(result).to.equal(true);
    expect(flagEvalStub.callCount).to.equal(6); // Should have called again

    // Verify circuit is now closed
    const stateMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.state').get();
    expect(stateMetric.values[0].value).to.equal(0); // 0 = closed state

    // Test failure scenario in half-open
    // Re-open circuit first
    flagEvalStub.rejects(new Error('flagd connection error'));
    for (let i = 0; i < 5; i++) {
      await getFlagValue('test-flag', defaultValue);
    }
    expect(flagEvalStub.callCount).to.equal(11);
    sandbox.clock.tick(30000);

    // Act: Call fails in half-open, circuit re-opens
    const failResult = await getFlagValue('test-flag', defaultValue);
    expect(failResult).to.equal(defaultValue);
    expect(flagEvalStub.callCount).to.equal(12);

    // Verify circuit is open again
    const reopenedStateMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.state').get();
    expect(reopenedStateMetric.values[0].value).to.equal(1);
    const opensCountMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.opens_total').get();
    expect(opensCountMetric.values[0].value).to.equal(2);
  });

  /**
   * AC-3: Given the circuit is closed or half-open, existing flagd retry logic executes
   * as configured before the circuit breaker processes the failure.
   */
  it('test_ac3_retry_logic_runs_before_circuit_breaker_counts_failure', async () => {
    // Arrange: We have 3 retries configured per call
    flagEvalStub.rejects(new Error('flagd timeout'));
    const defaultValue = false;

    // Act: Single call should trigger retries first
    await getFlagValue('test-flag', defaultValue);

    // Verify stub was called 3 times (retries) for this single flag call
    expect(flagEvalStub.callCount).to.equal(3); // Assuming retry config is 3 attempts
  });

  /**
   * AC-4: Given the circuit is open, no retries are executed for flag calls,
   * defaultValue is returned immediately.
   */
  it('test_ac4_no_retries_when_circuit_open', async () => {
    // Arrange: Open the circuit
    flagEvalStub.rejects(new Error('flagd connection error'));
    const defaultValue = false;
    for (let i = 0; i < 5; i++) {
      await getFlagValue('test-flag', defaultValue);
    }
    const callCountAfterOpen = flagEvalStub.callCount;

    // Act: Make a call when circuit is open
    const result = await getFlagValue('test-flag', defaultValue);
    expect(result).to.equal(defaultValue);

    // Verify no additional calls (no retries)
    expect(flagEvalStub.callCount).to.equal(callCountAfterOpen);
  });

  /**
   * AC-5: All 3 circuit breaker metrics defined in the Interface section are exposed
   * via the service's prometheus metrics endpoint, with values updated correctly on state transitions and fallback calls.
   */
  it('test_ac5_all_circuit_breaker_metrics_exposed_and_updated', async () => {
    // Check all metrics exist
    expect(promClient.register.getSingleMetric('feature_flag.circuit_breaker.state')).to.exist;
    expect(promClient.register.getSingleMetric('feature_flag.circuit_breaker.opens_total')).to.exist;
    expect(promClient.register.getSingleMetric('feature_flag.circuit_breaker.fallback_calls_total')).to.exist;

    // Initial state: closed, 0 opens, 0 fallbacks
    let stateMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.state').get();
    expect(stateMetric.values[0].value).to.equal(0); // closed
    expect(stateMetric.values[0].labels.service).to.equal('paymentservice');

    let opensMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.opens_total').get();
    expect(opensMetric.values[0].value).to.equal(0);
    expect(opensMetric.values[0].labels.service).to.equal('paymentservice');

    let fallbackMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.fallback_calls_total').get();
    expect(fallbackMetric.values[0].value).to.equal(0);
    expect(fallbackMetric.values[0].labels.service).to.equal('paymentservice');

    // Trigger a failure
    flagEvalStub.rejects(new Error('error'));
    await getFlagValue('test-flag', false);

    // Fallback count should be 1
    fallbackMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.fallback_calls_total').get();
    expect(fallbackMetric.values[0].value).to.equal(1);
  });

  /**
   * AC-6: When flagd is available and the circuit is closed, flag evaluation behavior
   * is identical to the current implementation: flags are resolved normally, retries work as expected,
   * no default values are returned unless explicitly configured.
   */
  it('test_ac6_normal_behavior_when_circuit_closed_and_flagd_available', async () => {
    // Arrange
    const expectedValue = true;
    flagEvalStub.resolves(expectedValue);
    const defaultValue = false;

    // Act
    const result = await getFlagValue('test-flag', defaultValue);

    // Assert
    expect(result).to.equal(expectedValue);
    expect(flagEvalStub.callCount).to.equal(1);

    // No fallbacks recorded
    const fallbackMetric = await promClient.register.getSingleMetric('feature_flag.circuit_breaker.fallback_calls_total').get();
    expect(fallbackMetric.values[0].value).to.equal(0);
  });
});
