package frauddetection

import org.awaitility.kotlin.await
import org.awaitility.kotlin.atMost
import org.awaitility.kotlin.untilAsserted
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import org.slf4j.LoggerFactory
import java.io.IOException
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.test.assertContains
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class GracefulShutdownACTests {
    private val logger = LoggerFactory.getLogger(GracefulShutdownACTests::class.java)
    private val httpClient = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(5))
        .build()
    private val serviceBaseUrl = "http://localhost:8080" // Adjust port as per fraud detection service config
    private val fraudCheckEndpoint = "$serviceBaseUrl/fraud/check"

    // Mock/test implementations of spec interfaces for test validation
    private class TestRequestCounter : RequestCounter {
        private val count = java.util.concurrent.atomic.AtomicInteger(0)
        override fun increment() = count.incrementAndGet()
        override fun decrement() = count.decrementAndGet()
        override fun getActiveCount(): Int = count.get()
    }

    private class TestConnectionCleaner : ConnectionCleaner {
        var connectionsClosed = false
        override fun closeAllConnections() {
            connectionsClosed = true
        }
    }

    private lateinit var requestCounter: TestRequestCounter
    private lateinit var connectionCleaner: TestConnectionCleaner
    private lateinit var shutdownManager: ShutdownManager

    @BeforeEach
    fun setup() {
        requestCounter = TestRequestCounter()
        connectionCleaner = TestConnectionCleaner()
        shutdownManager = ShutdownManager(requestCounter, connectionCleaner, 30)
        // TODO: Start the fraud detection service process programmatically for integration tests
    }

    @AfterEach
    fun teardown() {
        // TODO: Clean up service process if still running
    }

    @Test
    fun test_ac1_log_emitted_on_sigterm_sigint() {
        // Arrange
        // Start service process, capture stdout/stderr logs

        // Act: Send SIGTERM to service process
        val serviceProcess = startServiceProcess()
        serviceProcess.destroy() // SIGTERM on Unix

        // Assert: Log entry exists
        await.atMost(Duration.ofSeconds(5)) untilAsserted {
            val logs = getServiceLogs(serviceProcess)
            assertContains(logs, "Graceful shutdown initiated, waiting for in-flight requests to complete")
            assertContains(logs, "INFO")
        }
    }

    @Test
    fun test_ac2_new_requests_return_503_after_shutdown_signal() {
        // Arrange
        val serviceProcess = startServiceProcess()
        // Verify service is healthy first
        val healthyResponse = sendFraudCheckRequest()
        assertEquals(200, healthyResponse.statusCode())

        // Act: Initiate shutdown
        serviceProcess.destroy()
        await.atMost(Duration.ofSeconds(2)) untilAsserted {
            assertTrue(shutdownManager.isShutdownInProgress())
        }

        // Assert: New request returns 503 with correct Retry-After header
        val shutdownResponse = sendFraudCheckRequest()
        assertEquals(503, shutdownResponse.statusCode())
        assertEquals("30", shutdownResponse.headers().firstValue("Retry-After").orElseThrow())
    }

    @Test
    fun test_ac3_service_waits_up_to_30s_for_in_flight_requests() {
        // Arrange
        val serviceProcess = startServiceProcess()
        // Start a long-running fraud check request that takes 10s to complete
        val longRequestLatch = CountDownLatch(1)
        var requestCompleted = false
        Thread {
            sendFraudCheckRequest(delayMillis = 10000)
            requestCompleted = true
            longRequestLatch.countDown()
        }.start()
        // Wait until request is in flight
        await.atMost(Duration.ofSeconds(2)) untilAsserted {
            assertEquals(1, requestCounter.getActiveCount())
        }

        // Act: Initiate shutdown
        val shutdownStartTime = System.currentTimeMillis()
        serviceProcess.destroy()

        // Assert: Process does not exit before request completes
        assertFalse(serviceProcess.waitFor(5, TimeUnit.SECONDS))
        assertTrue(longRequestLatch.await(15, TimeUnit.SECONDS))
        val shutdownDuration = System.currentTimeMillis() - shutdownStartTime
        assertTrue(shutdownDuration >= 10000, "Shutdown should wait for long running request")
        assertTrue(shutdownDuration < 30000, "Shutdown should complete before 30s timeout when requests finish")
    }

    @Test
    fun test_ac4_shutdown_success_before_timeout() {
        // Arrange
        val serviceProcess = startServiceProcess()
        // Start 2 requests that complete in 2s
        val requestLatch = CountDownLatch(2)
        repeat(2) {
            Thread {
                sendFraudCheckRequest(delayMillis = 2000)
                requestLatch.countDown()
            }.start()
        }
        await.atMost(Duration.ofSeconds(2)) untilAsserted {
            assertEquals(2, requestCounter.getActiveCount())
        }

        // Act: Initiate shutdown
        serviceProcess.destroy()
        val exitCode = serviceProcess.waitFor(10, TimeUnit.SECONDS)

        // Assert
        assertEquals(0, exitCode, "Service should exit with code 0 on successful shutdown")
        assertTrue(connectionCleaner.connectionsClosed, "All connections should be cleaned up")
        val logs = getServiceLogs(serviceProcess)
        assertContains(logs, "Shutdown complete, all in-flight requests processed")
        assertContains(logs, "2 succeeded")
        assertContains(logs, "0 failed")
        assertContains(logs, "All open connections cleaned up successfully")
    }

    @Test
    fun test_ac5_shutdown_timeout_after_30s() {
        // Arrange
        val serviceProcess = startServiceProcess()
        // Start request that takes 40s (longer than 30s timeout)
        Thread {
            sendFraudCheckRequest(delayMillis = 40000)
        }.start()
        await.atMost(Duration.ofSeconds(2)) untilAsserted {
            assertEquals(1, requestCounter.getActiveCount())
        }

        // Act: Initiate shutdown
        val shutdownStartTime = System.currentTimeMillis()
        serviceProcess.destroy()
        val exitCode = serviceProcess.waitFor(35, TimeUnit.SECONDS)
        val shutdownDuration = System.currentTimeMillis() - shutdownStartTime

        // Assert
        assertEquals(1, exitCode, "Service should exit with code 1 on timeout")
        assertTrue(shutdownDuration >= 30000 && shutdownDuration < 35000, "Shutdown should timeout after ~30s")
        assertTrue(connectionCleaner.connectionsClosed, "All connections should be cleaned up even on timeout")
        val logs = getServiceLogs(serviceProcess)
        assertContains(logs, "WARN")
        assertContains(logs, "Shutdown timed out after 30 seconds")
        assertContains(logs, "1 in-flight requests were terminated")
        assertContains(logs, "All open connections cleaned up successfully")
    }

    @Test
    fun test_ac6_active_requests_logged_every_5s_during_shutdown() {
        // Arrange
        val serviceProcess = startServiceProcess()
        // Start 3 requests that take 12s to complete
        repeat(3) {
            Thread {
                sendFraudCheckRequest(delayMillis = 12000)
            }.start()
        }
        await.atMost(Duration.ofSeconds(2)) untilAsserted {
            assertEquals(3, requestCounter.getActiveCount())
        }

        // Act: Initiate shutdown
        serviceProcess.destroy()
        serviceProcess.waitFor(15, TimeUnit.SECONDS)

        // Assert: Logs have active count entries approximately every 5s
        val logs = getServiceLogs(serviceProcess)
        val logLines = logs.lines().filter { it.contains("Active in-flight requests during shutdown:") }
        assertTrue(logLines.size >= 2, "Should log active count at least twice during 12s wait")
        logLines.forEach { line ->
            assertContains(line, "INFO")
            assertContains(line, "Active in-flight requests during shutdown:")
        }
    }

    @Test
    fun test_ac7_no_active_requests_immediate_shutdown() {
        // Arrange
        val serviceProcess = startServiceProcess()
        // Verify no active requests
        assertEquals(0, requestCounter.getActiveCount())

        // Act: Initiate shutdown
        val shutdownStartTime = System.currentTimeMillis()
        serviceProcess.destroy()
        val exitCode = serviceProcess.waitFor(5, TimeUnit.SECONDS)
        val shutdownDuration = System.currentTimeMillis() - shutdownStartTime

        // Assert
        assertEquals(0, exitCode, "Service should exit with code 0")
        assertTrue(shutdownDuration < 2000, "Shutdown should complete immediately when no active requests")
        assertTrue(connectionCleaner.connectionsClosed, "All connections should be cleaned up")
        val logs = getServiceLogs(serviceProcess)
        assertContains(logs, "Shutdown complete, all in-flight requests processed")
        assertContains(logs, "All open connections cleaned up successfully")
    }

    @Test
    fun test_ac8_all_connections_closed_regardless_of_shutdown_outcome() {
        // Case 1: Normal successful shutdown
        val successProcess = startServiceProcess()
        successProcess.destroy()
        successProcess.waitFor(10, TimeUnit.SECONDS)
        assertTrue(connectionCleaner.connectionsClosed, "Connections closed on successful shutdown")

        // Reset for timeout case
        connectionCleaner.connectionsClosed = false

        // Case 2: Timeout shutdown
        val timeoutProcess = startServiceProcess()
        Thread {
            sendFraudCheckRequest(delayMillis = 40000)
        }.start()
        await.atMost(Duration.ofSeconds(2)) untilAsserted {
            assertEquals(1, requestCounter.getActiveCount())
        }
        timeoutProcess.destroy()
        timeoutProcess.waitFor(35, TimeUnit.SECONDS)
        assertTrue(connectionCleaner.connectionsClosed, "Connections closed on timeout shutdown")
    }

    // Helper methods (stubs, to be implemented for actual test runs)
    private fun startServiceProcess(): Process {
        // TODO: Implement actual service startup for integration tests
        return ProcessBuilder().start()
    }

    private fun getServiceLogs(process: Process): String {
        // TODO: Implement actual log capture from service process
        return ""
    }

    private fun sendFraudCheckRequest(delayMillis: Long = 0): HttpResponse<String> {
        val requestBody = """
            {
                "userId": "test-user-123",
                "orderId": "test-order-456",
                "amount": 100.0,
                "delayMillis": $delayMillis
            }
        """.trimIndent()

        val request = HttpRequest.newBuilder()
            .uri(URI.create(fraudCheckEndpoint))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(requestBody))
            .timeout(Duration.ofSeconds(45))
            .build()

        return try {
            httpClient.send(request, HttpResponse.BodyHandlers.ofString())
        } catch (e: Exception) {
            logger.error("Failed to send fraud check request", e)
            throw e
        }
    }

    // Exact interfaces from spec to ensure type compliance
    interface RequestCounter {
        fun increment(): Unit
        fun decrement(): Unit
        fun getActiveCount(): Int
    }

    interface ConnectionCleaner {
        fun closeAllConnections(): Unit
    }

    class ShutdownManager(
        private val requestCounter: RequestCounter,
        private val connectionCleaner: ConnectionCleaner,
        private val shutdownTimeoutSeconds: Int = 30
    ) {
        fun registerSignalHandlers(): Unit = throw UnsupportedOperationException("Not implemented yet")
        fun initiateGracefulShutdown(): Unit = throw UnsupportedOperationException("Not implemented yet")
        fun isShutdownInProgress(): Boolean = throw UnsupportedOperationException("Not implemented yet")
    }
}
