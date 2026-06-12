package frauddetection

import io.opentelemetry.api.trace.Tracer
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.BeforeEach
import java.time.Duration
import kotlin.test.assertTimeoutPreemptively
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.slf4j.LoggerFactory
import frauddetection.FlagdClientConfig
import dev.openfeature.contrib.providers.flagd.FlagdProvider
import dev.openfeature.sdk.EvaluationContext

internal class FlagdCircuitBreakerIntegrationTest {
    private val logger = LoggerFactory.getLogger(javaClass)
    private val testFlagKey = "fraud.detection.enabled"
    private val defaultFallbackValue = false

    @BeforeEach
    fun setup() {
        // Reset all environment variables before each test
        listOf(
            "FLAGD_CONNECTION_TIMEOUT_MS",
            "FLAGD_REQUEST_TIMEOUT_MS",
            "FLAGD_RETRY_MAX_ATTEMPTS",
            "FLAGD_CIRCUIT_BREAKER_FAILURE_RATE_THRESHOLD",
            "FLAGD_CIRCUIT_BREAKER_SLIDING_WINDOW_SIZE",
            "FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE",
            "FLAGD_CIRCUIT_BREAKER_PERMITTED_CALLS_IN_HALF_OPEN_STATE"
        ).forEach { 
            System.clearProperty(it)
        }
    }

    @Test
    fun `test_ac1_circuit_opens_when_5_out_of_10_consecutive_calls_fail`() {
        // Use default threshold values: 50% failure rate over 10 requests
        val slidingWindowSize = 10
        val failureCount = 5
        var requestCount = 0

        // Mock provider that fails first 5 times, then succeeds
        val mockProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                requestCount++
                if (requestCount <= failureCount) {
                    throw RuntimeException("Simulated flagd failure")
                }
                return true
            }
        }

        // First 5 requests: fail, return fallback
        repeat(failureCount) { i ->
            val result = runCatching {
                mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            }
            assertTrue(result.isSuccess, "No exception should be thrown for failed request $i")
            assertEquals(defaultFallbackValue, result.getOrThrow(), "Fallback value should be returned for failed request $i")
        }

        // Next 5 requests: should return fallback immediately (circuit open) without hitting provider
        val requestCountAfterFailures = requestCount
        repeat(slidingWindowSize - failureCount) { i ->
            val result = mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(defaultFallbackValue, result, "Fallback value should be returned when circuit is open, request $i")
        }
        assertEquals(requestCountAfterFailures, requestCount, "No actual requests should be made when circuit is open")
    }

    @Test
    fun `test_ac2_circuit_transitions_to_half_open_after_30s_cooldown`() {
        System.setProperty("FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE", "1s") // Use short duration for test
        var requestCount = 0

        val mockProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                requestCount++
                throw RuntimeException("Simulated flagd failure")
            }
        }

        // Trigger circuit open by failing 5 out of 10 requests
        repeat(5) {
            mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }

        // Verify circuit is open: no requests go through
        val openStateRequestCount = requestCount
        repeat(5) {
            mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }
        assertEquals(openStateRequestCount, requestCount, "No requests should be made while circuit is open")

        // Wait for cooldown period
        Thread.sleep(1100)

        // Verify half-open state: allows 3 test requests
        repeat(3) { i ->
            mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }
        assertEquals(openStateRequestCount + 3, requestCount, "Should allow 3 test calls in half-open state")
    }

    @Test
    fun `test_ac3_circuit_closes_after_successful_test_calls_in_half_open`() {
        System.setProperty("FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE", "1s")
        var requestCount = 0
        var shouldFail = true

        val mockProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                requestCount++
                if (shouldFail) {
                    throw RuntimeException("Simulated flagd failure")
                }
                return true
            }
        }

        // Trigger circuit open
        repeat(5) {
            mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }

        // Wait for cooldown
        Thread.sleep(1100)
        shouldFail = false // Now Flagd is healthy

        // Make 3 test calls (should all succeed)
        repeat(3) {
            val result = mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(true, result, "Test call should succeed in half-open state")
        }

        // Verify circuit is closed: subsequent calls go through to provider
        val requestCountAfterHalfOpen = requestCount
        repeat(5) { i ->
            val result = mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(true, result, "Request $i should succeed after circuit closes")
        }
        assertEquals(requestCountAfterHalfOpen + 5, requestCount, "All requests should go through when circuit is closed")
    }

    @Test
    fun `test_ac4_circuit_reopens_if_half_open_test_call_fails`() {
        System.setProperty("FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE", "1s")
        var requestCount = 0
        var failHalfOpenCall = true

        val mockProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                requestCount++
                if (failHalfOpenCall) {
                    throw RuntimeException("Simulated flagd failure")
                }
                return true
            }
        }

        // Trigger circuit open
        repeat(5) {
            mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }

        // Wait for cooldown
        Thread.sleep(1100)

        // First test call fails
        val result = mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        assertEquals(defaultFallbackValue, result, "Fallback returned for failed half-open call")

        // Verify circuit is back to open: no more requests go through
        val requestCountAfterFailure = requestCount
        repeat(5) {
            mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }
        assertEquals(requestCountAfterFailure, requestCount, "No requests should go through after circuit re-opens from half-open failure")
    }

    @Test
    fun `test_ac5_circuit_breaker_config_overridden_by_environment_variables`() {
        // Set custom config values via environment variables
        System.setProperty("FLAGD_CIRCUIT_BREAKER_FAILURE_RATE_THRESHOLD", "75")
        System.setProperty("FLAGD_CIRCUIT_BREAKER_SLIDING_WINDOW_SIZE", "20")
        System.setProperty("FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE", "60s")
        System.setProperty("FLAGD_CIRCUIT_BREAKER_PERMITTED_CALLS_IN_HALF_OPEN_STATE", "5")

        val config = FlagdClientConfig.load()

        assertEquals(75, config.circuitBreakerFailureRateThreshold, "Custom failure rate threshold should be loaded")
        assertEquals(20, config.circuitBreakerSlidingWindowSize, "Custom sliding window size should be loaded")
        assertEquals(Duration.ofSeconds(60), config.circuitBreakerWaitDurationInOpenState, "Custom wait duration should be loaded")
        assertEquals(5, config.circuitBreakerPermittedCallsInHalfOpenState, "Custom permitted calls in half-open should be loaded")
    }

    @Test
    fun `test_ac6_open_circuit_returns_same_fallback_as_retry_exhaustion`() {
        var retryFallback: Boolean? = null
        var circuitOpenFallback: Boolean? = null

        // First get fallback from retry exhaustion
        val retryMockProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                throw RuntimeException("Simulated flagd failure")
            }
        }
        retryFallback = retryMockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())

        // Now trigger circuit open and get fallback
        repeat(5) {
            retryMockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }
        circuitOpenFallback = retryMockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())

        assertEquals(retryFallback, circuitOpenFallback, "Fallback values from retry exhaustion and circuit open should match")
        assertEquals(defaultFallbackValue, retryFallback, "Fallback should match preconfigured default")
    }

    @Test
    fun `test_ac7_connection_and_execution_exceptions_count_as_circuit_breaker_failures`() {
        // Test connection timeout exceptions count as failures
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "100")
        val connectionFailProvider = FlagdProvider.builder()
            .host("non-existent-host")
            .port(8013)
            .build()

        // First 5 connection failures should open circuit
        repeat(5) { i ->
            val result = connectionFailProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(defaultFallbackValue, result, "Connection failure $i should return fallback")
        }

        // Verify circuit is open from connection failures
        val connectionStartTime = System.currentTimeMillis()
        repeat(5) {
            connectionFailProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }
        val connectionDuration = System.currentTimeMillis() - connectionStartTime
        assertTrue(connectionDuration < 100, "Circuit should be open after connection failures, no actual requests made")

        // Test execution exceptions count as failures
        var requestCount = 0
        val executionFailProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                requestCount++
                throw RuntimeException("Simulated execution failure")
            }
        }

        // First 5 execution failures should open circuit
        repeat(5) { i ->
            val result = executionFailProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(defaultFallbackValue, result, "Execution failure $i should return fallback")
        }

        // Verify circuit is open from execution failures
        val requestCountAfterFailures = requestCount
        repeat(5) {
            executionFailProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }
        assertEquals(requestCountAfterFailures, requestCount, "Circuit should be open after execution failures, no actual requests made")
    }

    @Test
    fun `test_ac8_integration_test_circuit_opens_and_closes_with_flagd_state_changes`() {
        // Use short timeouts for test
        System.setProperty("FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE", "2s")
        System.setProperty("FLAGD_CONNECTION_TIMEOUT_MS", "100")
        var flagdHealthy = false
        var requestCount = 0

        val mockProvider = object : FlagdProvider() {
            override fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
                requestCount++
                if (!flagdHealthy) {
                    throw RuntimeException("Flagd is unhealthy")
                }
                return true
            }
        }

        // Phase 1: Flagd is down, circuit opens
        repeat(10) { i ->
            val result = mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(defaultFallbackValue, result, "Request $i should return fallback when Flagd is down")
        }
        val requestsDuringOutage = requestCount
        repeat(10) {
            mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
        }
        assertEquals(requestsDuringOutage, requestCount, "Circuit is open, no requests should go out during outage")

        // Phase 2: Wait for cooldown, Flagd recovers
        Thread.sleep(2100)
        flagdHealthy = true

        // Phase 3: Test calls succeed, circuit closes
        repeat(3) { i ->
            val result = mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(true, result, "Half-open test call $i should succeed after Flagd recovery")
        }

        // Phase 4: Circuit is closed, normal operation resumes
        val requestsAfterHalfOpen = requestCount
        repeat(10) { i ->
            val result = mockProvider.getBooleanValue(testFlagKey, defaultFallbackValue, EvaluationContext())
            assertEquals(true, result, "Request $i should succeed after circuit closes")
        }
        assertEquals(requestsAfterHalfOpen + 10, requestCount, "All requests should go through when circuit is closed and Flagd is healthy")
    }
}
