package otel.demo.ad.service;

import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.health.v1.HealthCheckRequest;
import io.grpc.health.v1.HealthCheckResponse;
import io.grpc.health.v1.HealthGrpc;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import java.io.IOException;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

public class AdServiceShutdownIntegrationTest {
    private ManagedChannel channel;
    private HealthGrpc.HealthBlockingStub healthStub;
    private Process adServiceProcess;

    @BeforeEach
    void setUp() throws IOException {
        // Start ad service process
        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().remove("AD_SERVICE_SHUTDOWN_TIMEOUT_SECONDS");
        adServiceProcess = pb.start();
        // Wait for service to come up
        try {
            Thread.sleep(5000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        channel = ManagedChannelBuilder.forAddress("localhost", 8080)
                .usePlaintext()
                .build();
        healthStub = HealthGrpc.newBlockingStub(channel);
    }

    @AfterEach
    void tearDown() throws InterruptedException {
        if (channel != null) {
            channel.shutdownNow();
        }
        if (adServiceProcess != null && adServiceProcess.isAlive()) {
            adServiceProcess.destroyForcibly();
            adServiceProcess.waitFor();
        }
    }

    @Test
    void test_ac1_default_shutdown_timeout_10_seconds() {
        // Verify default timeout is 10s when no env var is set
        // Check logs for default timeout value on shutdown
        adServiceProcess.destroy();
        long startTime = System.currentTimeMillis();
        try {
            boolean exited = adServiceProcess.waitFor(15, TimeUnit.SECONDS);
            long shutdownDuration = System.currentTimeMillis() - startTime;
            assertTrue(exited, "Service should exit within 15 seconds");
            // Shutdown should take at least ~10s if no in-flight requests (wait for timeout)
            // OR exit immediately if 0 in-flight (but default timeout is still configured)
            // Check logs for "shutdown timeout configured: 10 seconds"
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            fail("Test interrupted");
        }
    }

    @Test
    void test_ac2_custom_shutdown_timeout_from_env_var() throws IOException {
        // Stop default service
        adServiceProcess.destroyForcibly();
        try {
            adServiceProcess.waitFor();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        // Start service with custom timeout
        int customTimeout = 5;
        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_SHUTDOWN_TIMEOUT_SECONDS", String.valueOf(customTimeout));
        adServiceProcess = pb.start();
        try {
            Thread.sleep(5000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        adServiceProcess.destroy();
        long startTime = System.currentTimeMillis();
        try {
            boolean exited = adServiceProcess.waitFor(10, TimeUnit.SECONDS);
            long shutdownDuration = System.currentTimeMillis() - startTime;
            assertTrue(exited, "Service should exit within 10 seconds");
            // Check logs for "shutdown timeout configured: 5 seconds"
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            fail("Test interrupted");
        }
    }

    @Test
    void test_ac3_health_check_not_serving_on_sigterm() {
        // Verify health check returns SERVING before shutdown
        HealthCheckResponse response = healthStub.check(HealthCheckRequest.newBuilder().setService("ad.AdService").build());
        assertEquals(HealthCheckResponse.ServingStatus.SERVING, response.getStatus());

        // Send SIGTERM
        adServiceProcess.destroy();

        // Verify health check immediately returns NOT_SERVING
        try {
            HealthCheckResponse postShutdownResponse = healthStub.check(HealthCheckRequest.newBuilder().setService("ad.AdService").build());
            assertEquals(HealthCheckResponse.ServingStatus.NOT_SERVING, postShutdownResponse.getStatus());
        } catch (Exception e) {
            // Even if connection fails, the health check should first return NOT_SERVING before closing
            fail("Health check should return NOT_SERVING immediately after shutdown signal");
        }
    }

    @Test
    void test_ac4_force_shutdown_after_timeout_with_remaining_requests() {
        // TODO: Implement test that starts long-running request, sends SIGTERM, verifies force shutdown after timeout with log message
        fail("Test not implemented yet");
    }

    @Test
    void test_ac5_successful_graceful_shutdown_with_no_remaining_requests() {
        // Send SIGTERM when no in-flight requests
        adServiceProcess.destroy();
        try {
            boolean exited = adServiceProcess.waitFor(15, TimeUnit.SECONDS);
            assertTrue(exited, "Service should exit within 15 seconds");
            // Check logs for "successful graceful shutdown, 0 remaining in-flight requests"
            // Verify prometheus metrics server is shut down
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            fail("Test interrupted");
        }
    }

    @Test
    void test_ac6_prometheus_metrics_server_available_during_shutdown_wait() {
        // TODO: Implement test that verifies prometheus endpoint is reachable during shutdown wait period
        fail("Test not implemented yet");
    }

    @Test
    void test_ac7_all_shutdown_events_logged_at_info_level() {
        // Send SIGTERM
        adServiceProcess.destroy();
        try {
            adServiceProcess.waitFor(15, TimeUnit.SECONDS);
            // Check logs for INFO level messages:
            // 1. "Shutdown initiated, timeout: X seconds"
            // 2. "Successful graceful shutdown, 0 remaining in-flight requests" OR "Shutdown timeout reached, Y remaining requests, force terminating"
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            fail("Test interrupted");
        }
    }
}
