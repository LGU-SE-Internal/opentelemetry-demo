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
import java.util.stream.IntStream;

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
    void test_ac1_too_many_context_keys_returns_invalid_argument() {
        // AC-1: When GetAdsRequest contains more than 10 context keys
        AdRequest.Builder builder = AdRequest.newBuilder();
        IntStream.range(0, 11).forEach(i -> builder.addContextKeys("key" + i));
        AdRequest request = builder.build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Too many context keys: maximum 10 allowed", exception.getStatus().getDescription());
    }

    @Test
    void test_ac2_context_key_too_long_returns_invalid_argument() {
        // AC-2: When context key length exceeds 64 characters
        String longKey = "a".repeat(65);
        AdRequest request = AdRequest.newBuilder()
                .addContextKeys(longKey)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Context key too long: maximum 64 characters allowed", exception.getStatus().getDescription());
    }

    @Test
    void test_ac3_invalid_context_key_format_returns_invalid_argument() {
        // AC-3: When context key contains invalid characters
        String invalidKey = "key!@#";
        AdRequest request = AdRequest.newBuilder()
                .addContextKeys(invalidKey)
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Invalid context key format: only alphanumeric characters, hyphens, underscores, and periods are allowed", exception.getStatus().getDescription());
    }

    @Test
    void test_ac3_various_invalid_characters_rejected() {
        // AC-3 Additional tests for various invalid characters
        String[] invalidKeys = {
                "key with space", "key$dollar", "key%percent", "key^caret",
                "key&and", "key*star", "key(parenthesis)", "key=equals",
                "key+plus", "key`backtick", "key~tilde", "key[bracket]",
                "key{curly}", "key|pipe", "key\\backslash", "key;semicolon",
                "key'quote", "key:colon", "key\"doublequote", "key,comma",
                "key<less", "key>greater", "key?question", "key/slash"
        };
        
        for (String invalidKey : invalidKeys) {
            AdRequest request = AdRequest.newBuilder()
                    .addContextKeys(invalidKey)
                    .build();

            StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
            assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
            assertEquals("Invalid context key format: only alphanumeric characters, hyphens, underscores, and periods are allowed", exception.getStatus().getDescription());
        }
    }

    @Test
    void test_ac4_empty_context_key_returns_invalid_argument() {
        // AC-4: When context key is empty string
        AdRequest request = AdRequest.newBuilder()
                .addContextKeys("")
                .build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Context key cannot be empty", exception.getStatus().getDescription());
    }

    @Test
    void test_ac5_valid_context_keys_accepted_and_processed() {
        // AC-5: Valid requests are processed normally
        AdRequest request = AdRequest.newBuilder()
                .addContextKeys("valid-key")
                .addContextKeys("valid_key123")
                .addContextKeys("valid.key.with.periods")
                .addContextKeys("VALID-UPPERCASE")
                .addContextKeys("key-with-mixed-123.456_Chars")
                // Add 5 more to reach 10 total (max allowed)
                .addContextKeys("key6")
                .addContextKeys("key7")
                .addContextKeys("key8")
                .addContextKeys("key9")
                .addContextKeys("key10")
                .build();

        AdResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
        assertFalse(response.getAdsList().isEmpty());
    }

    @Test
    void test_ac5_allowed_characters_are_accepted() {
        // AC-5 Additional test: verify all allowed characters are permitted
        AdRequest request = AdRequest.newBuilder()
                .addContextKeys("abcdefghijklmnopqrstuvwxyz") // lowercase
                .addContextKeys("ABCDEFGHIJKLMNOPQRSTUVWXYZ") // uppercase
                .addContextKeys("0123456789") // numbers
                .addContextKeys("key-with-hyphens") // hyphens
                .addContextKeys("key_with_underscores") // underscores
                .addContextKeys("key.with.periods") // periods
                .addContextKeys("all-allowed_chars.123") // combination
                .build();

        AdResponse response = assertDoesNotThrow(() -> stub.getAds(request));
        assertNotNull(response);
    }

    @Test
    void test_validation_runs_in_order_fail_fast() {
        // Verify validation order: count first, then per-key checks
        // Request with 11 keys, all invalid (should fail on count first)
        AdRequest.Builder builder = AdRequest.newBuilder();
        IntStream.range(0, 11).forEach(i -> builder.addContextKeys("invalid key! " + i));
        AdRequest request = builder.build();

        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.INVALID_ARGUMENT.getCode(), exception.getStatus().getCode());
        assertEquals("Too many context keys: maximum 10 allowed", exception.getStatus().getDescription());
    }
}

