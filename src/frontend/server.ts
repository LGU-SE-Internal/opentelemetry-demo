#!/usr/bin/env node
require('./Instrumentation.js');
import { startServerWithTls, loadTlsConfigFromEnv, TlsConfigError } from './utils/tls';
import { NextServer } from 'next';
import * as http from 'http';
import * as gateways from './gateways';

// Configuration type for graceful shutdown
export type ShutdownConfig = {
  gracePeriodMs: number; // Default: 30000 (30s), sourced from FRONTEND_SHUTDOWN_GRACE_PERIOD_MS env var
};

/**
 * Error types for shutdown process
 * - ShutdownTimeoutError: Thrown when grace period expires before all requests complete
 * - BackendConnectionCleanupError: Thrown when closing a downstream service connection fails
 */
export class ShutdownTimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ShutdownTimeoutError';
  }
}

export class BackendConnectionCleanupError extends Error {
  constructor(message: string, cause?: Error) {
    super(message);
    this.name = 'BackendConnectionCleanupError';
    this.cause = cause;
  }
}

export function getShutdownConfig(): ShutdownConfig {
  const gracePeriodMs = parseInt(process.env.FRONTEND_SHUTDOWN_GRACE_PERIOD_MS || '30000', 10);
  return { gracePeriodMs: isNaN(gracePeriodMs) ? 30000 : gracePeriodMs };
}

let isShuttingDown = false;

export function setupGracefulShutdown(server: NextServer, config: ShutdownConfig): void {
  const httpServer = server.server as http.Server;

  // Handle uncaught exceptions
  process.on('uncaughtException', (err: Error) => {
    console.error('Unhandled exception:', err);
    process.exit(1);
  });

  // Handle unhandled promise rejections
  process.on('unhandledRejection', (reason: Error) => {
    console.error('Unhandled promise rejection:', reason);
    process.exit(1);
  });

  // Add middleware to reject new requests when shutting down
  httpServer.on('request', (req, res) => {
    if (isShuttingDown) {
      res.writeHead(503, { 'Content-Type': 'text/plain', 'Connection': 'close' });
      res.end('Service Unavailable: Server is shutting down');
    }
  });

  async function performShutdown(signal: string) {
    if (isShuttingDown) return;
    isShuttingDown = true;

    console.info(`Shutdown signal received: ${signal}`);
    console.info(`Shutdown started with grace period of ${config.gracePeriodMs}ms`);

    // Stop accepting new connections
    httpServer.close((err) => {
      if (err) {
        console.error('Error closing HTTP server:', err);
      }
    });

    let shutdownTimeout: NodeJS.Timeout;

    const shutdownPromise = new Promise<void>((resolve, reject) => {
      // Wait for all connections to close
      const checkConnections = setInterval(() => {
        httpServer.getConnections((err, count) => {
          if (err) {
            clearInterval(checkConnections);
            return reject(err);
          }
          if (count === 0) {
            clearInterval(checkConnections);
            resolve();
          }
        });
      }, 100);

      // Timeout handler
      shutdownTimeout = setTimeout(() => {
        clearInterval(checkConnections);
        httpServer.closeAllConnections();
        reject(new ShutdownTimeoutError(`Grace period of ${config.gracePeriodMs}ms expired, force terminating remaining connections`));
      }, config.gracePeriodMs);
    });

    try {
      await shutdownPromise;
      clearTimeout(shutdownTimeout);
      console.info('All requests completed successfully');

      // Clean up backend connections
      try {
        await Promise.all([
          gateways.cartGateway.close(),
          gateways.productCatalogGateway.close(),
          gateways.checkoutGateway.close(),
          gateways.paymentGateway.close(),
          gateways.shippingGateway.close(),
        ]);
        console.info('Backend connections cleaned up');
        console.info('Shutdown completed successfully');
        process.exit(0);
      } catch (cleanupErr) {
        console.error('Backend connection cleanup failed:', cleanupErr);
        throw new BackendConnectionCleanupError('Failed to clean up backend connections', cleanupErr as Error);
      }
    } catch (shutdownErr) {
      console.error('Shutdown error:', shutdownErr);

      // Ensure we clean up backend connections even if shutdown failed
      try {
        await Promise.allSettled([
          gateways.cartGateway.close(),
          gateways.productCatalogGateway.close(),
          gateways.checkoutGateway.close(),
          gateways.paymentGateway.close(),
          gateways.shippingGateway.close(),
        ]);
      } catch {}

      if (shutdownErr instanceof ShutdownTimeoutError) {
        console.error('Shutdown timed out, force exiting');
      }

      process.exit(1);
    }
  }

  process.on('SIGINT', () => performShutdown('SIGINT'));
  process.on('SIGTERM', () => performShutdown('SIGTERM'));
}

const port = parseInt(process.env.FRONTEND_PORT || '8080', 10);
const tlsConfig = loadTlsConfigFromEnv();

async function main() {
  const result = await startServerWithTls(tlsConfig, port);
  
  if (!result.success) {
    console.error('Server startup failed:');
    if (typeof result.error === 'string') {
      console.error(result.error);
    } else if ('code' in result.error!) {
      console.error(`${result.error.code}: ${'path' in result.error ? result.error.path : result.error.message}`);
    }
    process.exit(1);
  }

  // Setup graceful shutdown
  const shutdownConfig = getShutdownConfig();
  setupGracefulShutdown(result.server, shutdownConfig);

  console.log(`> Frontend server running on ${tlsConfig.enabled ? 'https' : 'http'}://localhost:${port}`);
  console.log(`> TLS enabled: ${tlsConfig.enabled}`);
  if (tlsConfig.enabled) {
    console.log(`> mTLS enabled: ${tlsConfig.mtlsEnabled}`);
  }
}

main().catch(err => {
  console.error('Unexpected error during server startup:', err);
  process.exit(1);
});
