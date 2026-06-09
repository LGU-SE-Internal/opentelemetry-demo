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

// Exported singleton instance
export const gracefulShutdown: GracefulShutdown = {
  init: () => { throw new Error('Not implemented'); },
  operationTracker: {
    registerOperation: () => { throw new Error('Not implemented'); },
    getInFlightOperations: () => { throw new Error('Not implemented'); }
  },
  isShuttingDown: () => { throw new Error('Not implemented'); }
};
