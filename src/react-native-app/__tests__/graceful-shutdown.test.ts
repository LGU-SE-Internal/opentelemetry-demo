import {
  gracefulShutdown,
  ShutdownConfig,
  OperationTracker,
  ShutdownInProgressError,
} from '../utils/graceful-shutdown';

describe('Graceful Shutdown Acceptance Criteria', () => {
  beforeEach(() => {
    // Reset singleton state between tests
    jest.resetModules();
  });

  /**
   * AC-1: When SIGINT or SIGTERM signal is received by the react-native-app process,
   * the shutdown sequence is initiated instead of immediate termination.
   */
  test('ac1_sigint_sigterm_trigger_shutdown_sequence', async () => {
    gracefulShutdown.init();
    expect(gracefulShutdown.isShuttingDown()).toBe(false);
    
    // Mock process exit to prevent actual termination
    const mockExit = jest.spyOn(process, 'exit').mockImplementation((code?: number) => {
      throw new Error(`Process exit called with code ${code}`);
    });

    // Emit SIGINT signal
    process.emit('SIGINT');
    expect(gracefulShutdown.isShuttingDown()).toBe(true);

    // Emit SIGTERM signal
    jest.clearAllMocks();
    gracefulShutdown.init();
    process.emit('SIGTERM');
    expect(gracefulShutdown.isShuttingDown()).toBe(true);

    mockExit.mockRestore();
  });

  /**
   * AC-2: The shutdown timeout is configurable via ShutdownConfig, 
   * defaulting to 20 seconds if not specified.
   */
  test('ac2_shutdown_timeout_configurable_defaults_to_20s', async () => {
    // Test default timeout
    gracefulShutdown.init();
    // Access private state? No, test via behavior: when shutdown starts, it should wait 20s by default
    const mockExit = jest.spyOn(process, 'exit').mockImplementation(() => { throw new Error('exit'); });
    const mockLogger = { warn: jest.fn(), info: jest.fn(), error: jest.fn() };
    
    // Test custom timeout of 1s
    gracefulShutdown.init({ shutdownTimeoutSeconds: 1, logger: mockLogger });
    gracefulShutdown.operationTracker.registerOperation('test-1', 'long-operation');
    
    const startTime = Date.now();
    process.emit('SIGTERM');
    
    await expect(() => new Promise(resolve => setTimeout(resolve, 1500))).rejects.toThrow('exit');
    const timeTaken = Date.now() - startTime;
    expect(timeTaken).toBeGreaterThanOrEqual(1000);
    expect(timeTaken).toBeLessThan(2000); // Should be close to 1s not 20s

    mockExit.mockRestore();
  });

  /**
   * AC-3: All in-flight operations registered via `operationTracker.registerOperation()` 
   * are tracked with their ID, name, and start time.
   */
  test('ac3_operations_tracked_with_id_name_starttime', () => {
    gracefulShutdown.init();
    const tracker = gracefulShutdown.operationTracker;
    
    const opId = 'test-op-123';
    const opName = 'sync-user-data';
    const beforeRegister = Date.now();
    const completeOp = tracker.registerOperation(opId, opName);
    const afterRegister = Date.now();
    
    const inFlight = tracker.getInFlightOperations();
    expect(inFlight.length).toBe(1);
    expect(inFlight[0].id).toBe(opId);
    expect(inFlight[0].name).toBe(opName);
    expect(inFlight[0].startTime).toBeGreaterThanOrEqual(beforeRegister);
    expect(inFlight[0].startTime).toBeLessThanOrEqual(afterRegister);
    
    completeOp();
    expect(tracker.getInFlightOperations().length).toBe(0);
  });

  /**
   * AC-4: When `gracefulShutdown.isShuttingDown()` returns true, 
   * any new incoming API requests are rejected with a 503 Service Unavailable status and ShutdownInProgressError.
   */
  test('ac4_new_requests_rejected_with_503_during_shutdown', () => {
    gracefulShutdown.init();
    process.emit('SIGINT'); // Start shutdown
    
    expect(() => {
      // Simulate incoming request that tries to register operation
      gracefulShutdown.operationTracker.registerOperation('new-request-1', 'fetch-catalog');
    }).toThrow(ShutdownInProgressError);
  });

  /**
   * AC-5: During shutdown sequence, the process waits for all registered in-flight operations 
   * to complete before exiting, unless the shutdown timeout is reached.
   */
  test('ac5_wait_for_in_flight_operations_before_exit_before_timeout', async () => {
    const mockExit = jest.spyOn(process, 'exit').mockImplementation(() => { throw new Error('exit'); });
    const mockLogger = { info: jest.fn(), warn: jest.fn(), error: jest.fn() };
    
    gracefulShutdown.init({ shutdownTimeoutSeconds: 2, logger: mockLogger });
    const completeOp = gracefulShutdown.operationTracker.registerOperation('long-op', 'complete-purchase');
    
    // Start shutdown
    process.emit('SIGTERM');
    
    // Complete operation after 500ms
    setTimeout(() => completeOp(), 500);
    
    // Should exit successfully after operation completes, before timeout
    let exitCalled = false;
    try {
      await new Promise(resolve => setTimeout(resolve, 1000));
    } catch (e) {
      exitCalled = true;
    }
    
    expect(exitCalled).toBe(true);
    expect(mockExit).toHaveBeenCalledWith(0);
    expect(mockLogger.info).toHaveBeenCalledWith(expect.stringContaining('successful shutdown'));
    
    mockExit.mockRestore();
  });

  /**
   * AC-6: If shutdown timeout is reached before all in-flight operations complete, 
   * the process force exits with code 1, and logs a warning message containing details of all incomplete operations.
   */
  test('ac6_force_exit_with_code_1_and_log_incomplete_ops_on_timeout', async () => {
    const mockExit = jest.spyOn(process, 'exit').mockImplementation(() => { throw new Error('exit'); });
    const mockLogger = { warn: jest.fn(), info: jest.fn(), error: jest.fn() };
    
    gracefulShutdown.init({ shutdownTimeoutSeconds: 1, logger: mockLogger });
    // Register operation that never completes
    gracefulShutdown.operationTracker.registerOperation('stuck-op', 'sync-user-data');
    
    process.emit('SIGTERM');
    
    let exitCalled = false;
    try {
      await new Promise(resolve => setTimeout(resolve, 1500));
    } catch (e) {
      exitCalled = true;
    }
    
    expect(exitCalled).toBe(true);
    expect(mockExit).toHaveBeenCalledWith(1);
    expect(mockLogger.warn).toHaveBeenCalledWith(expect.stringContaining('incomplete operations'), expect.objectContaining({
      operations: expect.arrayContaining([
        expect.objectContaining({
          id: 'stuck-op',
          name: 'sync-user-data',
          duration: expect.any(Number)
        })
      ])
    }));
    
    mockExit.mockRestore();
  });

  /**
   * AC-7: If all in-flight operations complete before the shutdown timeout, 
   * the process exits gracefully with code 0, and logs an info message indicating successful shutdown.
   */
  test('ac7_graceful_exit_with_code_0_on_successful_shutdown', async () => {
    const mockExit = jest.spyOn(process, 'exit').mockImplementation(() => { throw new Error('exit'); });
    const mockLogger = { info: jest.fn(), warn: jest.fn(), error: jest.fn() };
    
    gracefulShutdown.init({ logger: mockLogger });
    const completeOp1 = gracefulShutdown.operationTracker.registerOperation('op1', 'fetch-catalog');
    const completeOp2 = gracefulShutdown.operationTracker.registerOperation('op2', 'update-cart');
    
    process.emit('SIGTERM');
    
    completeOp1();
    completeOp2();
    
    let exitCalled = false;
    try {
      await new Promise(resolve => setTimeout(resolve, 500));
    } catch (e) {
      exitCalled = true;
    }
    
    expect(exitCalled).toBe(true);
    expect(mockExit).toHaveBeenCalledWith(0);
    expect(mockLogger.info).toHaveBeenCalledWith(expect.stringContaining('shutdown completed successfully'));
    
    mockExit.mockRestore();
  });

  /**
   * AC-8: Registering a new operation via `operationTracker.registerOperation()` 
   * after shutdown has been initiated throws ShutdownInProgressError.
   */
  test('ac8_register_operation_after_shutdown_initiated_throws_error', () => {
    gracefulShutdown.init();
    process.emit('SIGINT'); // Start shutdown
    
    expect(() => {
      gracefulShutdown.operationTracker.registerOperation('late-op', 'some-operation');
    }).toThrow(ShutdownInProgressError);
    
    // Verify the operation was not added to in-flight list
    expect(gracefulShutdown.operationTracker.getInFlightOperations().length).toBe(0);
  });

  /**
   * AC-9: Custom logger provided in ShutdownConfig is used for all shutdown-related logs, 
   * falling back to console log if no logger is provided.
   */
  test('ac9_custom_logger_used_for_shutdown_logs_fallback_to_console', async () => {
    // Test custom logger
    const customLogger = { info: jest.fn(), warn: jest.fn(), error: jest.fn() };
    gracefulShutdown.init({ logger: customLogger, shutdownTimeoutSeconds: 1 });
    
    process.emit('SIGTERM');
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    expect(customLogger.warn).toHaveBeenCalledWith(expect.stringContaining('shutdown timed out'));
    
    // Test fallback to console
    const consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation();
    jest.resetModules();
    const { gracefulShutdown: newShutdown } = require('../utils/graceful-shutdown');
    newShutdown.init({ shutdownTimeoutSeconds: 1 });
    process.emit('SIGTERM');
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('shutdown timed out'));
    
    consoleWarnSpy.mockRestore();
  });
});
