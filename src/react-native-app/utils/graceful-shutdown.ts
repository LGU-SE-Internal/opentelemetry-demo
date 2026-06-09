/**
 * Configuration for graceful shutdown handler
 */
export type ShutdownConfig = {
  /** Time in seconds to wait for in-flight operations before force exit */
  shutdownTimeoutSeconds?: number;
  /** Optional custom logger for shutdown events */
  logger?: {
    info: (message: string, metadata?: Record<string, unknown>) => void;
    warn: (message: string, metadata?: Record<string, unknown>) => void;
    error: (message: string, metadata?: Record<string, unknown>) => void;
  };
};

/**
 * Tracker for in-flight operations
 */
export interface OperationTracker {
  /**
   * Register a new in-flight operation
   * @param operationId Unique identifier for the operation
   * @param operationName Human-readable name of the operation (e.g. "sync-user-data", "complete-purchase")
   * @returns Function to call when operation completes
   */
  registerOperation: (operationId: string, operationName: string) => () => void;

  /**
   * Get list of currently in-flight operations
   * @returns Array of operation details
   */
  getInFlightOperations: () => Array<{ id: string; name: string; startTime: number }>;
}

/**
 * Graceful shutdown handler public API
 */
export interface GracefulShutdown {
  /**
   * Initialize shutdown handler with given configuration
   * @param config Shutdown configuration
   */
  init: (config?: ShutdownConfig) => void;

  /**
   * Get the operation tracker instance to register in-flight tasks/requests
   */
  operationTracker: OperationTracker;

  /**
   * Check if shutdown is currently in progress
   * @returns True if shutdown has been initiated
   */
  isShuttingDown: () => boolean;
}

/**
 * Error thrown when new requests are received during shutdown
 */
export class ShutdownInProgressError extends Error {
  constructor() {
    super("Service is shutting down, request rejected");
    this.name = "ShutdownInProgressError";
  }
}

// Internal state
type InFlightOperation = {
  id: string;
  name: string;
  startTime: number;
};

let isShuttingDownFlag = false;
let shutdownTimeoutMs = 20 * 1000; // Default 20 seconds
let logger: Required<ShutdownConfig>['logger'] = console;
let inFlightOperations = new Map<string, InFlightOperation>();
let shutdownTimeoutTimer: NodeJS.Timeout | null = null;

const checkAndExitIfComplete = () => {
  if (inFlightOperations.size === 0) {
    logger.info('Shutdown completed successfully, all in-flight operations finished');
    if (shutdownTimeoutTimer) {
      clearTimeout(shutdownTimeoutTimer);
    }
    process.exit(0);
  }
};

const startShutdownSequence = () => {
  if (isShuttingDownFlag) return;

  isShuttingDownFlag = true;
  logger.info('Shutdown sequence initiated, waiting for in-flight operations to complete', {
    timeoutSeconds: shutdownTimeoutMs / 1000,
    inFlightOperationsCount: inFlightOperations.size
  });

  // Set force exit timeout
  shutdownTimeoutTimer = setTimeout(() => {
    const incompleteOps = Array.from(inFlightOperations.values()).map(op => ({
      id: op.id,
      name: op.name,
      duration: Date.now() - op.startTime
    }));

    logger.warn('Shutdown timeout reached, force exiting with incomplete operations', {
      operations: incompleteOps
    });

    process.exit(1);
  }, shutdownTimeoutMs);

  // Check if we already have no in-flight operations
  checkAndExitIfComplete();
};

// Operation tracker implementation
const operationTracker: OperationTracker = {
  registerOperation: (operationId: string, operationName: string) => {
    if (isShuttingDownFlag) {
      throw new ShutdownInProgressError();
    }

    const startTime = Date.now();
    inFlightOperations.set(operationId, { id: operationId, name: operationName, startTime });

    return () => {
      inFlightOperations.delete(operationId);
      if (isShuttingDownFlag) {
        checkAndExitIfComplete();
      }
    };
  },

  getInFlightOperations: () => {
    return Array.from(inFlightOperations.values());
  }
};

// Graceful shutdown singleton implementation
const gracefulShutdownInstance: GracefulShutdown = {
  init: (config?: ShutdownConfig) => {
    // Reset state for reinitialization (useful for tests)
    isShuttingDownFlag = false;
    inFlightOperations.clear();
    if (shutdownTimeoutTimer) {
      clearTimeout(shutdownTimeoutTimer);
      shutdownTimeoutTimer = null;
    }

    if (config?.shutdownTimeoutSeconds !== undefined) {
      shutdownTimeoutMs = config.shutdownTimeoutSeconds * 1000;
    } else {
      shutdownTimeoutMs = 20 * 1000;
    }

    if (config?.logger) {
      logger = config.logger;
    } else {
      logger = console;
    }

    // Register signal handlers
    process.on('SIGINT', startShutdownSequence);
    process.on('SIGTERM', startShutdownSequence);
  },

  operationTracker,

  isShuttingDown: () => isShuttingDownFlag
};

// Exported singleton instance
export const gracefulShutdown: GracefulShutdown = gracefulShutdownInstance;
