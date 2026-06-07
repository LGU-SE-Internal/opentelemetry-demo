import type http from 'http';
import type { TracerProvider } from '@opentelemetry/sdk-trace-base';
import type { MeterProvider } from '@opentelemetry/sdk-metrics';
import type { LoggerProvider } from '@opentelemetry/sdk-logs';

export type ShutdownConfig = {
  /** Shutdown timeout in seconds, defaults to 30 */
  timeout?: number;
  /** Reference to the running Node.js HTTP server instance */
  httpServer: import('http').Server;
  /** Array of cleanup functions to run on shutdown (close connections, etc.) */
  cleanupTasks: Array<() => Promise<void>>;
  /** OpenTelemetry SDK provider instances to flush */
  otelProviders: {
    traceProvider?: import('@opentelemetry/sdk-trace-base').TracerProvider;
    meterProvider?: import('@opentelemetry/sdk-metrics').MeterProvider;
    loggerProvider?: import('@opentelemetry/sdk-logs').LoggerProvider;
  };
}

let isShuttingDown = false;

export function initializeGracefulShutdown(config: ShutdownConfig): void {
  const timeoutSeconds = process.env.FRONTEND_SHUTDOWN_TIMEOUT 
    ? parseInt(process.env.FRONTEND_SHUTDOWN_TIMEOUT, 10)
    : (config.timeout ?? 30);

  const shutdown = async (signal: string) => {
    if (isShuttingDown) return;
    isShuttingDown = true;

    console.log(`Received ${signal}, starting graceful shutdown...`);

    // Step 1: Close HTTP server to stop accepting new connections
    const serverClosePromise = new Promise<void>((resolve, reject) => {
      config.httpServer.close((err) => {
        if (err) {
          console.error('Error closing HTTP server:', err);
          reject(err);
        } else {
          console.log('HTTP server closed, no new connections accepted');
          resolve();
        }
      });
    });

    // Step 2: Set up timeout force exit
    const timeoutId = setTimeout(async () => {
      console.error(`Shutdown timed out after ${timeoutSeconds} seconds, forcing exit`);
      await runCleanupAndFlush();
      process.exit(1);
    }, timeoutSeconds * 1000);

    try {
      // Wait for server to close (all in-flight requests complete)
      await serverClosePromise;
      clearTimeout(timeoutId);
      console.log('All in-flight requests completed');

      // Run cleanup and flush
      await runCleanupAndFlush();
      console.log('Graceful shutdown completed successfully');
      process.exit(0);
    } catch (err) {
      clearTimeout(timeoutId);
      console.error('Error during graceful shutdown:', err);
      await runCleanupAndFlush();
      process.exit(1);
    }
  };

  const runCleanupAndFlush = async (): Promise<void> => {
    // Flush all OTel providers
    const flushPromises: Promise<void>[] = [];
    if (config.otelProviders.traceProvider) {
      flushPromises.push(config.otelProviders.traceProvider.forceFlush());
    }
    if (config.otelProviders.meterProvider) {
      flushPromises.push(config.otelProviders.meterProvider.forceFlush());
    }
    if (config.otelProviders.loggerProvider) {
      flushPromises.push(config.otelProviders.loggerProvider.forceFlush());
    }

    try {
      await Promise.all(flushPromises);
      console.log('All OpenTelemetry data flushed successfully');
    } catch (err) {
      console.error('Error flushing OpenTelemetry data:', err);
    }

    // Run all cleanup tasks
    for (const task of config.cleanupTasks) {
      try {
        await task();
        console.log('Cleanup task completed');
      } catch (err) {
        console.error('Error running cleanup task:', err);
      }
    }
  };

  // Register signal handlers
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}
