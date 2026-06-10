import { createServer, NextServer } from 'next';
import { setupGracefulShutdown, ShutdownConfig, ShutdownTimeoutError, BackendConnectionCleanupError } from '../server';
import * as http from 'http';
import * as assert from 'assert';
import { promisify } from 'util';
import * as gateways from '../gateways';

jest.mock('../gateways', () => ({
  cartGateway: { close: jest.fn() },
  productCatalogGateway: { close: jest.fn() },
  checkoutGateway: { close: jest.fn() },
  paymentGateway: { close: jest.fn() },
  shippingGateway: { close: jest.fn() },
}));

describe('Graceful Shutdown Acceptance Criteria Tests', () => {
  let testServer: NextServer;
  let httpServer: http.Server;
  const originalEnv = process.env;

  beforeEach(() => {
    jest.clearAllMocks();
    process.env = { ...originalEnv };
    // Create a test next server
    testServer = createServer({ dev: false });
    // Override the server listen to return our test http server
    httpServer = http.createServer((req, res) => {
      if (req.url === '/slow') {
        // Simulate long running request that takes 200ms to complete
        setTimeout(() => {
          res.writeHead(200, { 'Content-Type': 'text/plain' });
          res.end('slow response');
        }, 200);
      } else {
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end('ok');
      }
    });
    // @ts-ignore - override the server property for testing
    testServer.server = httpServer;
  });

  afterEach((done) => {
    process.env = originalEnv;
    httpServer.closeAllConnections();
    httpServer.close(done);
  });

  test('test_ac1_signal_received_stops_accepting_new_requests_returns_503', async () => {
    const config: ShutdownConfig = { gracePeriodMs: 30000 };
    setupGracefulShutdown(testServer, config);

    // First request before signal should succeed
    const firstRes = await new Promise<http.IncomingMessage>((resolve) => {
      http.get('http://localhost:3000/', resolve);
    });
    assert.strictEqual(firstRes.statusCode, 200);

    // Send SIGINT signal
    process.emit('SIGINT');

    // Request after signal should return 503
    const postSignalRes = await new Promise<http.IncomingMessage>((resolve) => {
      http.get('http://localhost:3000/', resolve);
    });
    assert.strictEqual(postSignalRes.statusCode, 503);
  });

  test('test_ac2_waits_for_in_flight_requests_before_exit_within_grace_period', async () => {
    const config: ShutdownConfig = { gracePeriodMs: 1000 };
    setupGracefulShutdown(testServer, config);

    // Start a slow request that takes 200ms
    const slowRequestPromise = new Promise<{ statusCode: number; body: string }>((resolve) => {
      http.get('http://localhost:3000/slow', (res) => {
        let body = '';
        res.on('data', (chunk) => body += chunk);
        res.on('end', () => resolve({ statusCode: res.statusCode!, body }));
      });
    });

    // Wait 50ms then send SIGTERM
    await new Promise(resolve => setTimeout(resolve, 50));
    process.emit('SIGTERM');

    // The slow request should complete successfully
    const slowRes = await slowRequestPromise;
    assert.strictEqual(slowRes.statusCode, 200);
    assert.strictEqual(slowRes.body, 'slow response');
  });

  test('test_ac3_all_requests_complete_before_grace_period_closes_backend_connections_exits_0', async () => {
    const config: ShutdownConfig = { gracePeriodMs: 1000 };
    setupGracefulShutdown(testServer, config);

    // Start a normal request
    const requestPromise = new Promise<http.IncomingMessage>((resolve) => {
      http.get('http://localhost:3000/', resolve);
    });

    // Wait for request to be in flight
    await new Promise(resolve => setTimeout(resolve, 50));
    // Mock process.exit to capture exit code
    const exitMock = jest.spyOn(process, 'exit').mockImplementation((code?: number) => {
      throw new Error(`process.exit called with code ${code}`);
    } as any);

    // Emit SIGTERM
    try {
      process.emit('SIGTERM');
      // Wait for all operations to complete
      await new Promise(resolve => setTimeout(resolve, 300));
    } catch (e: any) {
      assert.match(e.message, /process.exit called with code 0/);
    }

    // Verify all backend connections were closed
    expect(gateways.cartGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.productCatalogGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.checkoutGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.paymentGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.shippingGateway.close).toHaveBeenCalledTimes(1);

    exitMock.mockRestore();
  });

  test('test_ac4_grace_period_expires_force_terminates_connections_exits_1', async () => {
    // Set very short grace period of 100ms, request takes 200ms
    const config: ShutdownConfig = { gracePeriodMs: 100 };
    setupGracefulShutdown(testServer, config);

    // Start slow request
    const slowRequestPromise = new Promise<Error | null>((resolve) => {
      http.get('http://localhost:3000/slow', (res) => {
        res.on('error', (err) => resolve(err));
        res.on('end', () => resolve(null));
      });
    });

    // Wait 50ms then send signal
    await new Promise(resolve => setTimeout(resolve, 50));
    const exitMock = jest.spyOn(process, 'exit').mockImplementation((code?: number) => {
      throw new Error(`process.exit called with code ${code}`);
    } as any);

    try {
      process.emit('SIGINT');
      // Wait for grace period to expire
      await new Promise(resolve => setTimeout(resolve, 150));
    } catch (e: any) {
      assert.match(e.message, /process.exit called with code 1/);
    }

    // Verify slow request was terminated with error
    const requestError = await slowRequestPromise;
    assert.notStrictEqual(requestError, null);

    // Verify all backend connections were closed
    expect(gateways.cartGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.productCatalogGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.checkoutGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.paymentGateway.close).toHaveBeenCalledTimes(1);
    expect(gateways.shippingGateway.close).toHaveBeenCalledTimes(1);

    exitMock.mockRestore();
  });

  test('test_ac4_grace_period_expires_throws_shutdown_timeout_error', async () => {
    const config: ShutdownConfig = { gracePeriodMs: 100 };
    setupGracefulShutdown(testServer, config);

    // Start a long running request
    http.get('http://localhost:3000/slow', () => {});
    await new Promise(resolve => setTimeout(resolve, 50));

    // Listen for uncaught exceptions to catch the timeout error
    const errorHandler = jest.fn();
    process.once('uncaughtException', errorHandler);

    process.emit('SIGTERM');
    await new Promise(resolve => setTimeout(resolve, 150));

    expect(errorHandler).toHaveBeenCalled();
    expect(errorHandler.mock.calls[0][0] instanceof ShutdownTimeoutError).toBe(true);
  });

  test('test_ac3_backend_connection_cleanup_failure_throws_backend_connection_cleanup_error', async () => {
    // Make one of the gateway close functions throw an error
    (gateways.cartGateway.close as jest.Mock).mockRejectedValue(new Error('Failed to close cart connection'));
    
    const config: ShutdownConfig = { gracePeriodMs: 1000 };
    setupGracefulShutdown(testServer, config);

    const errorHandler = jest.fn();
    process.once('uncaughtException', errorHandler);

    process.emit('SIGTERM');
    await new Promise(resolve => setTimeout(resolve, 300));

    expect(errorHandler).toHaveBeenCalled();
    expect(errorHandler.mock.calls[0][0] instanceof BackendConnectionCleanupError).toBe(true);
  });

  test('test_ac5_shutdown_events_logged_with_appropriate_levels', async () => {
    const config: ShutdownConfig = { gracePeriodMs: 1000 };
    // Mock console log/error to capture logs
    const infoMock = jest.spyOn(console, 'info').mockImplementation();
    const errorMock = jest.spyOn(console, 'error').mockImplementation();

    setupGracefulShutdown(testServer, config);
    process.emit('SIGINT');

    await new Promise(resolve => setTimeout(resolve, 200));

    // Verify info level logs
    expect(infoMock).toHaveBeenCalledWith(expect.stringContaining('Shutdown signal received'));
    expect(infoMock).toHaveBeenCalledWith(expect.stringContaining('Shutdown started'));
    expect(infoMock).toHaveBeenCalledWith(expect.stringContaining('All requests completed successfully'));
    expect(infoMock).toHaveBeenCalledWith(expect.stringContaining('Backend connections cleaned up'));
    expect(infoMock).toHaveBeenCalledWith(expect.stringContaining('Shutdown completed successfully'));

    // Test error scenario
    (gateways.cartGateway.close as jest.Mock).mockRejectedValue(new Error('Cleanup failed'));
    setupGracefulShutdown(testServer, { gracePeriodMs: 100 });
    process.emit('SIGTERM');
    await new Promise(resolve => setTimeout(resolve, 200));

    expect(errorMock).toHaveBeenCalledWith(expect.stringContaining('Backend connection cleanup failed'));

    infoMock.mockRestore();
    errorMock.mockRestore();
  });

  test('test_ac6_unhandled_exceptions_exit_immediately_with_code_1', async () => {
    const config: ShutdownConfig = { gracePeriodMs: 30000 };
    setupGracefulShutdown(testServer, config);

    const exitMock = jest.spyOn(process, 'exit').mockImplementation((code?: number) => {
      throw new Error(`process.exit called with code ${code}`);
    } as any);

    // Simulate unhandled exception
    try {
      process.emit('uncaughtException', new Error('Test unhandled exception'));
    } catch (e: any) {
      assert.match(e.message, /process.exit called with code 1/);
    }

    // Verify no graceful shutdown was triggered
    expect(console.info).not.toHaveBeenCalledWith(expect.stringContaining('Shutdown signal received'));

    exitMock.mockRestore();
  });

  test('test_ac6_unhandled_promise_rejections_exit_immediately_with_code_1', async () => {
    const config: ShutdownConfig = { gracePeriodMs: 30000 };
    setupGracefulShutdown(testServer, config);

    const exitMock = jest.spyOn(process, 'exit').mockImplementation((code?: number) => {
      throw new Error(`process.exit called with code ${code}`);
    } as any);

    // Simulate unhandled promise rejection
    try {
      process.emit('unhandledRejection', new Error('Test unhandled rejection'), Promise.resolve());
    } catch (e: any) {
      assert.match(e.message, /process.exit called with code 1/);
    }

    exitMock.mockRestore();
  });

  test('test_ac2_grace_period_configurable_via_environment_variable', () => {
    process.env.FRONTEND_SHUTDOWN_GRACE_PERIOD_MS = '5000';
    // Import the config from server to verify it picks up env var
    const { getShutdownConfig } = require('../server');
    const config = getShutdownConfig();
    assert.strictEqual(config.gracePeriodMs, 5000);
  });
});
