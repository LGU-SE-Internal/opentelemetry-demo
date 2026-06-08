package oteldemo;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerOpenException;
import io.grpc.ClientInterceptor;
import io.opentelemetry.api.trace.Span;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

// Test class for ad service circuit breaker implementation
// AC-1 to AC-7 integration tests
public class AdServiceCircuitBreakerIntegrationTest {

    private CircuitBreakerConfig circuitBreakerConfig;
    private CircuitBreakerClientInterceptor clientInterceptor;
    private AdServiceFallback fallbackProvider;
    private CircuitBreakerStateChangeListener stateListener;

    @BeforeEach
    void setUp() {
        // Initialize from spec-defined interface classes
        circuitBreakerConfig = new CircuitBreakerConfig();
        fallbackProvider = new AdServiceFallback() {};
        stateListener = new CircuitBreakerStateChangeListener();
        clientInterceptor = new CircuitBreakerClientInterceptor(circuitBreakerConfig, fallbackProvider, stateListener);
    }

    // AC-1: >=50% failure rate across min 20 calls → circuit CLOSED→OPEN
    @Test
    void test_ac1_circuit_opens_at_50_percent_failure_after_min_20_calls() {
        CircuitBreaker testCb = CircuitBreaker.of("test-upstream-service", circuitBreakerConfig.toResilience4jConfig());
        AtomicInteger successCount = new AtomicInteger(0);
        AtomicInteger failureCount = new AtomicInteger(0);

        // Make 20 calls, 10 success + 10 failure (50% failure)
        for (int i = 0; i < 20; i++) {
            try {
                if (i % 2 == 0) {
                    testCb.executeRunnable(() -> successCount.incrementAndGet());
                } else {
                    testCb.executeRunnable(() -> {
                        failureCount.incrementAndGet();
                        throw new RuntimeException("Upstream failure");
                    });
                }
            } catch (Exception ignored) {}
        }

        // Verify circuit is now OPEN
        assertEquals(CircuitBreaker.State.OPEN, testCb.getState());
        // Verify minimum calls threshold was met
        assertEquals(20, successCount.get() + failureCount.get());
        // Verify failure rate is exactly 50%
        assertEquals(50.0f, testCb.getMetrics().getFailureRate(), 0.1f);
    }

    // AC-2: OPEN state → fallback responses for 10s, no remote calls
    @Test
    void test_ac2_open_state_returns_fallback_for_10s_no_remote_calls() throws InterruptedException {
        CircuitBreaker testCb = CircuitBreaker.of("test-upstream-service", circuitBreakerConfig.toResilience4jConfig());
        // Force circuit to OPEN state
        testCb.transitionToOpenState();
        AtomicInteger remoteCallCount = new AtomicInteger(0);
        AtomicInteger fallbackCallCount = new AtomicInteger(0);

        // Make calls for 11 seconds, verify only fallbacks returned, no remote calls
        long start = System.currentTimeMillis();
        while (System.currentTimeMillis() - start < 11000) {
            try {
                testCb.executeRunnable(() -> {
                    remoteCallCount.incrementAndGet();
                    throw new RuntimeException("Should not reach here in OPEN state");
                });
            } catch (CircuitBreakerOpenException ex) {
                fallbackCallCount.incrementAndGet();
                AdRequest testRequest = AdRequest.newBuilder().addContextKeys("test").build();
                List<Ad> fallback = fallbackProvider.getFallbackAds(testRequest, ex);
                // Verify fallback is empty list as specified
                assertTrue(fallback.isEmpty());
            }
            Thread.sleep(100);
        }

        // Verify no remote calls were made during OPEN state
        assertEquals(0, remoteCallCount.get());
        // Verify multiple fallback responses returned
        assertTrue(fallbackCallCount.get() > 0);
        // Verify after 10s circuit is no longer OPEN (should be HALF_OPEN)
        assertNotEquals(CircuitBreaker.State.OPEN, testCb.getState());
    }

    // AC-3: Half open state logic
    @Test
    void test_ac3_half_open_state_test_calls_and_state_transitions() {
        CircuitBreaker testCb = CircuitBreaker.of("test-upstream-service", circuitBreakerConfig.toResilience4jConfig());
        testCb.transitionToHalfOpenState();
        AtomicInteger remoteCallCount = new AtomicInteger(0);

        // First test case: >=50% failures in half open → back to OPEN
        for (int i = 0; i < 10; i++) {
            try {
                int finalI = i;
                testCb.executeRunnable(() -> {
                    remoteCallCount.incrementAndGet();
                    if (finalI < 5) throw new RuntimeException("Failure");
                });
            } catch (Exception ignored) {}
        }
        assertEquals(CircuitBreaker.State.OPEN, testCb.getState());
        assertEquals(10, remoteCallCount.get()); // All 10 test calls made

        // Reset for second test case: <50% failures → back to CLOSED
        testCb.transitionToHalfOpenState();
        remoteCallCount.set(0);
        for (int i = 0; i < 10; i++) {
            try {
                int finalI = i;
                testCb.executeRunnable(() -> {
                    remoteCallCount.incrementAndGet();
                    if (finalI < 4) throw new RuntimeException("Failure");
                });
            } catch (Exception ignored) {}
        }
        assertEquals(CircuitBreaker.State.CLOSED, testCb.getState());
        assertEquals(10, remoteCallCount.get());
    }

    // AC-4: State transitions generate structured logs
    @Test
    void test_ac4_state_transition_log_events() throws InterruptedException {
        CircuitBreaker testCb = CircuitBreaker.of("test-log-service", circuitBreakerConfig.toResilience4jConfig());
        CountDownLatch logLatch = new CountDownLatch(1);

        // Register state change listener
        testCb.getEventPublisher().onStateTransition(event -> {
            // Verify log event fields as per AC-4
            assertEquals("test-log-service", event.getCircuitBreakerName());
            assertNotNull(event.getCreationTime());
            assertNotNull(event.getStateTransition().getFromState());
            assertNotNull(event.getStateTransition().getToState());
            assertTrue(testCb.getMetrics().getFailureRate() >= 0);
            logLatch.countDown();
        });

        // Trigger state transition
        testCb.transitionToOpenState();
        assertTrue(logLatch.await(1, TimeUnit.SECONDS), "State transition log event was not emitted");
    }

    // AC-5: State transitions add OTel span attributes
    @Test
    void test_ac5_state_transition_otel_span_attributes() {
        CircuitBreaker testCb = CircuitBreaker.of("test-otel-service", circuitBreakerConfig.toResilience4jConfig());
        Span testSpan = Span.current();

        // Trigger state transition
        testCb.transitionToOpenState();

        // Verify span attributes exist and have correct values
        Map<String, Object> attributes = testSpan.getAttributes().asMap();
        assertTrue(attributes.containsKey("resilience4j.circuitbreaker.name"));
        assertEquals("test-otel-service", attributes.get("resilience4j.circuitbreaker.name"));
        assertTrue(attributes.containsKey("resilience4j.circuitbreaker.previous_state"));
        assertEquals("CLOSED", attributes.get("resilience4j.circuitbreaker.previous_state"));
        assertTrue(attributes.containsKey("resilience4j.circuitbreaker.new_state"));
        assertEquals("OPEN", attributes.get("resilience4j.circuitbreaker.new_state"));
        assertTrue(attributes.containsKey("resilience4j.circuitbreaker.failure_rate"));
        assertTrue((Float) attributes.get("resilience4j.circuitbreaker.failure_rate") >= 0);
    }

    // AC-6: CLOSED state → no changes to normal operation
    @Test
    void test_ac6_closed_state_passes_all_calls_unmodified() {
        CircuitBreaker testCb = CircuitBreaker.of("test-closed-service", circuitBreakerConfig.toResilience4jConfig());
        assertEquals(CircuitBreaker.State.CLOSED, testCb.getState());
        AtomicInteger callCount = new AtomicInteger(0);

        // Make 100 successful calls
        for (int i = 0; i < 100; i++) {
            testCb.executeRunnable(callCount::incrementAndGet);
        }

        // Verify all calls passed through
        assertEquals(100, callCount.get());
        // Verify no fallbacks were triggered
        assertEquals(0, testCb.getMetrics().getNumberOfFailedCalls());
        // Verify circuit remains closed
        assertEquals(CircuitBreaker.State.CLOSED, testCb.getState());
    }

    // AC-7: All gRPC clients wrapped with interceptor
    @Test
    void test_ac7_all_grpc_clients_have_circuit_breaker_interceptor() {
        // Get all gRPC stubs used by ad service
        List<Object> grpcStubs = AdService.getGrpcStubs();
        assertFalse(grpcStubs.isEmpty(), "No gRPC stubs found in ad service");

        for (Object stub : grpcStubs) {
            List<ClientInterceptor> interceptors = ((io.grpc.stub.AbstractStub<?>) stub).getInterceptors();
            // Verify circuit breaker interceptor is present
            assertTrue(interceptors.stream().anyMatch(i -> i instanceof CircuitBreakerClientInterceptor),
                "gRPC stub missing CircuitBreakerClientInterceptor");
        }
    }
}
