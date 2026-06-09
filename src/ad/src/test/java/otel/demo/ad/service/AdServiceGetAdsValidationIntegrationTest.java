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
import oteldemo.GetAdsRequest;
import oteldemo.GetAdsResponse;
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
    void test_ac1_empty_context_keys_entry_rejected() {
        // AC-1: When a GetAdsRequest contains any empty string in the context_keys array
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .addContextKeys("user")
                .addContextKeys("") // Empty entry at index 1
                .addContextKeys("device")
                .addContextValues("123")
                .addContextValues("mobile")
                .addContextValues("android")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertTrue(exception.getStatus().getDescription().contains("context_keys contains empty string at index 1"));
    }

    @Test
    void test_ac2_empty_context_values_entry_rejected() {
        // AC-2: When a GetAdsRequest contains any empty string in the context_values array
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .addContextKeys("user")
                .addContextKeys("device")
                .addContextKeys("session")
                .addContextValues("123")
                .addContextValues("") // Empty entry at index 1
                .addContextValues("abc123")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertTrue(exception.getStatus().getDescription().contains("context_values contains empty string at index 1"));
    }

    @Test
    void test_ac3_context_key_value_length_mismatch_rejected() {
        // AC-3: When a GetAdsRequest has context_keys array length not equal to context_values array length
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .addContextKeys("user")
                .addContextKeys("device")
                .addContextValues("123") // Only 1 value for 2 keys
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertTrue(exception.getStatus().getDescription().contains("context_keys length (2) does not match context_values length (1)"));
    }

    @Test
    void test_ac4_invalid_category_characters_rejected() {
        // AC-4: When a GetAdsRequest has a non-empty category field containing non-alphanumeric characters
        String invalidCategory = "electronics@home";
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .setCategory(invalidCategory)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("category 'electronics@home' contains invalid characters: @", exception.getStatus().getDescription());
    }

    @Test
    void test_ac4_category_too_long_rejected() {
        // AC-4: When a GetAdsRequest has a category field longer than 100 characters
        String longCategory = "a".repeat(101);
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .setCategory(longCategory)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertTrue(exception.getStatus().getDescription().startsWith("category '"));
        assertTrue(exception.getStatus().getDescription().endsWith("' exceeds maximum allowed length of 100 characters"));
    }

    @Test
    void test_ac4_valid_category_allowed_special_chars() {
        // AC-4: Category with allowed special chars (- and _) should pass validation
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .setCategory("home-kitchen_books")
                .addContextKeys("user")
                .addContextValues("123")
                .build();

        // Should not throw validation exception
        assertDoesNotThrow(() -> stub.getAds(request));
    }

    @Test
    void test_ac5_valid_request_proceeds() {
        // AC-5: When all GetAdsRequest fields are valid
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .addContextKeys("user")
                .addContextKeys("device")
                .addContextKeys("region")
                .addContextValues("456")
                .addContextValues("ios")
                .addContextValues("us-west")
                .setCategory("electronics-mobile")
                .build();

        GetAdsResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
        assertFalse(response.getAdsList().isEmpty());
    }

    @Test
    void test_ac5_valid_request_no_category_proceeds() {
        // AC-5: Valid request with no category provided
        GetAdsRequest request = GetAdsRequest.newBuilder()
                .addContextKeys("user")
                .addContextKeys("device")
                .addContextValues("789")
                .addContextValues("desktop")
                .build();

        GetAdsResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
        assertFalse(response.getAdsList().isEmpty());
    }
}
