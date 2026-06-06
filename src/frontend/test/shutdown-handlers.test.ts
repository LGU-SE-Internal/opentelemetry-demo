import type { NextServer } from 'next/dist/server/next';
import type { ShutdownConfig, setupShutdownHandlers } from '../server'; // This will be exported from server.js/ts once implemented

// Mock logger implementation for testing
const mockLogger = {
  info: jest.fn(),
  warn: jest.fn(),
  error: jest.fn()
};

// Mock Next.js server instance
const mockNextServer = {
  close: jest.fn().mockResolvedValue(undefined),
  server: {
    close: jest.fn((cb) => cb()),
    address: jest.fn().mockReturnValue({ port: 3000, family: 'IPv4', address: '0.0.0.0' })
  }
} as unknown as NextServer;

describe('Graceful Shutdown Acceptance Criteria Tests', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
    // Remove any existing signal listeners before each test
    process.removeAllListeners('SIGINT');
    process.removeAllListeners('SIGTERM');
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test('ac1_sigterm_stops_accepting_new_connections', async () => {
    // Arrange
    const config: ShutdownConfig = {
      timeoutMs: 30000,
      logger: mockLogger
    };
    setupShutdownHandlers(mockNextServer, config);

    // Act
    process.emit('SIGTERM');
    await new Promise(process.nextTick);

    // Assert: server close should have been called to stop accepting new connections
    expect(mockNextServer.server.close).toHaveBeenCalledTimes(1);
    
    // Verify new connections would be refused (test that server is no longer listening)
    const net = require('net');
    const socket = new net.Socket();
    const connectionPromise = new Promise((resolve, reject) => {
      socket.connect(3000, 'localhost', () => reject(new Error('Connection should have been refused')));
      socket.on('error', (err) => {
        expect(err.message).toContain('ECONNREFUSED');
        resolve(true);
      });
    });
    await expect(connectionPromise).resolves.toBe(true);
  });

  test('ac2_in_flight_requests_complete_before_exit', async () => {
    // Arrange
    const config: ShutdownConfig = {
      timeoutMs: 30000,
      logger: mockLogger
    };
    setupShutdownHandlers(mockNextServer, config);

    // Simulate an in-flight request that takes 200ms to complete
    let requestCompleted = false;
    const inFlightRequest = new Promise(resolve => {
      setTimeout(() => {
        requestCompleted = true;
        resolve(true);
      }, 200);
    });

    // Act: send SIGINT immediately after starting request
    process.emit('SIGINT');
    
    // Fast-forward time by 200ms
    jest.advanceTimersByTime(200);
    await inFlightRequest;

    // Assert: request should have completed before process exit
    expect(requestCompleted).toBe(true);
    // Verify process didn't exit before request completed
    expect(process.exit).not.toHaveBeenCalled();
  });

  test('ac3_timeout_triggers_force_exit_with_incomplete_requests', async () => {
    // Arrange
    const testTimeout = 1000;
    const config: ShutdownConfig = {
      timeoutMs: testTimeout,
      logger: mockLogger
    };
    setupShutdownHandlers(mockNextServer, config);

    // Mock process.exit to prevent actual test exit
    const mockExit = jest.spyOn(process, 'exit').mockImplementation((code?: number) => { throw new Error(`Process exit called with code ${code}`); });

    // Simulate a long-running request that takes longer than timeout
    let requestCompleted = false;
    const longRequest = new Promise(resolve => {
      setTimeout(() => {
        requestCompleted = true;
        resolve(true);
      }, 2000);
    });

    // Act: send SIGINT
    process.emit('SIGINT');
    
    // Fast-forward time past timeout
    try {
      jest.advanceTimersByTime(testTimeout + 100);
      await new Promise(process.nextTick);
    } catch (err) {
      expect(err.message).toContain('Process exit called with code 1');
    }

    // Assert: process should have exited, request not completed
    expect(mockExit).toHaveBeenCalledTimes(1);
    expect(requestCompleted).toBe(false);
    // Verify warning log with incomplete request count exists
    expect(mockLogger.warn).toHaveBeenCalledWith(
      expect.objectContaining({
        event_type: 'shutdown',
        event_subtype: 'timeout_triggered',
        details: expect.objectContaining({
          timeoutMs: testTimeout,
          incompleteRequests: expect.any(Number)
        })
      }),
      expect.any(String)
    );
  });

  test('ac4_upstream_connections_closed_before_exit', async () => {
    // Arrange
    const config: ShutdownConfig = {
      timeoutMs: 30000,
      logger: mockLogger
    };
    // Mock existing upstream connections in the global gateways
    const mockGrpcClientClose = jest.fn();
    (global as any).productCatalogClient = { close: mockGrpcClientClose };
    (global as any).cartClient = { close: mockGrpcClientClose };
    (global as any).checkoutClient = { close: mockGrpcClientClose };

    setupShutdownHandlers(mockNextServer, config);

    // Act
    process.emit('SIGTERM');
    jest.advanceTimersByTime(1000);
    await new Promise(process.nextTick);

    // Assert: all upstream connections should be closed
    expect(mockGrpcClientClose).toHaveBeenCalledTimes(3);
    // Verify log entry for closed connections
    expect(mockLogger.info).toHaveBeenCalledWith(
      expect.objectContaining({
        event_type: 'shutdown',
        event_subtype: 'upstream_connections_closed',
        details: expect.objectContaining({ closedConnections: 3 })
      }),
      expect.any(String)
    );
  });

  test('ac5_all_shutdown_events_logged_structured', async () => {
    // Arrange
    const config: ShutdownConfig = {
      timeoutMs: 30000,
      logger: mockLogger
    };
    setupShutdownHandlers(mockNextServer, config);

    // Act
    process.emit('SIGINT');
    jest.advanceTimersByTime(500);
    await new Promise(process.nextTick);

    // Assert: all expected log events exist
    const logCalls = mockLogger.info.mock.calls.map(call => call[0]);
    
    // Check signal received event
    expect(logCalls).toContainEqual(expect.objectContaining({
      event_type: 'shutdown',
      event_subtype: 'signal_received',
      details: expect.objectContaining({ signal: 'SIGINT' }),
      timestamp: expect.any(String)
    }));

    // Check new connections blocked event
    expect(logCalls).toContainEqual(expect.objectContaining({
      event_type: 'shutdown',
      event_subtype: 'new_connections_blocked',
      timestamp: expect.any(String)
    }));

    // Check upstream connections closed event
    expect(logCalls).toContainEqual(expect.objectContaining({
      event_type: 'shutdown',
      event_subtype: 'upstream_connections_closed',
      timestamp: expect.any(String)
    }));

    // Check shutdown complete event
    expect(logCalls).toContainEqual(expect.objectContaining({
      event_type: 'shutdown',
      event_subtype: 'shutdown_complete',
      details: expect.objectContaining({
        durationMs: expect.any(Number),
        completedRequests: expect.any(Number)
      }),
      timestamp: expect.any(String)
    }));
  });

  test('ac6_fast_exit_with_no_in_flight_requests', async () => {
    // Arrange
    const config: ShutdownConfig = {
      timeoutMs: 30000,
      logger: mockLogger
    };
    setupShutdownHandlers(mockNextServer, config);
    const mockExit = jest.spyOn(process, 'exit').mockImplementation(() => { throw new Error('Exit called'); });

    // Act
    const startTime = Date.now();
    try {
      process.emit('SIGTERM');
      jest.advanceTimersByTime(1000);
      await new Promise(process.nextTick);
    } catch (err) {}

    // Assert: exit should have been called within 1 second
    expect(mockExit).toHaveBeenCalledTimes(1);
    const timeDiff = Date.now() - startTime;
    expect(timeDiff).toBeLessThan(1000);
  });
});
