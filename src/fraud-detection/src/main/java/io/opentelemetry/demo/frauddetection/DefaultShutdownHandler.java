package io.opentelemetry.demo.frauddetection;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import org.springframework.boot.web.server.WebServer;

import jakarta.annotation.PreDestroy;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.TimeUnit;

public class DefaultShutdownHandler implements ShutdownHandler {
    private static final Logger logger = LoggerFactory.getLogger(DefaultShutdownHandler.class);
    private static final int GRACE_PERIOD_SECONDS = 30;
    private static final int DRAIN_LOG_INTERVAL_SECONDS = 5;

    private final AtomicBoolean shuttingDown = new AtomicBoolean(false);
    private final AtomicInteger activeRequests = new AtomicInteger(0);

    @Autowired
    private ApplicationContext applicationContext;

    @Override
    public boolean performGracefulShutdown() {
        if (!shuttingDown.compareAndSet(false, true)) {
            return false; // Already shutting down
        }

        try {
            // Step 1: Stop accepting new requests by pausing the web server
            stopWebServer();

            // Step 2: Drain in-flight requests
            long startTime = System.currentTimeMillis();
            long endTime = startTime + TimeUnit.SECONDS.toMillis(GRACE_PERIOD_SECONDS);

            while (System.currentTimeMillis() < endTime && activeRequests.get() > 0) {
                try {
                    long remaining = endTime - System.currentTimeMillis();
                    long logInterval = TimeUnit.SECONDS.toMillis(DRAIN_LOG_INTERVAL_SECONDS);
                    long sleepTime = Math.min(remaining, logInterval);

                    Thread.sleep(sleepTime);

                    if (activeRequests.get() > 0 && System.currentTimeMillis() < endTime) {
                        logger.info("Draining in-flight requests, count: {}", activeRequests.get());
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }

            // Check if we completed gracefully
            boolean graceful = activeRequests.get() == 0;
            if (graceful) {
                logger.info("All in-flight requests completed, starting resource cleanup");
            } else {
                logger.error("Forcing shutdown after {}s timeout, remaining in-flight requests: {}",
                        GRACE_PERIOD_SECONDS, activeRequests.get());
                // Log interrupted requests details would go here
            }

            // Step 3: Clean up resources (DB connections, gRPC clients, etc.)
            cleanupResources();

            if (graceful) {
                logger.info("Graceful shutdown completed successfully");
            }

            return graceful;
        } catch (Exception e) {
            logger.error("Error during shutdown sequence", e);
            return false;
        }
    }

    private void stopWebServer() {
        if (applicationContext instanceof ServletWebServerApplicationContext servletContext) {
            WebServer webServer = servletContext.getWebServer();
            webServer.stop();
        }
    }

    private void cleanupResources() {
        // Close JDBC/JPA connections
        // Close gRPC client connections
        // Any other resource cleanup
    }

    public boolean isShuttingDown() {
        return shuttingDown.get();
    }

    public void incrementActiveRequests() {
        if (!shuttingDown.get()) {
            activeRequests.incrementAndGet();
        }
    }

    public void decrementActiveRequests() {
        activeRequests.decrementAndGet();
    }
}
