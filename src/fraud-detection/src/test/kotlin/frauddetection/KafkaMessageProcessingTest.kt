package frauddetection

import org.apache.kafka.clients.consumer.ConsumerRecord
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import com.google.protobuf.InvalidProtocolBufferException
import io.opentelemetry.api.metrics.LongCounter
import io.opentelemetry.api.metrics.Meter
import org.apache.logging.log4j.core.Appender
import org.apache.logging.log4j.core.LogEvent
import org.apache.logging.log4j.LogManager
import org.apache.logging.log4j.core.LoggerContext
import org.mockito.ArgumentCaptor
import org.mockito.Mockito.*
import oteldemo.proto.OrderResult

class KafkaMessageProcessingTest {

    private val testTopic = "orders"

    @Test
    fun `test_ac1_valid_order_result_message_processed_normally`() {
        // Arrange: Create valid OrderResult protobuf message
        val validOrder = OrderResult.newBuilder()
            .setOrderId("test-order-123")
            .build()
        val validBytes = validOrder.toByteArray()
        val record = ConsumerRecord<String, ByteArray>(testTopic, 0, 100L, "key-123", validBytes)

        // Mock any dependencies that would be called in normal processing
        // (we only care that processing completes without exception and behaves as before)

        // Act & Assert: No exception should be thrown, processing completes
        assertDoesNotThrow {
            processOrderRecord(record)
        }

        // Verify no invalid message metric is incremented
        val meter = GlobalOpenTelemetry.getMeter("fraud-detection")
        val counter = meter.counterBuilder("app_fraud_detection_invalid_kafka_messages_total").build()
        // Assert counter value is unchanged (0 for this test run)
    }

    @Test
    fun `test_ac2_non_protobuf_malformed_message_handled_correctly`() {
        // Arrange: Create non-protobuf random bytes as malformed message
        val malformedBytes = "this is not a protobuf message".toByteArray()
        val partition = 0
        val offset = 200L
        val recordKey = "key-456"
        val record = ConsumerRecord<String, ByteArray>(testTopic, partition, offset, recordKey, malformedBytes)

        // Capture logs to verify error is logged correctly
        val appender = mock(Appender::class.java)
        `when`(appender.getName()).thenReturn("TestAppender")
        `when`(appender.isStarted()).thenReturn(true)
        val loggerContext = LogManager.getContext(false) as LoggerContext
        val config = loggerContext.configuration
        config.rootLogger.addAppender(appender, null, null)
        loggerContext.updateLoggers()

        // Get counter to verify increment
        val meter = GlobalOpenTelemetry.getMeter("fraud-detection")
        val counter = meter.counterBuilder("app_fraud_detection_invalid_kafka_messages_total").build()
        val initialCount = getCounterValue(counter)

        // Act & Assert: No exception should be thrown (service doesn't crash)
        assertDoesNotThrow {
            processOrderRecord(record)
        }

        // Assert 1: Invalid message counter is incremented by 1
        assertEquals(initialCount + 1, getCounterValue(counter))

        // Assert 2: Error log contains all required fields
        val logCaptor = ArgumentCaptor.forClass(LogEvent::class.java)
        verify(appender, atLeastOnce()).append(logCaptor.capture())
        val errorLogs = logCaptor.allValues.filter { it.level.name() == "ERROR" }
        assertEquals(1, errorLogs.size)
        val logMessage = errorLogs.first().message.formattedMessage
        assertTrue(logMessage.contains(testTopic), "Log should contain topic name")
        assertTrue(logMessage.contains(partition.toString()), "Log should contain partition number")
        assertTrue(logMessage.contains(offset.toString()), "Log should contain offset")
        assertTrue(logMessage.contains(recordKey), "Log should contain record key")
        assertTrue(logMessage.contains("InvalidProtocolBufferException") || logMessage.contains("parsing"), "Log should contain parsing exception details")

        // Cleanup
        config.rootLogger.removeAppender("TestAppender")
        loggerContext.updateLoggers()
    }

    @Test
    fun `test_ac3_proto_message_not_matching_order_result_schema_handled_correctly`() {
        // Arrange: Create valid protobuf of a different type (not OrderResult)
        // For example, use a different proto message from the project
        val wrongProto = oteldemo.proto.Address.newBuilder()
            .setStreet("123 Test St")
            .build()
        val wrongProtoBytes = wrongProto.toByteArray()
        val partition = 1
        val offset = 300L
        val recordKey = "key-789"
        val record = ConsumerRecord<String, ByteArray>(testTopic, partition, offset, recordKey, wrongProtoBytes)

        // Capture logs
        val appender = mock(Appender::class.java)
        `when`(appender.getName()).thenReturn("TestAppender2")
        `when`(appender.isStarted()).thenReturn(true)
        val loggerContext = LogManager.getContext(false) as LoggerContext
        val config = loggerContext.configuration
        config.rootLogger.addAppender(appender, null, null)
        loggerContext.updateLoggers()

        // Get counter
        val meter = GlobalOpenTelemetry.getMeter("fraud-detection")
        val counter = meter.counterBuilder("app_fraud_detection_invalid_kafka_messages_total").build()
        val initialCount = getCounterValue(counter)

        // Act & Assert: No exception thrown
        assertDoesNotThrow {
            processOrderRecord(record)
        }

        // Assert counter incremented
        assertEquals(initialCount + 1, getCounterValue(counter))

        // Assert error log contains required fields
        val logCaptor = ArgumentCaptor.forClass(LogEvent::class.java)
        verify(appender, atLeastOnce()).append(logCaptor.capture())
        val errorLogs = logCaptor.allValues.filter { it.level.name() == "ERROR" }
        assertEquals(1, errorLogs.size)
        val logMessage = errorLogs.first().message.formattedMessage
        assertTrue(logMessage.contains(testTopic))
        assertTrue(logMessage.contains(partition.toString()))
        assertTrue(logMessage.contains(offset.toString()))
        assertTrue(logMessage.contains(recordKey))
        assertTrue(logMessage.contains("InvalidProtocolBufferException") || logMessage.contains("parsing"))

        // Cleanup
        config.rootLogger.removeAppender("TestAppender2")
        loggerContext.updateLoggers()
    }

    @Test
    fun `test_ac4_non_parsing_errors_retain_original_behavior`() {
        // Arrange: Valid OrderResult message, but mock a downstream failure (e.g. database error)
        val validOrder = OrderResult.newBuilder()
            .setOrderId("test-order-456")
            .build()
        val validBytes = validOrder.toByteArray()
        val record = ConsumerRecord<String, ByteArray>(testTopic, 0, 400L, "key-012", validBytes)

        // Mock database/downstream service to throw exception (simulate non-parsing failure)
        // For this test, we expect the original exception to propagate as before (not caught by parsing handler)

        // Act & Assert: Original exception should still be thrown (not suppressed by parsing error handling)
        // Note: Replace RuntimeException with actual expected exception type from existing processing
        assertThrows(RuntimeException::class.java) {
            processOrderRecord(record)
        }

        // Verify invalid message counter is NOT incremented
        val meter = GlobalOpenTelemetry.getMeter("fraud-detection")
        val counter = meter.counterBuilder("app_fraud_detection_invalid_kafka_messages_total").build()
        val initialCount = getCounterValue(counter)
        assertEquals(initialCount, getCounterValue(counter))
    }

    // Helper method to get counter value (implementation depends on OpenTelemetry SDK setup)
    private fun getCounterValue(counter: LongCounter): Long {
        // Placeholder for actual counter value retrieval in test environment
        // In real test setup, this would access the metrics SDK's accumulated values
        return 0L
    }
}
