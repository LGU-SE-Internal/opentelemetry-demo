const http = require('http');
const { registerShutdownHandlers } = require('../index');
const sinon = require('sinon');
const request = require('supertest');
const { expect } = require('chai');

describe('Shutdown Handler Tests', () => {
  let server;
  let loggerMock;
  let cleanupHooks;
  let originalExit;
  let clock;

  beforeEach(() => {
    // Create a simple test server that responds after 100ms
    server = http.createServer((req, res) => {
      setTimeout(() => {
        res.statusCode = 200;
        res.end('OK');
      }, 100);
    });

    loggerMock = {
      info: sinon.stub(),
      error: sinon.stub()
    };

    cleanupHooks = [
      sinon.stub().resolves(), // database cleanup
      sinon.stub().resolves()  // stripe cleanup
    ];

    originalExit = process.exit;
    process.exit = sinon.stub();

    clock = sinon.useFakeTimers();
  });

  afterEach(() => {
    server.close();
    process.exit = originalExit;
    clock.restore();
    sinon.reset();
  });

  test('test_ac1_log_shutdown_started_on_signal', async () => {
    registerShutdownHandlers(server, cleanupHooks, 30000, loggerMock);

    // Emit SIGINT signal
    process.emit('SIGINT');

    // Verify shutdown started log is emitted with correct fields
    sinon.assert.calledWith(loggerMock.info, {
      event: 'service.shutdown.started',
      signal: 'SIGINT',
      gracePeriodMs: 30000
    });

    // Test SIGTERM as well
    process.emit('SIGTERM');
    sinon.assert.calledWith(loggerMock.info, {
      event: 'service.shutdown.started',
      signal: 'SIGTERM',
      gracePeriodMs: 30000
    });
  });

  test('test_ac2_server_stops_accepting_new_connections_after_shutdown', async () => {
    server.listen(0);
    await new Promise(resolve => server.on('listening', resolve));
    const port = server.address().port;

    // Verify server accepts connections before shutdown
    const resBefore = await request(`http://localhost:${port}`).get('/');
    expect(resBefore.statusCode).to.equal(200);

    registerShutdownHandlers(server, cleanupHooks, 30000, loggerMock);
    process.emit('SIGINT');

    // Verify new connections get 503
    const resAfter = await request(`http://localhost:${port}`).get('/');
    expect(resAfter.statusCode).to.equal(503);
  });

  test('test_ac3_waits_for_in_flight_requests_before_cleanup', async () => {
    server.listen(0);
    await new Promise(resolve => server.on('listening', resolve));
    const port = server.address().port;

    registerShutdownHandlers(server, cleanupHooks, 30000, loggerMock);

    // Start a request that takes 100ms to complete
    const inFlightReq = request(`http://localhost:${port}`).get('/');

    // Trigger shutdown immediately after sending request
    process.emit('SIGINT');

    // Fast-forward time 50ms - request still in flight, cleanup should not have run
    clock.tick(50);
    sinon.assert.notCalled(cleanupHooks[0]);
    sinon.assert.notCalled(cleanupHooks[1]);

    // Fast-forward another 100ms - request completed, cleanup should run
    clock.tick(100);
    sinon.assert.calledOnce(cleanupHooks[0]);
    sinon.assert.calledOnce(cleanupHooks[1]);
  });

  test('test_ac4_runs_cleanup_immediately_when_all_in_flight_complete_early', async () => {
    registerShutdownHandlers(server, cleanupHooks, 30000, loggerMock);
    process.emit('SIGINT');

    // No in-flight requests, cleanup should run immediately without waiting full 30s
    clock.tick(0);
    sinon.assert.calledOnce(cleanupHooks[0]);
    sinon.assert.calledOnce(cleanupHooks[1]);
    // Verify we haven't waited 30s
    sinon.assert.notCalled(loggerMock.error); // no forced shutdown log
  });

  test('test_ac5_forced_shutdown_after_grace_period', async () => {
    server.listen(0);
    await new Promise(resolve => server.on('listening', resolve));
    const port = server.address().port;

    registerShutdownHandlers(server, cleanupHooks, 30000, loggerMock);

    // Start a long running request that takes 40s to complete
    const longReq = request(`http://localhost:${port}`).get('/');

    // Trigger shutdown
    process.emit('SIGINT');

    // Fast-forward 30s - grace period exceeded
    clock.tick(30000);

    // Verify forced shutdown log
    sinon.assert.calledWith(loggerMock.info, {
      event: 'service.shutdown.forced',
      reason: 'grace_period_exceeded',
      inFlightRequestsCount: 1
    });

    // Verify cleanup runs immediately after grace period
    sinon.assert.calledOnce(cleanupHooks[0]);
    sinon.assert.calledOnce(cleanupHooks[1]);
  });

  test('test_ac6_cleanup_hooks_run_in_order', async () => {
    const thirdHook = sinon.stub().resolves();
    const orderedHooks = [cleanupHooks[0], cleanupHooks[1], thirdHook];

    registerShutdownHandlers(server, orderedHooks, 30000, loggerMock);
    process.emit('SIGINT');
    clock.tick(0);

    // Verify order: database first, then stripe, then others in order
    sinon.assert.callOrder(cleanupHooks[0], cleanupHooks[1], thirdHook);
  });

  test('test_ac7_shutdown_completed_log_on_success', async () => {
    registerShutdownHandlers(server, cleanupHooks, 30000, loggerMock);
    process.emit('SIGINT');
    clock.tick(500); // simulate 500ms total shutdown time

    // Verify completed log with duration
    sinon.assert.calledWith(loggerMock.info, sinon.match({
      event: 'service.shutdown.completed',
      durationMs: sinon.match(value => value >= 500 && value < 600)
    }));

    // Verify exit code 0
    sinon.assert.calledWith(process.exit, 0);
  });

  test('test_ac8_cleanup_failure_log_and_non_zero_exit', async () => {
    const failingDbHook = sinon.stub().rejects(new Error('Connection timeout'));
    const hooks = [failingDbHook, cleanupHooks[1]];

    registerShutdownHandlers(server, hooks, 30000, loggerMock);
    process.emit('SIGINT');
    clock.tick(0);

    // Verify cleanup failure log
    sinon.assert.calledWith(loggerMock.error, {
      event: 'service.shutdown.cleanup_failed',
      error: 'Connection timeout',
      resourceType: 'database'
    });

    // Verify second hook still runs even after first fails
    sinon.assert.calledOnce(cleanupHooks[1]);

    // Verify exit code 1
    sinon.assert.calledWith(process.exit, 1);
  });

  test('test_ac9_no_immediate_exit_on_signal', async () => {
    registerShutdownHandlers(server, cleanupHooks, 30000, loggerMock);

    // Emit signal
    process.emit('SIGINT');

    // Verify exit not called immediately
    sinon.assert.notCalled(process.exit);

    // Fast-forward to when all steps complete
    clock.tick(30000);

    // Verify exit was called eventually
    sinon.assert.calledOnce(process.exit);
  });
});
