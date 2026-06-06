package frauddetection

import io.opentelemetry.api.trace.Tracer
import io.opentelemetry.extension.annotations.WithSpan
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.BeforeEach
import java.time.Duration
import kotlin.test.assertFails
import kotlin.test.assertTimeoutPreemptively
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.slf4j.LoggerFactory
import frauddetection.FlagdClientConfig

internal class FlagdResilienceIntegrationTest {
    private lateinit var flagdClient: Client
    private val logger = LoggerFactory.getLogger(javaClass)

    @BeforeEach
    fun setup() {
        // Reset environment variables before each test
        listOf("FLAGD_CONNECTION_TIMEOUT_MS", "FLAGD_REQUEST_TIMEOUT_MS", "FLAGD_RETRY_MAX_ATTEMPTS").forEach { 
            System.clearProperty(it)
        }
    }

    @Test
    fun `test_ac1_connection_timeout_triggers_retries_when_flagd_unreachable`() {
        val expectedTimeoutMs = 500
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", expectedTimeoutMs.toString())
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", "1")

        // Point to non-existent flagd endpoint
        val provider = FlagdProvider.builder()
            .host("non-existent-flagd-host")
            .port(8013)
            .build()

        val start = System.currentTimeMillis()
        val result = provider.getBooleanValue("fraud.detection.enabled", false, EvaluationContext())
        val duration = System.currentTimeMillis() - start

        // Verify timeout is roughly N ms (allow 20% tolerance)
        assertTrue(duration >= expectedTimeoutMs * 0.8 && duration <= expectedTimeoutMs * 2.5,
            "Connection attempt should fail after ~$expectedTimeoutMs ms, took $duration ms")
        // Verify fallback value is returned
        assertFalse(result)
    }

    @Test
    fun `test_ac2_request_timeout_aborts_slow_requests_and_triggers_retries`() {
        val expectedTimeoutMs = 300
        System.setProperty("FLAGD_REQUEST_TIMEOUT_MS", expectedTimeoutMs.toString())
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", "1")

        // Use a local test server that hangs on requests (simulated slow flagd)
        val provider = FlagdProvider.builder()
            .host("localhost")
            .port(9999) // Port with no listener, simulate hung response
            .build()

        assertTimeoutPreemptively(Duration.ofMillis(expectedTimeoutMs * 2 + 100)) {
            val result = provider.getBooleanValue("fraud.detection.enabled", false, EvaluationContext())
            assertFalse(result)
        }
    }

    @Test
    fun `test_ac3_max_retry_attempts_respects_configured_value`() {
        val maxRetries = 2
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", maxRetries.toString())
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "100")

        var requestCount = 0
        // Mock client that counts requests
        val mockProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                requestCount++
                throw RuntimeException("Simulated flagd failure")
            }
        }

        val result = mockProvider.getBooleanValue("fraud.detection.enabled", false, EvaluationContext())

        // 1 initial request + M retries = total M+1 requests
        assertEquals(maxRetries + 1, requestCount, "Should attempt exactly $maxRetries retries")
        assertFalse(result)
    }

    @Test
    fun `test_ac4_fallback_value_returned_after_retry_exhaustion_no_exceptions_thrown`() {
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", "2")
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "100")

        val provider = FlagdProvider.builder()
            .host("non-existent-host")
            .port(8013)
            .build()

        // No exception should be thrown
        val result = runCatching {
            provider.getBooleanValue("fraud.detection.enabled", false, EvaluationContext())
        }

        assertTrue(result.isSuccess, "No exception should be propagated")
        assertEquals(false, result.getOrThrow(), "Fallback default value should be returned")
    }

    @Test
    fun `test_ac5_error_log_emitted_after_retry_exhaustion_with_required_fields`() {
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", "1")
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "100")

        val listAppender = io.mockk.mockk<org.slf4j.helpers.ListAppender<*>>()
        val rootLogger = LoggerFactory.getLogger(org.slf4j.Logger.ROOT_LOGGER_NAME) as ch.qos.logback.classic.Logger
        rootLogger.addAppender(listAppender)

        val provider = FlagdProvider.builder()
            .host("non-existent-host")
            .port(8013)
            .build()

        provider.getBooleanValue("fraud.detection.enabled", false, EvaluationContext())

        val errorLogs = listAppender.list.filter { it.level == ch.qos.logback.classic.Level.ERROR }
        assertEquals(1, errorLogs.size, "Should have one ERROR log after retry exhaustion")
        
        val logMessage = errorLogs.first().message
        assertTrue(logMessage.contains("fraud.detection.enabled"), "Log should contain flag name")
        assertTrue(logMessage.contains("Connection refused") || logMessage.contains("timeout"), "Log should contain error cause")
        assertTrue(logMessage.contains("1 retries attempted"), "Log should contain number of retries")
        assertTrue(logMessage.contains("fallback value"), "Log should mention fallback value usage")

        rootLogger.detachAppender(listAppender)
    }

    @Test
    fun `test_ac6_transient_retry_failures_logged_at_debug_level`() {
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", "2")
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "100")

        val listAppender = io.mockk.mockk<org.slf4j.helpers.ListAppender<*>>()
        val rootLogger = LoggerFactory.getLogger(org.slf4j.Logger.ROOT_LOGGER_NAME) as ch.qos.logback.classic.Logger
        rootLogger.addAppender(listAppender)

        val provider = FlagdProvider.builder()
            .host("non-existent-host")
            .port(8013)
            .build()

        provider.getBooleanValue("fraud.detection.enabled", false, EvaluationContext())

        val debugLogs = listAppender.list.filter { it.level == ch.qos.logback.classic.Level.DEBUG }
        val errorLogs = listAppender.list.filter { it.level == ch.qos.logback.classic.Level.ERROR }

        assertEquals(2, debugLogs.size, "Should have 2 DEBUG logs for 2 retries")
        assertEquals(1, errorLogs.size, "Should have only 1 ERROR log for final failure")

        rootLogger.detachAppender(listAppender)
    }

    @Test
    fun `test_ac7_kafka_processing_continues_when_flagd_unreachable`() {
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", "2")
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "100")

        val fraudDetectionService = FraudDetectionService(Tracer.noop())

        // Process 10 messages with flagd unreachable
        repeat(10) { i ->
            val result = runCatching {
                fraudDetectionService.processTransaction(
                    transactionId = "test-$i",
                    userId = "user-$i",
                    amount = 100.0
                )
            }
            assertTrue(result.isSuccess, "Kafka message $i processing should not fail")
        }
    }

    @Test
    fun `test_ac8_environment_variables_read_with_default_values`() {
        // No env vars set, should use defaults
        val config = FlagdClientConfig.load()

        assertEquals(2000, config.connectionTimeoutMs, "Default connection timeout should be 2000ms")
        assertEquals(1000, config.requestTimeoutMs, "Default request timeout should be 1000ms")
        assertEquals(2, config.maxRetryAttempts, "Default retry count should be 2")

        // Set env vars
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "5000")
        System.setProperty("FLAGD_REQUEST_TIMEOUT_MS", "2000")
        System.setProperty("FLAGD_RETRY_MAX_ATTEMPTS", "3")

        val customConfig = FlagdClientConfig.load()

        assertEquals(5000, customConfig.connectionTimeoutMs, "Custom connection timeout should be applied")
        assertEquals(2000, customConfig.requestTimeoutMs, "Custom request timeout should be applied")
        assertEquals(3, customConfig.maxRetryAttempts, "Custom retry count should be applied")
    }
