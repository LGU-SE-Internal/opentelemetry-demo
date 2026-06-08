package io.opentelemetry.demo.frauddetection;

import static io.grpc.Status.Code.INVALID_ARGUMENT;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.grpc.StatusRuntimeException;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.testing.GrpcCleanupRule;
import org.junit.Rule;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Validation tests for FraudDetectionService CheckFraud RPC, mapped to acceptance criteria AC-1 to AC-7.
 */
public class FraudDetectionValidationTest {
    @Rule
    public final GrpcCleanupRule grpcCleanup = new GrpcCleanupRule();

    private FraudDetectionServiceGrpc.FraudDetectionServiceBlockingStub blockingStub;

    // Test data constants
    private static final long VALID_AMOUNT = 1000L;
    private static final String VALID_USER_ID = "550e8400-e29b-41d4-a716-446655440000"; // UUID v4
    private static final String VALID_CARD_NUMBER = "4111111111111111"; // Passes Luhn, 16 digits
    private static final String VALID_ORDER_ID = "ABC123DEF456"; // 12 char uppercase alphanumeric

    @BeforeEach
    public void setUp() throws Exception {
        String serverName = InProcessServerBuilder.generateName();

        // Initialize service implementation (will be unimplemented initially, tests should fail)
        FraudDetectionServiceImpl service = new FraudDetectionServiceImpl();

        grpcCleanup.register(InProcessServerBuilder
                .forName(serverName)
                .directExecutor()
                .addService(service)
                .build()
                .start());

        blockingStub = FraudDetectionServiceGrpc.newBlockingStub(
                grpcCleanup.register(InProcessChannelBuilder.forName(serverName).directExecutor().build())
        );
    }

    @Test
    public void test_ac1_missing_amount_field_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("amount is required and cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac1_missing_user_id_field_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("user_id is required and cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac1_empty_user_id_field_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId("")
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("user_id is required and cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac1_missing_card_number_field_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("card_number is required and cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac1_empty_card_number_field_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber("")
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("card_number is required and cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac1_missing_order_id_field_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("order_id is required and cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac1_empty_order_id_field_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId("")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("order_id is required and cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac2_amount_zero_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(0)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Transaction amount must be greater than 0", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac2_amount_negative_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(-100L)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Transaction amount must be greater than 0", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac3_user_id_invalid_uuid_format_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId("not-a-uuid-1234")
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("User ID must be a valid UUID v4", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac4_order_id_too_short_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId("ABC123")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Order ID must be 12-character uppercase alphanumeric", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac4_order_id_lowercase_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId("abc123def456")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Order ID must be 12-character uppercase alphanumeric", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac4_order_id_special_chars_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId("ABC123DEF45@")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Order ID must be 12-character uppercase alphanumeric", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac5_card_number_non_digit_chars_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber("4111-1111-1111-1111")
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Card number must be 13-19 digits with no separators", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac5_card_number_too_short_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber("41111111111") // 11 digits
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Card number must be 13-19 digits with no separators", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac5_card_number_too_long_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber("41111111111111111111") // 20 digits
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Card number must be 13-19 digits with no separators", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac6_card_number_valid_length_fails_luhn_returns_invalid_argument() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber("4111111111111112") // Same as valid but last digit changed, fails Luhn
                .setOrderId(VALID_ORDER_ID)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> blockingStub.checkFraud(request));
        assertEquals(INVALID_ARGUMENT, exception.getStatus().getCode());
        assertEquals("Card number is invalid (failed Luhn check)", exception.getStatus().getDescription());
    }

    @Test
    public void test_ac7_all_valid_fields_passes_validation() {
        CheckFraudRequest request = CheckFraudRequest.newBuilder()
                .setAmount(VALID_AMOUNT)
                .setUserId(VALID_USER_ID)
                .setCardNumber(VALID_CARD_NUMBER)
                .setOrderId(VALID_ORDER_ID)
                .build();

        // Should not throw exception, returns valid response
        CheckFraudResponse response = blockingStub.checkFraud(request);
        // Verify response exists (any valid response is acceptable for AC-7)
        assertEquals(CheckFraudResponse.class, response.getClass());
    }
}
