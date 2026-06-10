package io.opentelemetry.demo.frauddetection;

public interface ShutdownHandler {
    /**
     * Triggers graceful shutdown sequence.
     * @return true if shutdown completed gracefully, false if forced after timeout
     */
    boolean performGracefulShutdown();
}
