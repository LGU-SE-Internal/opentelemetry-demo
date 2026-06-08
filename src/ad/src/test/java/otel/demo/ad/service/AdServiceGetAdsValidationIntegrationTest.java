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
    void test_ac1_empty_context_category_returns_invalid_argument() {
        // AC-1: Given an AdRequest containing an empty string in the context_categories list
        AdRequest request = AdRequest.newBuilder()
                .addContextCategories("")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Category cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    void test_ac2_context_category_too_long_returns_invalid_argument() {
        // AC-2: Given an AdRequest containing a context_categories entry longer than 255 characters
        String longCategory = "a".repeat(256);
        AdRequest request = AdRequest.newBuilder()
                .addContextCategories(longCategory)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        String expectedMsgStart = "Category '" + longCategory.substring(0, 10) + "...' exceeds maximum allowed length of 255 characters";
        assertTrue(exception.getStatus().getDescription().startsWith("Category '"));
        assertTrue(exception.getStatus().getDescription().endsWith("' exceeds maximum allowed length of 255 characters"));
    }

    @Test
    void test_ac3_context_category_invalid_characters_returns_invalid_argument() {
        // AC-3: Given an AdRequest containing a context_categories entry with non-alphanumeric characters
        String invalidCategory = "electronics!";
        AdRequest request = AdRequest.newBuilder()
                .addContextCategories(invalidCategory)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Category 'electronics!' contains invalid characters: only alphanumeric characters [a-zA-Z0-9] are allowed", exception.getStatus().getDescription());
    }

    @Test
    void test_ac3_context_category_with_special_chars_rejected() {
        // AC-3 Additional test cases for various invalid characters
        String[] invalidCategories = {"home&kitchen", "clothes@store", "books$discount", "sports gear", "food!", "toys#sale"};
        
        for (String invalidCat : invalidCategories) {
            AdRequest request = AdRequest.newBuilder()
                    .addContextCategories(invalidCat)
                    .build();

            StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
            assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
            assertTrue(exception.getStatus().getDescription().contains("contains invalid characters: only alphanumeric characters [a-zA-Z0-9] are allowed"));
            assertTrue(exception.getStatus().getDescription().contains(invalidCat));
        }
    }

    @Test
    void test_ac4_valid_context_categories_accepted() {
        // AC-4: Given an AdRequest where all context_categories entries are valid
        AdRequest request = AdRequest.newBuilder()
                .addContextCategories("electronics")
                .addContextCategories("clothes")
                .addContextCategories("books123")
                .addContextCategories("HomeAppliances")
                .build();

        AdResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
        assertFalse(response.getAdsList().isEmpty());
    }

    @Test
    void test_ac5_multiple_invalid_categories_reject_first() {
        // AC-5: Verify validation fails immediately on first invalid category
        AdRequest request = AdRequest.newBuilder()
                .addContextCategories("validCategory1")
                .addContextCategories("invalid category!") // This should fail first
                .addContextCategories("validCategory2")
                .addContextCategories("anotherInvalid@")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertTrue(exception.getStatus().getDescription().contains("invalid category!"));
    }
}
