import next from 'next';
import type { NextServer } from 'next/dist/server/next';
import { createServer } from 'http';
import { parse } from 'url';

const port = parseInt(process.env.PORT || '3000', 10);
const dev = process.env.NODE_ENV !== 'production';
const app = next({ dev });
const handle = app.getRequestHandler();

export interface ShutdownConfig {
  timeoutMs: number;
  logger: {
    info: (data: Record<string, unknown>, message: string) => void;
    warn: (data: Record<string, unknown>, message: string) => void;
    error: (data: Record<string, unknown>, message: string) => void;
  };
}

type ShutdownEvent = {
  event_type: "shutdown";
  event_subtype: "signal_received" | "shutdown_started" | "new_connections_blocked" | "upstream_connections_closed" | "shutdown_complete" | "timeout_triggered";
  timestamp: string;
  details: Record<string, unknown>;
}

function createShutdownEvent(subtype: ShutdownEvent['event_subtype'], details: Record<string, unknown> = {}): ShutdownEvent {
  return {
    event_type: "shutdown",
    event_subtype: subtype,
    timestamp: new Date().toISOString(),
    details
  };
}

export function setupShutdownHandlers(server: NextServer, config: ShutdownConfig): void {
  let isShuttingDown = false;
  let activeRequests = 0;
  const startTime = Date.now();

  const upstreamClients = [
    (global as any).productCatalogClient,
    (global as any).cartClient,
    (global as any).checkoutClient
  ].filter(Boolean);

  const shutdown = async (signal: string) => {
    if (isShuttingDown) return;
    isShuttingDown = true;

    config.logger.info(createShutdownEvent("signal_received", { signal }), `Received ${signal}, starting graceful shutdown`);

    // Stop accepting new connections
    config.logger.info(createShutdownEvent("new_connections_blocked"), "Stopping accepting new connections");
    server.server?.close();

    // Set up hard timeout
    const timeout = setTimeout(() => {
      config.logger.warn(
        createShutdownEvent("timeout_triggered", {
          timeoutMs: config.timeoutMs,
          incompleteRequests: activeRequests
        }),
        `Shutdown timeout reached after ${config.timeoutMs}ms, force exiting with ${activeRequests} incomplete requests`
      );
      process.exit(1);
    }, config.timeoutMs);

    // Wait for all active requests to complete
    while (activeRequests > 0) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }

    // Close upstream connections
    let closedConnections = 0;
    for (const client of upstreamClients) {
      if (typeof client.close === 'function') {
        client.close();
        closedConnections++;
      }
    }
    config.logger.info(
      createShutdownEvent("upstream_connections_closed", { closedConnections }),
      `Closed ${closedConnections} upstream service connections`
    );

    // Clear timeout
    clearTimeout(timeout);

    const durationMs = Date.now() - startTime;
    config.logger.info(
      createShutdownEvent("shutdown_complete", {
        durationMs,
        completedRequests: 0
      }),
      `Shutdown complete after ${durationMs}ms`
    );

    process.exit(0);
  };

  // Track active requests
  server.server?.on('request', (req, res) => {
    if (!isShuttingDown) {
      activeRequests++;
      res.on('finish', () => {
        activeRequests--;
      });
      res.on('close', () => {
        activeRequests--;
      });
    }
  });

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

app.prepare().then(() => {
  const httpServer = createServer((req, res) => {
    const parsedUrl = parse(req.url!, true);
    handle(req, res, parsedUrl);
  }).listen(port);

  console.log(`> Server listening at http://localhost:${port} as ${dev ? 'development' : process.env.NODE_ENV}`);

  // Set up shutdown handlers
  const logger = {
    info: (data: any, msg: string) => console.log(JSON.stringify({ ...data, message: msg })),
    warn: (data: any, msg: string) => console.warn(JSON.stringify({ ...data, message: msg })),
    error: (data: any, msg: string) => console.error(JSON.stringify({ ...data, message: msg }))
  };

  const timeoutMs = parseInt(process.env.FRONTEND_SHUTDOWN_TIMEOUT_MS || '30000', 10);
  setupShutdownHandlers(app as unknown as NextServer, { timeoutMs, logger });
});
