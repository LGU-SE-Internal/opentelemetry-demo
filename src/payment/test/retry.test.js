const { expect } = require('chai');
const sinon = require('sinon');
const { withRetry, MaxRetriesExceededError, NonIdempotentRetryAttemptError } = require('../retry');
const { metrics } = require('../opentelemetry');
const logger = require('../logger');

describe('retry logic', () => {
  let clock;
  let metricsStub;
  let loggerStub;

  beforeEach(() => {
    clock = sinon.useFakeTimers();
    metricsStub = {
      add: sinon.stub()
    };
    sinon.stub(metrics, 'getCounter').returns(metricsStub);
    loggerStub = sinon.stub(logger, 'warn');
    delete process.env.PAYMENT_SERVICE_RETRY_IDEMPOTENCY_ONLY;
  });

  afterEach(() => {
    clock.restore();
    sinon.restore();
    delete process.env.PAYMENT_SERVICE_RETRY_IDEMPOTENCY_ONLY;
  });

  describe('ac1_transient_error_retries_with_exponential_backoff', () => {
    it('should retry transient errors up to max attempts with exponential backoff', async () => {
      let callCount = 0;
      const testFn = async () => {
        callCount++;
        if (callCount < 3) {
          const err = new Error('Connection reset');
          err.code = 'ECONNRESET';
          throw err;
        }
        return 'success';
      };

      const promise = withRetry(testFn, {
        serviceName: 'test-service',
        callType: 'test-call',
        maxAttempts: 3,
        initialDelayMs: 100,
        isIdempotent: true
      });

      // First attempt fails
      expect(callCount).to.equal(1);
      await Promise.resolve(); // Let promise resolve
      clock.tick(100); // Wait first retry delay
      await Promise.resolve();
      expect(callCount).to.equal(2); // Second attempt fails
      clock.tick(200); // Wait second retry delay
      await Promise.resolve();
      expect(callCount).to.equal(3); // Third attempt succeeds

      const result = await promise;
      expect(result).to.equal('success');
      expect(metricsStub.add.calledTwice).to.be.true; // Two retry attempts
      expect(metricsStub.add.firstCall.calledWith(1, {
        service_name: 'test-service',
        call_type: 'test-call',
        attempt_number: 1
      })).to.be.true;
      expect(metricsStub.add.secondCall.calledWith(1, {
        service_name: 'test-service',
        call_type: 'test-call',
        attempt_number: 2
      })).to.be.true;
    });
  });

  describe('ac2_max_attempts_zero_no_retries', () => {
    it('should not retry when maxAttempts is 0', async () => {
      let callCount = 0;
      const testFn = async () => {
        callCount++;
        const err = new Error('Connection reset');
        err.code = 'ECONNRESET';
        throw err;
      };

      try {
        await withRetry(testFn, {
          serviceName: 'test-service',
          callType: 'test-call',
          maxAttempts: 0,
          initialDelayMs: 100,
          isIdempotent: true
        });
        expect.fail('Should have thrown error');
      } catch (err) {
        expect(err.code).to.equal('ECONNRESET');
        expect(callCount).to.equal(1);
        expect(metricsStub.add.called).to.be.false;
      }
    });
  });

  describe('ac3_initial_delay_uses_exponential_backoff', () => {
    it('should use exponential backoff with custom initial delay', async () => {
      let callCount = 0;
      const setTimeoutSpy = sinon.spy(global, 'setTimeout');
      const testFn = async () => {
        callCount++;
        if (callCount < 4) {
          const err = new Error('Timeout');
          err.code = 'ETIMEDOUT';
          throw err;
        }
        return 'success';
      };

      const promise = withRetry(testFn, {
        serviceName: 'test-service',
        callType: 'test-call',
        maxAttempts: 4,
        initialDelayMs: 200,
        isIdempotent: true
      });

      expect(callCount).to.equal(1);
      await Promise.resolve();
      clock.tick(200); // First retry delay 200ms
      await Promise.resolve();
      expect(callCount).to.equal(2);
      expect(setTimeoutSpy.firstCall.calledWith(sinon.match.func, 200)).to.be.true;
      
      clock.tick(400); // Second retry delay 400ms
      await Promise.resolve();
      expect(callCount).to.equal(3);
      expect(setTimeoutSpy.secondCall.calledWith(sinon.match.func, 400)).to.be.true;
      
      clock.tick(800); // Third retry delay 800ms
      await Promise.resolve();
      expect(callCount).to.equal(4);
      expect(setTimeoutSpy.thirdCall.calledWith(sinon.match.func, 800)).to.be.true;

      const result = await promise;
      expect(result).to.equal('success');
    });
  });
});
