package otel.demo.ad.service;

import static org.junit.jupiter.api.Assertions.*;

import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.testing.GrpcCleanupRule;
import org.junit.Rule;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import oteldemo.AdRequest;
import oteldemo.AdResponse;
import oteldemo.AdServiceGrpc;
import java.util.HashMap;
import java.util.Map;

class AdServiceGetAdsValidationIntegrationTest {

    @Rule
    public final GrpcCleanupRule grpcCleanup = new GrpcCleanupRule();
    private AdServiceGrpc.AdServiceBlockingStub stub;

    @BeforeEach
    void setUp() throws Exception {
        // Create a unique in-process server name
        String serverName = InProcessServerBuilder.generateName();
        // Register server to GrpcCleanup to shut down after test
        grpcCleanup.register(InProcessServerBuilder
                .forName(serverName)
                .directExecutor()
                .addService(new oteldemo.AdService())
                .build()
                .start());
        // Create client stub
        stub = AdServiceGrpc.newBlockingStub(grpcCleanup.register(
                InProcessChannelBuilder.forName(serverName)
                        .directExecutor()
                        .build()));
    }

    @Test
    void test_ac1_too_many_context_entries_returns_invalid_argument() {
        // AC-1: >10 context entries -> INVALID_ARGUMENT
        AdRequest.Builder builder = AdRequest.newBuilder();
        // Add 11 context entries
        for (int i = 0; i < 11; i++) {
            builder.putContext("key" + i, "value" + i);
        }
        AdRequest request = builder.build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
    }

    @Test
    void test_ac2_null_context_key_returns_invalid_argument() {
        // AC-2: null context key -> INVALID_ARGUMENT
        AdRequest request = AdRequest.newBuilder()
                .putContext(null, "value")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
    }

    @Test
    void test_ac3_empty_context_key_returns_invalid_argument() {
        // AC-3: empty context key -> INVALID_ARGUMENT
        AdRequest request = AdRequest.newBuilder()
                .putContext("", "value")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
    }

    @Test
    void test_ac4_context_key_too_long_returns_invalid_argument() {
        // AC-4: key longer than 100 chars -> INVALID_ARGUMENT
        String longKey = "a".repeat(101);
        AdRequest request = AdRequest.newBuilder()
                .putContext(longKey, "value")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
    }

    @Test
    void test_ac5_null_context_value_returns_invalid_argument() {
        // AC-5: null context value -> INVALID_ARGUMENT
        AdRequest request = AdRequest.newBuilder()
                .putContext("key", null)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
    }

    @Test
    void test_ac6_empty_context_value_returns_invalid_argument() {
        // AC-6: empty context value -> INVALID_ARGUMENT
        AdRequest request = AdRequest.newBuilder()
                .putContext("key", "")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
    }

    @Test
    void test_ac7_context_value_too_long_returns_invalid_argument() {
        // AC-7: value longer than 100 chars -> INVALID_ARGUMENT
        String longValue = "a".repeat(101);
        AdRequest request = AdRequest.newBuilder()
                .putContext("key", longValue)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
    }

    @Test
    void test_ac8_context_value_with_forbidden_characters_returns_invalid_argument() {
        // AC-8: value with newlines, null bytes or control characters -> INVALID_ARGUMENT
        // Test newline
        AdRequest request1 = AdRequest.newBuilder()
                .putContext("key", "value\nwithnewline")
                .build();
        StatusRuntimeException exception1 = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request1));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception1.getStatus().getCode());

        // Test carriage return
        AdRequest request2 = AdRequest.newBuilder()
                .putContext("key", "value\rwithreturn")
                .build();
        StatusRuntimeException exception2 = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request2));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception2.getStatus().getCode());

        // Test null byte
        AdRequest request3 = AdRequest.newBuilder()
                .putContext("key", "value\0withnull")
                .build();
        StatusRuntimeException exception3 = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request3));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception3.getStatus().getCode());

        // Test control character (ASCII 0x1F = unit separator)
        AdRequest request4 = AdRequest.newBuilder()
                .putContext("key", "value\u001Fwithcontrol")
                .build();
        StatusRuntimeException exception4 = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request4));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception4.getStatus().getCode());

        // Test DEL character (0x7F)
        AdRequest request5 = AdRequest.newBuilder()
                .putContext("key", "value\u007Fwithdel")
                .build();
        StatusRuntimeException exception5 = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request5));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception5.getStatus().getCode());
    }

    @Test
    void test_ac9_valid_request_processed_normally() {
        // AC-9: valid request returns success
        AdRequest request = AdRequest.newBuilder()
                .putContext("device_type", "mobile")
                .putContext("user_segment", "premium")
                .build();

        AdResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
        // Valid request should return at least one ad as per existing service behavior
        assertFalse(response.getAdsList().isEmpty());
    }

    @Test
    void test_ac1_valid_category_accepted_normally() {
        // AC-1: Category with alphanumeric and underscore, length <=64 is accepted
        AdRequest request = AdRequest.newBuilder()
                .setCategory("valid_category_123")
                .build();

        AdResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
    }

    @Test
    void test_ac2_invalid_category_characters_rejected() {
        // AC-2: Category with invalid characters returns INVALID_ARGUMENT with proper message
        AdRequest request = AdRequest.newBuilder()
                .setCategory("invalid category!@#")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Invalid category: must only contain alphanumeric characters and underscores", exception.getStatus().getDescription());
    }

    @Test
    void test_ac3_category_length_exceeded_rejected() {
        // AC-3: Category longer than 64 characters returns INVALID_ARGUMENT with proper message
        String longCategory = "a".repeat(65);
        AdRequest request = AdRequest.newBuilder()
                .setCategory(longCategory)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Invalid category: must not exceed 64 characters in length", exception.getStatus().getDescription());
    }

    @Test
    void test_ac5_empty_category_allowed() {
        // AC-5: Empty category string is allowed per existing behavior
        AdRequest request = AdRequest.newBuilder()
                .setCategory("")
                .build();

        AdResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
    }
}
