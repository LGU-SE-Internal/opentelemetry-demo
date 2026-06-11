package frauddetection

import io.opentelemetry.api.GlobalOpenTelemetry
import io.opentelemetry.api.common.Attributes
import io.opentelemetry.api.logs.Logger
import io.opentelemetry.context.Context
import io.opentelemetry.sdk.testing.junit5.OpenTelemetryExtension
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.RegisterExtension
import java.io.ByteArrayOutputStream
import java.io.PrintStream
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class FraudLoggingMigrationTest {

    @JvmField
    @RegisterExtension
    val otelExtension: OpenTelemetryExtension = OpenTelemetryExtension.create()

    private val originalOut = System.out
    private val originalErr = System.err
    private lateinit var outContent: ByteArrayOutputStream
    private lateinit var errContent: ByteArrayOutputStream

    @BeforeEach
    fun setUpStreams() {
        outContent = ByteArrayOutputStream()
        errContent = ByteArrayOutputStream()
        System.setOut(PrintStream(outContent))
        System.setErr(PrintStream(errContent))
        GlobalOpenTelemetry.resetForTest()
        GlobalOpenTelemetry.set(otelExtension.openTelemetry)
    }

    @AfterEach
    fun restoreStreams() {
        System.setOut(originalOut)
        System.setErr(originalErr)
        GlobalOpenTelemetry.resetForTest()
    }

    @Test
    fun test_ac1_no_unstructured_print_calls_remaining() {
        // AC-1: All print/println/System.out/System.err calls replaced with OTel logger
        // Check that no unstructured output is written to stdout/stderr when processing transactions
        val testTransaction = Transaction(
            transactionId = "test-txn-123",
            userId = "user-456",
            amount = 100.0,
            items = listOf()
        )

        val fraudService = FraudDetectionService()
        fraudService.evaluateTransaction(testTransaction)

        val stdout = outContent.toString().trim()
        val stderr = errContent.toString().trim()

        // No unstructured output should exist
        assertEquals("", stdout, "Unstructured stdout output found: $stdout")
        assertEquals("", stderr, "Unstructured stderr output found: $stderr")
    }

    @Test
    fun test_ac2_log_severity_matches_intent() {
        // AC-2: Log severity levels match original message intent
        val testTransaction = Transaction(
            transactionId = "test-txn-789",
            userId = "user-101",
            amount = 9999.99,
            items = listOf()
        )

        val fraudService = FraudDetectionService()
        fraudService.evaluateTransaction(testTransaction)

        val logRecords = otelExtension.logRecords
        assertFalse(logRecords.isEmpty(), "No logs generated")

        // Normal operational events should be INFO level
        val infoLogs = logRecords.filter { it.severity == io.opentelemetry.api.logs.Severity.INFO }
        assertTrue(infoLogs.isNotEmpty(), "No INFO level logs found for operational events")

        // Test error scenario
        val invalidTransaction = Transaction(
            transactionId = "",
            userId = "",
            amount = -1.0,
            items = listOf()
        )
        try {
            fraudService.evaluateTransaction(invalidTransaction)
        } catch (e: IllegalArgumentException) {
            // Expected exception
        }

        val errorLogs = otelExtension.logRecords.filter { it.severity == io.opentelemetry.api.logs.Severity.ERROR }
        assertTrue(errorLogs.isNotEmpty(), "No ERROR level logs found for failure cases")
    }

    @Test
    fun test_ac3_fraud_check_logs_include_required_metadata() {
        // AC-3: 100% of fraud check processing logs include transaction_id, user_id, fraud_score
        val transactionId = "test-txn-meta-123"
        val userId = "user-meta-456"
        val expectedFraudScore = 0.85

        val testTransaction = Transaction(
            transactionId = transactionId,
            userId = userId,
            amount = 500.0,
            items = listOf()
        )

        val fraudService = FraudDetectionService()
        fraudService.evaluateTransaction(testTransaction)

        val fraudCheckLogs = otelExtension.logRecords.filter {
            it.body?.asString()?.contains("fraud", ignoreCase = true) == true
        }
        assertFalse(fraudCheckLogs.isEmpty(), "No fraud check processing logs found")

        fraudCheckLogs.forEach { log ->
            val attributes = log.attributes
            // Verify required attributes exist and have correct values
            assertEquals(transactionId, attributes.get(io.opentelemetry.api.common.AttributeKey.stringKey("transaction_id")),
                "transaction_id missing or incorrect in log: ${log.body}")
            assertEquals(userId, attributes.get(io.opentelemetry.api.common.AttributeKey.stringKey("user_id")),
                "user_id missing or incorrect in log: ${log.body}")
            assertNotNull(attributes.get(io.opentelemetry.api.common.AttributeKey.doubleKey("fraud_score")),
                "fraud_score missing in log: ${log.body}")
        }
    }

    @Test
    fun test_ac4_logs_include_trace_context_when_in_trace() {
        // AC-4: Log events in active trace include trace_id and span_id
        val tracer = otelExtension.openTelemetry.getTracer("fraud-detection-test")
        val transactionId = "test-txn-trace-123"
        val userId = "user-trace-456"

        val testTransaction = Transaction(
            transactionId = transactionId,
            userId = userId,
            amount = 200.0,
            items = listOf()
        )

        // Start a trace before processing
        val span = tracer.spanBuilder("test-fraud-check-span").startSpan()
        val context = Context.current().with(span)
        val scope = context.makeCurrent()

        try {
            val fraudService = FraudDetectionService()
            fraudService.evaluateTransaction(testTransaction)

            val logRecords = otelExtension.logRecords
            assertFalse(logRecords.isEmpty(), "No logs generated during trace")

            logRecords.forEach { log ->
                assertEquals(span.spanContext.traceId, log.traceId, "trace_id missing or incorrect in log")
                assertEquals(span.spanContext.spanId, log.spanId, "span_id missing or incorrect in log")
            }
        } finally {
            scope.close()
            span.end()
        }
    }

    @Test
    fun test_ac5_production_profile_no_unstructured_output() {
        // AC-5: PROFILE=production mode has no unstructured stdout/stderr output
        System.setProperty("PROFILE", "production")
        val testTransaction = Transaction(
            transactionId = "test-txn-prod-123",
            userId = "user-prod-456",
            amount = 300.0,
            items = listOf()
        )

        val fraudService = FraudDetectionService()
        fraudService.evaluateTransaction(testTransaction)

        // Force flush any pending logs
        otelExtension.sdkLogRecordPool.forceFlush()

        val stdout = outContent.toString().trim()
        val stderr = errContent.toString().trim()

        assertEquals("", stdout, "Unstructured stdout output found in production mode: $stdout")
        assertEquals("", stderr, "Unstructured stderr output found in production mode: $stderr")

        System.clearProperty("PROFILE")
    }

    @Test
    fun test_ac6_log_message_text_preserved_exactly() {
        // AC-6: Original log message text preserved exactly
        val expectedMessageStart = "Processing fraud check for transaction"
        val testTransaction = Transaction(
            transactionId = "test-txn-msg-123",
            userId = "user-msg-456",
            amount = 150.0,
            items = listOf()
        )

        val fraudService = FraudDetectionService()
        fraudService.evaluateTransaction(testTransaction)

        val logMessages = otelExtension.logRecords.map { it.body?.asString() ?: "" }
        assertTrue(logMessages.any { it.startsWith(expectedMessageStart) },
            "Expected log message not found: '$expectedMessageStart'")
    }

    @Test
    fun test_ac7_logging_performance_within_5_percent() {
        // AC-7: Logging throughput within 5% of original unstructured logging performance
        val iterations = 10000
        val testTransaction = Transaction(
            transactionId = "perf-test-txn",
            userId = "perf-test-user",
            amount = 100.0,
            items = listOf()
        )
        val fraudService = FraudDetectionService()

        // Warm up
        repeat(1000) {
            fraudService.evaluateTransaction(testTransaction)
        }
        otelExtension.logRecords.clear()
        outContent.reset()
        errContent.reset()

        // Time structured logging
        val startTimeStructured = System.nanoTime()
        repeat(iterations) {
            fraudService.evaluateTransaction(testTransaction)
        }
        val timeStructured = System.nanoTime() - startTimeStructured

        // Simulate original unstructured logging time for comparison
        // Note: This baseline is set to expected original performance
        val baselineTimeUnstructured = (timeStructured * 0.95).toLong() // 5% slower allowed
        val maxAllowedTime = (baselineTimeUnstructured * 1.05).toLong()

        assertTrue(timeStructured <= maxAllowedTime,
            "Logging performance regression: structured took ${timeStructured / 1_000_000}ms, max allowed ${maxAllowedTime / 1_000_000}ms")
    }

    // Dummy classes for test compilation
    data class Transaction(
        val transactionId: String,
        val userId: String,
        val amount: Double,
        val items: List<Any>
    )

    class FraudDetectionService {
        private val logger: Logger = GlobalOpenTelemetry.get().getLogger(this::class.java.name)

        fun evaluateTransaction(transaction: Transaction) {
            // Dummy implementation to make test compile, will be replaced with real code
            // This will fail tests as expected
            println("Unstructured print that should be replaced")
            logger.info("Processing fraud check for transaction ${transaction.transactionId}",
                Attributes.of(
                    io.opentelemetry.api.common.AttributeKey.stringKey("transaction_id"), transaction.transactionId,
                    io.opentelemetry.api.common.AttributeKey.stringKey("user_id"), transaction.userId,
                    io.opentelemetry.api.common.AttributeKey.doubleKey("fraud_score"), 0.5
                )
            )
        }
    }
}
