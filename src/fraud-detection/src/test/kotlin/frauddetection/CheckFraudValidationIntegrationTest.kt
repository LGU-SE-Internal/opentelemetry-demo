package frauddetection

import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.opentelemetry.demo.frauddetection.CheckFraudRequest
import io.opentelemetry.demo.frauddetection.FraudDetectionServiceGrpc
import net.devh.boot.grpc.client.inject.GrpcClient
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.boot.test.context.SpringBootTest
import java.util.*
import kotlin.test.assertEquals
import kotlin.test.assertTrue

@SpringBootTest(properties = ["grpc.server.port=0"])
class CheckFraudValidationIntegrationTest {

    @GrpcClient("test")
    private lateinit var stub: FraudDetectionServiceGrpc.FraudDetectionServiceBlockingStub

    private val validTransactionId = UUID.randomUUID().toString()
    private val validUserId = 12345UL
    private val validMerchantId = 67890UL
    private val validAmount = 10.99
    private val validPaymentMethodId = "pm_abc123def456ghi789jkl01"

    @Test
    fun `test_ac1_missing_empty_transaction_id_returns_invalid_argument`() {
        // Test empty transaction id
        val requestEmpty = CheckFraudRequest.newBuilder()
            .setTransactionId("")
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        val exceptionEmpty = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestEmpty)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionEmpty.status.code)
        assertTrue(exceptionEmpty.status.description?.contains("transaction_id is required and cannot be empty") == true)

        // Test null transaction id (protobuf default empty string)
        val requestMissing = CheckFraudRequest.newBuilder()
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        val exceptionMissing = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestMissing)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionMissing.status.code)
        assertTrue(exceptionMissing.status.description?.contains("transaction_id is required and cannot be empty") == true)
    }

    @Test
    fun `test_ac2_invalid_transaction_id_uuid_format_returns_invalid_argument`() {
        val request = CheckFraudRequest.newBuilder()
            .setTransactionId("not-a-valid-uuid")
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        val exception = assertThrows<StatusRuntimeException> {
            stub.checkFraud(request)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertTrue(exception.status.description?.contains("transaction_id must be a valid UUID v4") == true)
    }

    @Test
    fun `test_ac3_user_id_zero_returns_invalid_argument`() {
        val request = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(0)
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        val exception = assertThrows<StatusRuntimeException> {
            stub.checkFraud(request)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertTrue(exception.status.description?.contains("user_id must be a positive integer") == true)
    }

    @Test
    fun `test_ac4_merchant_id_zero_returns_invalid_argument`() {
        val request = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(0)
            .setAmount(validAmount)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        val exception = assertThrows<StatusRuntimeException> {
            stub.checkFraud(request)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exception.status.code)
        assertTrue(exception.status.description?.contains("merchant_id must be a positive integer") == true)
    }

    @Test
    fun `test_ac5_amount_less_than_0_01_returns_invalid_argument`() {
        // Test amount 0
        val requestZero = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(0.0)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        val exceptionZero = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestZero)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionZero.status.code)
        assertTrue(exceptionZero.status.description?.contains("amount must be greater than or equal to 0.01") == true)

        // Test amount 0.009
        val requestSmall = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(0.009)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        val exceptionSmall = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestSmall)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionSmall.status.code)
        assertTrue(exceptionSmall.status.description?.contains("amount must be greater than or equal to 0.01") == true)
    }

    @Test
    fun `test_ac6_missing_empty_payment_method_id_returns_invalid_argument`() {
        // Test empty payment method id
        val requestEmpty = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId("")
            .build()

        val exceptionEmpty = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestEmpty)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionEmpty.status.code)
        assertTrue(exceptionEmpty.status.description?.contains("payment_method_id is required and cannot be empty") == true)

        // Test missing payment method id
        val requestMissing = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .build()

        val exceptionMissing = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestMissing)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionMissing.status.code)
        assertTrue(exceptionMissing.status.description?.contains("payment_method_id is required and cannot be empty") == true)
    }

    @Test
    fun `test_ac7_invalid_payment_method_id_format_returns_invalid_argument`() {
        // Test wrong prefix
        val requestWrongPrefix = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId("notpm_abc123def456ghi789jkl01")
            .build()

        val exceptionWrongPrefix = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestWrongPrefix)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionWrongPrefix.status.code)
        assertTrue(exceptionWrongPrefix.status.description?.contains("payment_method_id must match format pm_<24 alphanumeric characters>") == true)

        // Test too short suffix
        val requestTooShort = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId("pm_abc123")
            .build()

        val exceptionTooShort = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestTooShort)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionTooShort.status.code)
        assertTrue(exceptionTooShort.status.description?.contains("payment_method_id must match format pm_<24 alphanumeric characters>") == true)

        // Test special characters
        val requestSpecialChars = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId("pm_abc123def456ghi789jk!@#")
            .build()

        val exceptionSpecialChars = assertThrows<StatusRuntimeException> {
            stub.checkFraud(requestSpecialChars)
        }
        assertEquals(Status.Code.INVALID_ARGUMENT, exceptionSpecialChars.status.code)
        assertTrue(exceptionSpecialChars.status.description?.contains("payment_method_id must match format pm_<24 alphanumeric characters>") == true)
    }

    @Test
    fun `test_ac8_valid_request_passes_validation`() {
        val request = CheckFraudRequest.newBuilder()
            .setTransactionId(validTransactionId)
            .setUserId(validUserId.toLong())
            .setMerchantId(validMerchantId.toLong())
            .setAmount(validAmount)
            .setPaymentMethodId(validPaymentMethodId)
            .build()

        // Should not throw exception
        val response = stub.checkFraud(request)
        // We just check that a response is returned, no validation on fraud score as that's out of scope
        assertTrue(response.fraudScore >= 0.0)
    }
}
