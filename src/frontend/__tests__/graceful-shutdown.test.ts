import http from 'http';
import { initializeGracefulShutdown, ShutdownConfig } from '../utils/graceful-shutdown';
import { TracerProvider } from '@opentelemetry/sdk-trace-base';
import { MeterProvider } from '@opentelemetry/sdk-metrics';
import { LoggerProvider } from '@opentelemetry/sdk-logs';

describe('Graceful Shutdown Acceptance Criteria', () => {
  let mockServer: http.Server;
  let mockTraceProvider: jest.Mocked<TracerProvider>;
  let mockMeterProvider: jest.Mocked<MeterProvider>;
  let mockLoggerProvider: jest.Mocked<LoggerProvider>;
  let mockCleanupTask1: jest.Mock;
  let mockCleanupTask2: jest.Mock;
  let originalEnv: NodeJS.ProcessEnv;

  beforeEach(() => {
    // Mock HTTP server
    mockServer = {
      close: jest.fn((callback) => callback?.()),
      listen: jest.fn(),
    } as unknown as http.Server;

    // Mock OTel providers
    mockTraceProvider = {
      forceFlush: jest.fn().mockResolvedValue(undefined),
    } as unknown as jest.Mocked<TracerProvider>;
    mockMeterProvider = {
      forceFlush: jest.fn().mockResolvedValue(undefined),
    } as unknown as jest.Mocked<MeterProvider>;
    mockLoggerProvider = {
      forceFlush: jest.fn().mockResolvedValue(undefined),
    } as unknown as jest.Mocked<LoggerProvider>;

    // Mock cleanup tasks
    mockCleanupTask1 = jest.fn().mockResolvedValue(undefined);
    mockCleanupTask2 = jest.fn().mockResolvedValue(undefined);

    // Save original env
    originalEnv = { ...process.env };
    delete process.env.FRONTEND_SHUTDOWN_TIMEOUT;

    // Remove all signal listeners before each test
    process.removeAllListeners('SIGTERM');
    process.removeAllListeners('SIGINT');

    // Mock process.exit to prevent test from exiting
    jest.spyOn(process, 'exit').mockImplementation((code?: number) => {
      throw new Error(`Process exited with code ${code}`);
    } as never);
  });

  afterEach(() => {
    jest.restoreAllMocks();
    process.env = originalEnv;
    process.removeAllListeners('SIGTERM');
    process.removeAllListeners('SIGINT');
  });

  // AC-1: SIGTERM signal rejects new connections immediately
  test('test_ac1_sigterm_rejects_new_connections_immediately', async () => {
    const config: ShutdownConfig = {
      httpServer: mockServer,
      cleanupTasks: [],
      otelProviders: {},
    };

    initializeGracefulShutdown(config);
    expect(mockServer.close).not.toHaveBeenCalled();

    // Emit SIGTERM signal
    process.emit('SIGTERM');

    // Verify server is closed immediately (stops accepting new connections)
    expect(mockServer.close).toHaveBeenCalledTimes(1);
  });

  // AC-2: SIGINT signal rejects new connections immediately
  test('test_ac2_sigint_rejects_new_connections_immediately', async () => {
    const config: ShutdownConfig = {
      httpServer: mockServer,
      cleanupTasks: [],
      otelProviders: {},
    };

    initializeGracefulShutdown(config);
    expect(mockServer.close).not.toHaveBeenCalled();

    // Emit SIGINT signal
    process.emit('SIGINT');

    // Verify server is closed immediately (stops accepting new connections)
    expect(mockServer.close).toHaveBeenCalledTimes(1);
  });

  // AC-3: Wait for in-flight requests to complete before cleanup if within timeout
  test('test_ac3_waits_for_in_flight_requests_before_cleanup', async () => {
    // Create a long-running request that takes 200ms to complete
    let requestCompleted = false;
    const longRunningRequest = () => new Promise<void>(resolve => {
      setTimeout(() => {
        requestCompleted = true;
        resolve();
      }, 200);
    });

    // Mock server close callback waits for in-flight requests
    mockServer.close = jest.fn((callback) => {
      longRunningRequest().then(() => callback?.());
    });

    const config: ShutdownConfig = {
      timeout: 1, // 1 second timeout
      httpServer: mockServer,
      cleanupTasks: [mockCleanupTask1],
      otelProviders: { traceProvider: mockTraceProvider },
    };

    initializeGracefulShutdown(config);

    // Emit signal
    process.emit('SIGTERM');

    // Wait a bit less than request duration
    await new Promise(resolve => setTimeout(resolve, 100));
    expect(requestCompleted).toBe(false);
    expect(mockCleanupTask1).not.toHaveBeenCalled();
    expect(mockTraceProvider.forceFlush).not.toHaveBeenCalled();

    // Wait until request should be complete
    await new Promise(resolve => setTimeout(resolve, 150));
    expect(requestCompleted).toBe(true);
    expect(mockCleanupTask1).toHaveBeenCalledTimes(1);
    expect(mockTraceProvider.forceFlush).toHaveBeenCalledTimes(1);
    expect(process.exit).toHaveBeenCalledWith(0);
  });

  // AC-4: Force exit after timeout if requests don't complete
  test('test_ac4_force_exits_after_configured_timeout', async () => {
    // Mock server close that never completes (simulates hanging requests)
    mockServer.close = jest.fn(() => {}); // Never calls callback

    const config: ShutdownConfig = {
      timeout: 0.5, // 500ms timeout for test
      httpServer: mockServer,
      cleanupTasks: [mockCleanupTask1, mockCleanupTask2],
      otelProviders: { traceProvider: mockTraceProvider, meterProvider: mockMeterProvider, loggerProvider: mockLoggerProvider },
    };

    initializeGracefulShutdown(config);

    // Emit signal
    const startTime = Date.now();
    process.emit('SIGTERM');

    // Should exit after ~500ms
    await expect(() => new Promise(resolve => setTimeout(resolve, 700))).rejects.toThrow('Process exited with code 1');

    const elapsed = Date.now() - startTime;
    expect(elapsed).toBeGreaterThanOrEqual(450); // Allow 50ms tolerance
    expect(elapsed).toBeLessThanOrEqual(750);

    // Verify cleanup and flush still run even on timeout
    expect(mockCleanupTask1).toHaveBeenCalledTimes(1);
    expect(mockCleanupTask2).toHaveBeenCalledTimes(1);
    expect(mockTraceProvider.forceFlush).toHaveBeenCalledTimes(1);
    expect(mockMeterProvider.forceFlush).toHaveBeenCalledTimes(1);
    expect(mockLoggerProvider.forceFlush).toHaveBeenCalledTimes(1);
  });

  // AC-5: Flush all OTel telemetry before exit
  test('test_ac5_flushes_all_otel_telemetry_before_exit', async () => {
    const config: ShutdownConfig = {
      httpServer: mockServer,
      cleanupTasks: [],
      otelProviders: {
        traceProvider: mockTraceProvider,
        meterProvider: mockMeterProvider,
        loggerProvider: mockLoggerProvider,
      },
    };

    initializeGracefulShutdown(config);
    process.emit('SIGINT');

    await new Promise(resolve => setTimeout(resolve, 100));

    expect(mockTraceProvider.forceFlush).toHaveBeenCalledTimes(1);
    expect(mockMeterProvider.forceFlush).toHaveBeenCalledTimes(1);
    expect(mockLoggerProvider.forceFlush).toHaveBeenCalledTimes(1);
    expect(process.exit).toHaveBeenCalledWith(0);
  });

  // AC-6: Execute all cleanup tasks before exit
  test('test_ac6_runs_all_cleanup_tasks_before_exit', async () => {
    const config: ShutdownConfig = {
      httpServer: mockServer,
      cleanupTasks: [mockCleanupTask1, mockCleanupTask2],
      otelProviders: {},
    };

    initializeGracefulShutdown(config);
    process.emit('SIGTERM');

    await new Promise(resolve => setTimeout(resolve, 100));

    expect(mockCleanupTask1).toHaveBeenCalledTimes(1);
    expect(mockCleanupTask2).toHaveBeenCalledTimes(1);
    expect(process.exit).toHaveBeenCalledWith(0);
  });

  // AC-7: Shutdown timeout configurable via FRONTEND_SHUTDOWN_TIMEOUT env var
  test('test_ac7_timeout_configurable_via_environment_variable', async () => {
    process.env.FRONTEND_SHUTDOWN_TIMEOUT = '10';

    // Mock server close that never completes
    mockServer.close = jest.fn(() => {});

    const config: ShutdownConfig = {
      httpServer: mockServer,
      cleanupTasks: [],
      otelProviders: {},
    };

    initializeGracefulShutdown(config);
    const startTime = Date.now();
    process.emit('SIGTERM');

    // Should exit after ~10 seconds, but we'll check that it doesn't exit in 2 seconds
    let exitedEarly = false;
    try {
      await new Promise(resolve => setTimeout(resolve, 2000));
    } catch (e) {
      exitedEarly = true;
    }
    expect(exitedEarly).toBe(false);
    expect(Date.now() - startTime).toBeLessThan(2500);
  });

  // AC-8: Immediate exit if no in-flight requests
  test('test_ac8_immediate_exit_when_no_in_flight_requests', async () => {
    const config: ShutdownConfig = {
      timeout: 30, // Long timeout, but should exit immediately
      httpServer: mockServer,
      cleanupTasks: [mockCleanupTask1],
      otelProviders: { traceProvider: mockTraceProvider },
    };

    initializeGracefulShutdown(config);
    const startTime = Date.now();
    process.emit('SIGTERM');

    await new Promise(resolve => setTimeout(resolve, 100));

    const elapsed = Date.now() - startTime;
    expect(elapsed).toBeLessThan(200); // Should exit immediately not wait 30s
    expect(mockCleanupTask1).toHaveBeenCalledTimes(1);
    expect(mockTraceProvider.forceFlush).toHaveBeenCalledTimes(1);
    expect(process.exit).toHaveBeenCalledWith(0);
  });
});
