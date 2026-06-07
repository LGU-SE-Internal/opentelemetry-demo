package frauddetection

import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import java.nio.charset.StandardCharsets

// Interface definitions from spec (to be implemented)
sealed class Result<out T> {
    data class Success<out T>(val value: T) : Result<T>()
    data class Failure(val error: Exception) : Result<Nothing>()
}

data class ValidatedAddress(
    val street: String,
    val city: String,
    val postalCode: String,
    val country: String
)

data class OrderItem(
    val id: String,
    val name: String,
    val quantity: Int,
    val price: Long
)

data class ValidatedOrder(
    val orderId: String,
    val userId: String,
    val items: List<OrderItem>,
    val shippingAddress: ValidatedAddress,
    val paymentMethodId: String,
    val totalAmount: Long
)

sealed class OrderValidationError(message: String) : Exception(message) {
    class MalformedProtobuf(message: String = "Failed to deserialize Protobuf message") : OrderValidationError(message)
    class MissingRequiredField(field: String) : OrderValidationError("Missing required field: $field")
    class InvalidFieldValue(field: String, reason: String) : OrderValidationError("Invalid value for field $field: $reason")
    class FieldTooLong(field: String, maxLength: Int) : OrderValidationError("Field $field exceeds maximum allowed length $maxLength")
}

// Placeholder for implementation function
fun validateOrderMessage(message: ByteArray): Result<ValidatedOrder> {
    throw NotImplementedError("validateOrderMessage not implemented yet")
}

class OrderValidationTest {

    @Test
    fun `test_ac1_valid_message_returns_sanitized_order`() {
        // Valid Protobuf message bytes would be here, using dummy for test
        val validMessageBytes = "valid_order_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(validMessageBytes)
        
        assertTrue(result is Result.Success)
        val order = (result as Result.Success).value
        assertTrue(order.orderId.isNotBlank())
        assertTrue(order.userId.isNotBlank())
        assertTrue(order.items.isNotEmpty())
        assertTrue(order.paymentMethodId.isNotBlank())
        assertTrue(order.totalAmount >= 0)
        // Verify no leading/trailing whitespace in string fields
        assertEquals(order.orderId.trim(), order.orderId)
        assertEquals(order.userId.trim(), order.userId)
        assertEquals(order.paymentMethodId.trim(), order.paymentMethodId)
        // Verify max lengths
        assertTrue(order.orderId.length <= 64)
        assertTrue(order.userId.length <= 64)
        assertTrue(order.paymentMethodId.length <= 64)
        assertTrue(order.shippingAddress.street.length <= 256)
        assertTrue(order.shippingAddress.city.length <= 256)
        assertTrue(order.shippingAddress.postalCode.length <= 256)
        assertTrue(order.shippingAddress.country.length <= 256)
    }

    @Test
    fun `test_ac2_missing_order_id_logs_warning_skips_processing`() {
        val messageMissingOrderId = "order_without_id_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageMissingOrderId)
        
        assertTrue(result is Result.Failure)
        val error = (result as Result.Failure).error
        assertTrue(error is OrderValidationError.MissingRequiredField)
        assertEquals("Missing required field: order_id", error.message)
    }

    @Test
    fun `test_ac3_missing_user_id_logs_warning_skips_processing`() {
        val messageMissingUserId = "order_without_user_id_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageMissingUserId)
        
        assertTrue(result is Result.Failure)
        val error = (result as Result.Failure).error
        assertTrue(error is OrderValidationError.MissingRequiredField)
        assertEquals("Missing required field: user_id", error.message)
    }

    @Test
    fun `test_ac4_empty_items_list_logs_warning_skips_processing`() {
        val messageEmptyItems = "order_with_empty_items_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageEmptyItems)
        
        assertTrue(result is Result.Failure)
        val error = (result as Result.Failure).error
        assertTrue(error is OrderValidationError.InvalidFieldValue)
        assertEquals("Invalid value for field items: list is empty", error.message)
    }

    @Test
    fun `test_ac5_missing_shipping_address_fields_logs_warning`() {
        val messageMissingStreet = "order_without_street_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageMissingStreet)
        
        assertTrue(result is Result.Failure)
        val error = (result as Result.Failure).error
        assertTrue(error is OrderValidationError.MissingRequiredField)
        assertTrue(error.message!!.startsWith("Missing required field: shipping_address."))
    }

    @Test
    fun `test_ac6_missing_payment_method_id_logs_warning`() {
        val messageMissingPaymentMethod = "order_without_payment_id_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageMissingPaymentMethod)
        
        assertTrue(result is Result.Failure)
        val error = (result as Result.Failure).error
        assertTrue(error is OrderValidationError.MissingRequiredField)
        assertEquals("Missing required field: payment_method_id", error.message)
    }

    @Test
    fun `test_ac7_order_id_with_whitespace_is_trimmed`() {
        val messageWithWhitespaceOrderId = "order_with_whitespace_id_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageWithWhitespaceOrderId)
        
        assertTrue(result is Result.Success)
        val order = (result as Result.Success).value
        assertEquals("ord-1234", order.orderId)
    }

    @Test
    fun `test_ac8_long_order_id_is_truncated_to_64_chars`() {
        val messageWithLongOrderId = "order_with_100char_id_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageWithLongOrderId)
        
        assertTrue(result is Result.Success)
        val order = (result as Result.Success).value
        assertEquals(64, order.orderId.length)
    }

    @Test
    fun `test_ac9_corrupted_non_protobuf_message_logs_warning_no_crash`() {
        val corruptedBytes = byteArrayOf(0x00, 0x01, 0x02, 0x03, 0x04) // Not valid protobuf
        
        val result = validateOrderMessage(corruptedBytes)
        
        assertTrue(result is Result.Failure)
        val error = (result as Result.Failure).error
        assertTrue(error is OrderValidationError.MalformedProtobuf)
        assertEquals("Failed to deserialize Protobuf message", error.message)
    }

    @Test
    fun `test_ac10_negative_total_amount_logs_warning_skips_processing`() {
        val messageWithNegativeTotal = "order_with_negative_total_proto".toByteArray(StandardCharsets.UTF_8)
        
        val result = validateOrderMessage(messageWithNegativeTotal)
        
        assertTrue(result is Result.Failure)
    }
}
