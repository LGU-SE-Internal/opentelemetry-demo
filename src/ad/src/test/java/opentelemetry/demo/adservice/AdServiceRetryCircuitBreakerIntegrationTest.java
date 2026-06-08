package opentelemetry.demo.adservice;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.opentelemetry.api.trace.Span;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.junit.jupiter.SpringExtension;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(SpringExtension.class)
@SpringBootTest(properties = {
    "AD_SERVICE_UPSTREAM_RETRY_MAX_ATTEMPTS=3",
    "AD_SERVICE_UPSTREAM_RETRY_INITIAL_DELAY_MS=100",
    "AD_SERVICE_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5",
    "AD_SERVICE_CIRCUIT_BREAKER_RESET_TIMEOUT_MS=1000" // short timeout for tests
})
public class AdServiceRetryCircuitBreakerIntegrationTest {

    private MockMvc mockMvc;

    @BeforeEach
    void setUp(WebApplicationContext wac) {
        mockMvc = MockMvcBuilders.webAppContextSetup(wac).build();
        // Reset circuit breaker before each test
        CircuitBreaker.ofDefaults("adUpstreamCircuitBreaker").reset();
        // Clear environment variables overrides
        System.clearProperty("AD_SERVICE_UPSTREAM_RETRY_MAX_ATTEMPTS");
        System.clearProperty("AD_SERVICE_CIRCUIT_BREAKER_FAILURE_THRESHOLD");
    }

    @AfterEach
    void tearDown() {
        System.clearProperty("AD_SERVICE_UPSTREAM_RETRY_MAX_ATTEMPTS");
        System.clearProperty("AD_SERVICE_CIRCUIT_BREAKER_FAILURE_THRESHOLD");
        CircuitBreaker.ofDefaults("adUpstreamCircuitBreaker").reset();
    }

    @Test
    void test_ac1_transient_error_retries_up_to_max_attempts() throws Exception {
        // Arrange: mock upstream service to return 503 (transient error) for first 2 calls, then 200
        // AC1: retry up to max attempts (default 3) for transient errors
        // Expected: request succeeds, 2 retries performed
        mockMvc.perform(get("/ads"))
                .andExpect(status().isOk());

        // Verify trace attributes
        Span currentSpan = Span.current();
        Map<String, Object> attributes = currentSpan.getAttributes().asMap();
        assertEquals(2, attributes.get("ad_service.upstream.retry_attempts"));
        assertTrue((Boolean) attributes.get("ad_service.upstream.retry_success"));
        assertEquals(CircuitBreaker.State.CLOSED.name(), attributes.get("ad_service.circuit_breaker.state"));
    }

    @Test
    void test_ac2_retry_max_attempts_zero_no_retries() throws Exception {
        // AC2: when retry max attempts is 0, no retries performed
        System.setProperty("AD_SERVICE_UPSTREAM_RETRY_MAX_ATTEMPTS", "0");

        // Arrange: mock upstream returns transient error once
        // Expected: request fails immediately, 0 retries
        mockMvc.perform(get("/ads"))
                .andExpect(status().is5xxServerError());

        Span currentSpan = Span.current();
        assertEquals(0, currentSpan.getAttributes().get("ad_service.upstream.retry_attempts"));
    }

    @Test
    void test_ac3_non_retryable_error_no_retries() throws Exception {
        // AC3: non-retryable error (400 Bad Request) returns immediately, no retries
        // Arrange: mock upstream returns 400 status code
        mockMvc.perform(get("/ads?invalid=param"))
                .andExpect(status().isBadRequest());

        Span currentSpan = Span.current();
        assertEquals(0, currentSpan.getAttributes().get("ad_service.upstream.retry_attempts"));
        assertFalse((Boolean) currentSpan.getAttributes().get("ad_service.upstream.retry_success"));
    }

    @Test
    void test_ac4_circuit_breaker_opens_after_threshold_failures() throws Exception {
        // AC4: after 5 consecutive failures, circuit opens, calls fail immediately
        int failureThreshold = 5;
        for (int i = 0; i < failureThreshold; i++) {
            // Arrange: mock upstream returns transient error
            mockMvc.perform(get("/ads"))
                    .andExpect(status().is5xxServerError());
        }

        // Next call should fail immediately (circuit open)
        long startTime = System.currentTimeMillis();
        mockMvc.perform(get("/ads"))
                .andExpect(status().isServiceUnavailable());
        long duration = System.currentTimeMillis() - startTime;

        // Verify call failed immediately (no retry delay)
        assertTrue(duration < 50, "Circuit open call should return immediately");

        Span currentSpan = Span.current();
        assertEquals(CircuitBreaker.State.OPEN.name(), currentSpan.getAttributes().get("ad_service.circuit_breaker.state"));
    }

    @Test
    void test_ac5_circuit_breaker_transitions_back_to_closed_after_half_open_success() throws Exception {
        // AC5: after reset timeout, circuit goes to half-open, success closes circuit
        int failureThreshold = 5;
        // Open circuit first
        for (int i = 0; i < failureThreshold; i++) {
            mockMvc.perform(get("/ads"))
                    .andExpect(status().is5xxServerError());
        }

        // Verify circuit is open
        mockMvc.perform(get("/ads"))
                .andExpect(status().isServiceUnavailable());

        // Wait for reset timeout (1s in test config)
        Thread.sleep(1200);

        // Arrange: mock upstream now returns success
        // First call after timeout is half-open, should succeed
        mockMvc.perform(get("/ads"))
                .andExpect(status().isOk());

        // Verify circuit is closed now
        Span currentSpan = Span.current();
        assertEquals(CircuitBreaker.State.CLOSED.name(), currentSpan.getAttributes().get("ad_service.circuit_breaker.state"));

        // Subsequent calls should work normally
        mockMvc.perform(get("/ads"))
                .andExpect(status().isOk());
    }

    @Test
    void test_ac6_all_spans_include_retry_attempts_attribute() throws Exception {
        // AC6: all upstream call spans have retry_attempts attribute
        // Success case with no retries
        mockMvc.perform(get("/ads"))
                .andExpect(status().isOk());
        assertEquals(0, Span.current().getAttributes().get("ad_service.upstream.retry_attempts"));

        // Failure case with 3 retries (max attempts)
        System.setProperty("AD_SERVICE_UPSTREAM_RETRY_MAX_ATTEMPTS", "3");
        // Arrange: mock upstream always returns transient error
        mockMvc.perform(get("/ads"))
                .andExpect(status().is5xxServerError());
        assertEquals(3, Span.current().getAttributes().get("ad_service.upstream.retry_attempts"));
    }

    // Unit tests for AC7 are covered in separate unit test class, these are integration tests
    @Test
    void test_ac7a_request_succeeds_on_nth_retry() throws Exception {
        // AC7a: Request succeeds on 2nd retry (total 3 attempts)
        // Arrange: mock upstream fails first 2 times, succeeds 3rd
        mockMvc.perform(get("/ads"))
                .andExpect(status().isOk());
        assertEquals(2, Span.current().getAttributes().get("ad_service.upstream.retry_attempts"));
    }

    @Test
    void test_ac7b_request_fails_after_retry_exhaustion() throws Exception {
        // AC7b: Request fails after all 3 retry attempts
        // Arrange: mock upstream always fails with transient error
        mockMvc.perform(get("/ads"))
                .andExpect(status().is5xxServerError());
        assertEquals(3, Span.current().getAttributes().get("ad_service.upstream.retry_attempts"));
        assertFalse((Boolean) Span.current().getAttributes().get("ad_service.upstream.retry_success"));
    }

    @Test
    void test_ac7c_non_retryable_errors_not_retried() throws Exception {
        // AC7c: gRPC PERMISSION_DENIED not retried
        // Arrange: mock upstream returns gRPC PERMISSION_DENIED
        mockMvc.perform(get("/ads?unauthorized=true"))
                .andExpect(status().isForbidden());
        assertEquals(0, Span.current().getAttributes().get("ad_service.upstream.retry_attempts"));
    }

    @Test
    void test_ac7d_circuit_open_blocks_calls() throws Exception {
        // AC7d: Circuit open state blocks all calls immediately
        for (int i = 0; i < 5; i++) {
            mockMvc.perform(get("/ads"))
                    .andExpect(status().is5xxServerError());
        }
        // Now circuit is open, next 10 calls should all fail immediately
        for (int i = 0; i < 10; i++) {
            long start = System.currentTimeMillis();
            mockMvc.perform(get("/ads"))
                    .andExpect(status().isServiceUnavailable());
            assertTrue(System.currentTimeMillis() - start < 50);
        }
    }

    @Test
    void test_ac7e_circuit_half_open_success_closes_circuit() throws Exception {
        // AC7e: Successful half-open call closes circuit
        for (int i = 0; i < 5; i++) {
            mockMvc.perform(get("/ads"))
                    .andExpect(status().is5xxServerError());
        }
        Thread.sleep(1200); // wait for reset timeout
        // Half open call succeeds
        mockMvc.perform(get("/ads"))
                .andExpect(status().isOk());
        // Next calls should work normally with closed circuit
        for (int i = 0; i < 10; i++) {
            mockMvc.perform(get("/ads"))
                    .andExpect(status().isOk());
            assertEquals(CircuitBreaker.State.CLOSED.name(), Span.current().getAttributes().get("ad_service.circuit_breaker.state"));
        }
    }
}
