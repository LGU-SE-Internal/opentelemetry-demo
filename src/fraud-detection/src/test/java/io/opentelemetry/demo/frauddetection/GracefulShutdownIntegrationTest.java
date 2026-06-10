package io.opentelemetry.demo.frauddetection;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.ActiveProfiles;

import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
public class GracefulShutdownIntegrationTest {

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private ShutdownHandler shutdownHandler;

    private String getBaseUrl() {
        return "http://localhost:" + port;
    }

    @Test
    @DisplayName("AC-1: After SIGINT/SIGTERM signal, health endpoint returns 503 and new requests are rejected")
    public void test_ac1_shutdown_signal_returns_503_health_and_rejects_new_requests() {
        // Precondition: Health check returns 200 before shutdown
        ResponseEntity<Map> healthResponse = restTemplate.getForEntity(getBaseUrl() + "/health", Map.class);
        assertEquals(HttpStatus.OK, healthResponse.getStatusCode());

        // Trigger shutdown sequence
        CompletableFuture<Boolean> shutdownFuture = CompletableFuture.supplyAsync(() ->
                shutdownHandler.performGracefulShutdown()
        );

        // Verify health endpoint returns 503 immediately
        ResponseEntity<Map> postShutdownHealth = restTemplate.getForEntity(getBaseUrl() + "/health", Map.class);
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, postShutdownHealth.getStatusCode());
        assertEquals("shutdown_in_progress", postShutdownHealth.getBody().get("status"));

        // Verify new fraud check requests are rejected (HTTP 503 or connection refused)
        ResponseEntity<Map> fraudCheckResponse = restTemplate.postForEntity(
                getBaseUrl() + "/fraud/check",
                Map.of("userId", "test123", "amount", 100.0),
                Map.class
        );
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, fraudCheckResponse.getStatusCode());
    }

    @Test
    @DisplayName("AC-2: Service waits for in-flight requests to complete up to 30s before termination")
    public void test_ac2_waits_for_in_flight_requests_before_termination() throws Exception {
        int slowRequestDurationMs = 2000;
        AtomicInteger requestCompleted = new AtomicInteger(0);

        // Start slow in-flight request that takes 2s to complete
        CompletableFuture<Void> requestFuture = CompletableFuture.runAsync(() -> {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    getBaseUrl() + "/fraud/check?slow=" + slowRequestDurationMs,
                    Map.of("userId", "slow123", "amount", 200.0),
                    Map.class
            );
            if (response.getStatusCode().is2xxSuccessful()) {
                requestCompleted.incrementAndGet();
            }
        });

        // Wait 500ms for request to start processing
        Thread.sleep(500);

        // Trigger shutdown
        long shutdownStartTime = System.currentTimeMillis();
        boolean shutdownResult = shutdownHandler.performGracefulShutdown();
        long shutdownDurationMs = System.currentTimeMillis() - shutdownStartTime;

        // Verify request completed successfully
        requestFuture.get(5, TimeUnit.SECONDS);
        assertEquals(1, requestCompleted.get());

        // Verify shutdown waited at least the slow request duration (2s)
        assertTrue(shutdownDurationMs >= slowRequestDurationMs,
                "Shutdown finished too fast, did not wait for in-flight request");
        assertTrue(shutdownResult, "Shutdown should complete gracefully");
    }

    @Test
    @DisplayName("AC-3: Service terminates immediately after all in-flight requests complete, no extra wait")
    public void test_ac3_terminates_immediately_after_in_flight_completion() throws Exception {
        int fastRequestDurationMs = 500;
        AtomicInteger requestCompleted = new AtomicInteger(0);

        // Start fast in-flight request
        CompletableFuture<Void> requestFuture = CompletableFuture.runAsync(() -> {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    getBaseUrl() + "/fraud/check?slow=" + fastRequestDurationMs,
                    Map.of("userId", "fast123", "amount", 50.0),
                    Map.class
            );
            if (response.getStatusCode().is2xxSuccessful()) {
                requestCompleted.incrementAndGet();
            }
        });

        // Wait for request to start
        Thread.sleep(100);

        // Trigger shutdown
        long shutdownStartTime = System.currentTimeMillis();
        boolean shutdownResult = shutdownHandler.performGracefulShutdown();
        long shutdownDurationMs = System.currentTimeMillis() - shutdownStartTime;

        // Verify request completed
        requestFuture.get(2, TimeUnit.SECONDS);
        assertEquals(1, requestCompleted.get());

        // Verify shutdown duration is only slightly longer than request duration (not full 30s)
        assertTrue(shutdownDurationMs < 5000, "Shutdown took too long, waited unnecessary time after request completion");
        assertTrue(shutdownResult, "Shutdown should complete gracefully");
    }

    @Test
    @DisplayName("AC-4: Service forcibly terminates in-flight requests after 30s timeout and logs interrupted requests")
    public void test_ac4_forces_shutdown_after_30s_timeout() throws Exception {
        int verySlowRequestDurationMs = 35000; // Longer than 30s timeout
        AtomicInteger requestCompleted = new AtomicInteger(0);

        // Start request that takes 35s
        CompletableFuture<Void> requestFuture = CompletableFuture.runAsync(() -> {
            try {
                restTemplate.postForEntity(
                        getBaseUrl() + "/fraud/check?slow=" + verySlowRequestDurationMs,
                        Map.of("userId", "timeout123", "amount", 1000.0),
                        Map.class
                );
                requestCompleted.incrementAndGet();
            } catch (Exception e) {
                // Expected, request will be interrupted
            }
        });

        // Wait for request to start
        Thread.sleep(500);

        // Trigger shutdown
        long shutdownStartTime = System.currentTimeMillis();
        boolean shutdownResult = shutdownHandler.performGracefulShutdown();
        long shutdownDurationMs = System.currentTimeMillis() - shutdownStartTime;

        // Verify request was NOT completed (interrupted)
        assertFalse(requestFuture.isCompletedExceptionally() == false && requestCompleted.get() == 1,
                "Slow request should have been interrupted after timeout");

        // Verify shutdown took approximately 30s (within tolerance)
        assertTrue(shutdownDurationMs >= 29000 && shutdownDurationMs <= 31000,
                "Shutdown did not wait for 30s timeout, duration: " + shutdownDurationMs + "ms");
        assertFalse(shutdownResult, "Shutdown should return false when forced after timeout");

        // TODO: Verify logs contain ERROR entries for interrupted request ID, client IP, timestamp
    }

    @Test
    @DisplayName("AC-5: All connections are closed during shutdown regardless of outcome")
    public void test_ac5_closes_all_connections_before_exit() throws Exception {
        // Precondition: Verify we can get a valid DB connection before shutdown
        // TODO: Add JDBC connection check precondition

        // Trigger graceful shutdown (no in-flight requests)
        boolean shutdownResult = shutdownHandler.performGracefulShutdown();
        assertTrue(shutdownResult);

        // Verify DB connections are closed
        // TODO: Assert that new DB connection attempts fail after shutdown

        // Verify gRPC client connections are closed
        // TODO: Assert that new gRPC calls fail after shutdown

        // Verify listening server socket is closed
        assertThrows(Exception.class, () -> {
            restTemplate.getForEntity(getBaseUrl() + "/health", Map.class);
        }, "Server socket should be closed after shutdown");
    }

    @Test
    @DisplayName("AC-6: All required shutdown events are logged at INFO level")
    public void test_ac6_logs_shutdown_events() throws Exception {
        // Start in-flight request to trigger drain logs
        CompletableFuture<Void> requestFuture = CompletableFuture.runAsync(() -> {
            restTemplate.postForEntity(
                    getBaseUrl() + "/fraud/check?slow=12000",
                    Map.of("userId", "logtest123", "amount", 300.0),
                    Map.class
            );
        });

        Thread.sleep(500);

        // Trigger shutdown
        shutdownHandler.performGracefulShutdown();
        requestFuture.get(15, TimeUnit.SECONDS);

        // TODO: Verify logs contain all required INFO entries:
        // 1. "Shutdown signal received: [signal type], initiating graceful shutdown"
        // 2. "Draining in-flight requests, count: [count]" (at least twice, since 12s > 5s interval)
        // 3. "All in-flight requests completed, starting resource cleanup"
        // 4. "Graceful shutdown completed successfully"
        // 5. "Forcing shutdown after 30s timeout, remaining in-flight requests: [count]" (tested in AC4)
    }
}
