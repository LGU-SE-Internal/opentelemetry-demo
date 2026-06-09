package frauddetection

import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.opentelemetry.demo.frauddetection.CheckTransactionRequest
import io.opentelemetry.demo.frauddetection.FraudDetectionServiceGrpc
import io.quarkus.grpc.GrpcClient
import io.quarkus.test.junit.QuarkusTest
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.time.Instant
import java.util.*

@QuarkusTest
class CheckTransactionValidationIntegrationTest {

    @GrpcClient
    lateinit var fraudDetectionService: FraudDetectionServiceGrpc.FraudDetectionServiceBlockingStub

    private val validUserId = UUID.randomUUID().toString()
    private val validTransactionId = "txn12345abcdef"
    private val validAmount = 100.0
    private val validCurrency = "USD"
    private val validTimestamp = Instant.now().toEpochMilli()

    private fun buildValidRequest() = CheckTransactionRequest.newBuilder()
        .setUserId(validUserId)
        .setTransactionId(validTransactionId)
        .setAmount(validAmount)
        .setCurrency(validCurrency)
        .setTimestamp(validTimestamp)
        .build()

    @Test
    fun test_ac1_null_user_id_returns_invalid_argument() {
        val request = buildValidRequest().toBuilder()
            .clearUserId()
            .build()

        val exception = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request)
        }

        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertEquals("user_id is required", exception.status.description)
    }

    @Test
    fun test_ac2_invalid_user_id_uuid_returns_invalid_argument() {
        val request = buildValidRequest().toBuilder()
            .setUserId("not-a-valid-uuid")
            .build()

        val exception = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request)
        }

        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertEquals("user_id must be a valid UUID", exception.status.description)
    }

    @Test
    fun test_ac3_null_transaction_id_returns_invalid_argument() {
        val request = buildValidRequest().toBuilder()
            .clearTransactionId()
            .build()

        val exception = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request)
        }

        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertEquals("transaction_id is required", exception.status.description)
    }

    @Test
    fun test_ac4_invalid_transaction_id_format_returns_invalid_argument() {
        // Test non-alphanumeric
        val request1 = buildValidRequest().toBuilder()
            .setTransactionId("txn_123!@#")
            .build()
        val exception1 = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request1)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception1.status.code)
        assertEquals("transaction_id must be a 1-64 character alphanumeric string", exception1.status.description)

        // Test longer than 64 chars
        val longTxnId = "a".repeat(65)
        val request2 = buildValidRequest().toBuilder()
            .setTransactionId(longTxnId)
            .build()
        val exception2 = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request2)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception2.status.code)
        assertEquals("transaction_id must be a 1-64 character alphanumeric string", exception2.status.description)
    }

    @Test
    fun test_ac5_amount_less_than_zero_returns_invalid_argument() {
        val request = buildValidRequest().toBuilder()
            .setAmount(-10.0)
            .build()

        val exception = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request)
        }

        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertEquals("amount must be greater than 0", exception.status.description)
    }

    @Test
    fun test_ac6_invalid_currency_returns_invalid_argument() {
        // Test too short
        val request1 = buildValidRequest().toBuilder()
            .setCurrency("US")
            .build()
        val exception1 = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request1)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception1.status.code)
        assertEquals("currency must be a valid 3-letter ISO 4217 code", exception1.status.description)

        // Test lowercase
        val request2 = buildValidRequest().toBuilder()
            .setCurrency("usd")
            .build()
        val exception2 = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request2)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception2.status.code)
        assertEquals("currency must be a valid 3-letter ISO 4217 code", exception2.status.description)

        // Test non-existent code
        val request3 = buildValidRequest().toBuilder()
            .setCurrency("XYZ")
            .build()
        val exception3 = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request3)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception3.status.code)
        assertEquals("currency must be a valid 3-letter ISO 4217 code", exception3.status.description)
    }

    @Test
    fun test_ac7_future_timestamp_returns_invalid_argument() {
        val futureTimestamp = Instant.now().plusSeconds(3600).toEpochMilli()
        val request = buildValidRequest().toBuilder()
            .setTimestamp(futureTimestamp)
            .build()

        val exception = assertThrows(StatusRuntimeException::class.java) {
            fraudDetectionService.checkTransaction(request)
        }

        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertEquals("timestamp must be a valid Unix timestamp not in the future", exception.status.description)
    }

    @Test
    fun test_ac8_valid_request_passes_validation() {
        val request = buildValidRequest()

        val response = fraudDetectionService.checkTransaction(request)
        assertNotNull(response)
    }
}
