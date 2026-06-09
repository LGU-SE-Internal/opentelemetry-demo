package opentelemetry.demo.adservice;

import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.sdk.testing.junit5.OpenTelemetryExtension;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.slf4j.LoggerFactory;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;
import static org.junit.jupiter.api.Assertions.*;

public class AdServiceRateLimitIntegrationTest {

    @RegisterExtension
    static final OpenTelemetryExtension otelTesting = OpenTelemetryExtension.create();

    private AdServiceGrpc.AdServiceBlockingStub stub;
    private ListAppender<ILoggingEvent> listAppender;
    private Logger rootLogger;

    @BeforeEach
    void setUp() {
        // Set up gRPC stub to ad service (configure test container/channel here)
        stub = AdServiceGrpc.newBlockingStub(/* channel to test service instance */);
        // Set up log appender to capture structured logs
        rootLogger = (Logger) LoggerFactory.getLogger(org.slf4j.Logger.ROOT_LOGGER_NAME);
        listAppender = new ListAppender<>();
        listAppender.start();
        rootLogger.addAppender(listAppender);
    }

    @AfterEach
    void tearDown() {
        rootLogger.detachAppender(listAppender);
        listAppender.stop();
        otelTesting.clearSpans();
        // Clear test environment variables after each run
        System.clearProperty("AD_SERVICE_RATE_LIMIT_REQUESTS_PER_PERIOD");
        System.clearProperty("AD_SERVICE_RATE_LIMIT_PERIOD_DURATION_MS");
    }

    @Test
    void test_ac1_excess_requests_return_resource_exhausted_status() {
        // Configure rate limit to 10 requests per 1 second window
        System.setProperty("AD_SERVICE_RATE_LIMIT_REQUESTS_PER_PERIOD", "10");
        System.setProperty("AD_SERVICE_RATE_LIMIT_PERIOD_DURATION_MS", "1000");

        // Send 10 valid requests that should all succeed
        for (int i = 0; i < 10; i++) {
            AdRequest request = AdRequest.newBuilder().build();
            assertNotNull(stub.getAds(request), "Request " + i + " should succeed within rate limit");
        }

        // 11th request exceeds rate limit, should return RESOURCE_EXHAUSTED
        StatusRuntimeException rateLimitException = assertThrows(StatusRuntimeException.class, () -> {
            stub.getAds(AdRequest.newBuilder().build());
        }, "Excess request should fail with rate limit error");

        assertEquals(Status.Code.RESOURCE_EXHAUSTED, rateLimitException.getStatus().getCode(), "Incorrect gRPC status code for rate limit");
        assertEquals("Rate limit exceeded. Try again later.", rateLimitException.getStatus().getDescription(), "Incorrect rate limit error message");
    }

    @Test
    void test_ac2_custom_rate_limit_configuration_works() {
        // Set custom rate limit values from AC-2: 500 requests per 2 seconds
        System.setProperty("AD_SERVICE_RATE_LIMIT_REQUESTS_PER_PERIOD", "500");
        System.setProperty("AD_SERVICE_RATE_LIMIT_PERIOD_DURATION_MS", "2000");

        // Send 500 requests, all should succeed
        for (int i = 0; i < 500; i++) {
            AdRequest request = AdRequest.newBuilder().build();
            assertNotNull(stub.getAds(request), "Request " + i + " should succeed within custom rate limit");
        }

        // 501st request should fail with rate limit
        assertThrows(StatusRuntimeException.class, () -> stub.getAds(AdRequest.newBuilder().build()),
                "501st request should exceed 500/2s limit");

        // Wait for 2 second window to reset
        try {
            TimeUnit.MILLISECONDS.sleep(2000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            fail("Test interrupted during rate limit window wait");
        }

        // Next request should succeed after window reset
        assertNotNull(stub.getAds(AdRequest.newBuilder().build()), "Request should succeed after rate limit window resets");
    }

    @Test
    void test_ac3_default_rate_limit_values_apply() {
        // No environment variables set, default values should apply: 1000 requests per 1s
        for (int i = 0; i < 1000; i++) {
            AdRequest request = AdRequest.newBuilder().build();
            assertNotNull(stub.getAds(request), "Request " + i + " should succeed with default rate limit");
        }

        // 1001st request should exceed default limit
        assertThrows(StatusRuntimeException.class, () -> stub.getAds(AdRequest.newBuilder().build()),
                "1001st request should exceed default 1000/1s limit");

        // Wait for 1 second window reset
        try {
            TimeUnit.MILLISECONDS.sleep(1000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            fail("Test interrupted during default rate limit window wait");
        }

        // Request should succeed after window reset
        assertNotNull(stub.getAds(AdRequest.newBuilder().build()), "Request should succeed after default window resets");
    }

    @Test
    void test_ac4_rate_limit_hit_generates_warn_log_with_required_fields() {
        System.setProperty("AD_SERVICE_RATE_LIMIT_REQUESTS_PER_PERIOD", "1");
        System.setProperty("AD_SERVICE_RATE_LIMIT_PERIOD_DURATION_MS", "1000");

        // First request succeeds
        stub.getAds(AdRequest.newBuilder().build());
        // Second request triggers rate limit hit
        assertThrows(StatusRuntimeException.class, () -> stub.getAds(AdRequest.newBuilder().build()));

        // Filter WARN level logs for rate limit event
        List<ILoggingEvent> warnLogs = listAppender.list.stream()
                .filter(event -> event.getLevel().levelStr.equals("WARN"))
                .filter(event -> "rate_limit_hit".equals(event.getMDCPropertyMap().get("event")))
                .collect(Collectors.toList());

        assertEquals(1, warnLogs.size(), "Expected exactly one WARN log for rate limit hit");
        ILoggingEvent rateLimitLog = warnLogs.get(0);
        Map<String, String> logMdc = rateLimitLog.getMDCPropertyMap();

        // Validate all required log fields
        assertEquals("rate_limit_hit", logMdc.get("event"));
        assertEquals("opentelemetry.demo.adservice.AdService/GetAds", logMdc.get("endpoint"));
        assertNotNull(logMdc.get("remote_address"), "remote_address field missing from rate limit log");
        assertEquals("1", logMdc.get("limit"), "limit field incorrect in rate limit log");
        assertEquals("1000", logMdc.get("period_ms"), "period_ms field incorrect in rate limit log");
    }

    @Test
    void test_ac5_rate_limit_hit_adds_trace_attribute() {
        System.setProperty("AD_SERVICE_RATE_LIMIT_REQUESTS_PER_PERIOD", "1");
        System.setProperty("AD_SERVICE_RATE_LIMIT_PERIOD_DURATION_MS", "1000");

        // First request succeeds
        stub.getAds(AdRequest.newBuilder().build());
        // Second request hits rate limit
        assertThrows(StatusRuntimeException.class, () -> stub.getAds(AdRequest.newBuilder().build()));

        // Check for trace attribute in captured spans
        var spans = otelTesting.getSpans();
        var rateLimitSpans = spans.stream()
                .filter(span -> Boolean.TRUE.equals(span.getAttribute(io.opentelemetry.api.common.AttributeKey.booleanKey("resilience4j.rate_limit.hit"))))
                .collect(Collectors.toList());

        assertEquals(1, rateLimitSpans.size(), "Expected exactly one span with rate limit hit attribute");
        assertTrue(rateLimitSpans.get(0).getAttribute(io.opentelemetry.api.common.AttributeKey.booleanKey("resilience4j.rate_limit.hit")),
                "Rate limit hit trace attribute should be true");
    }

    @Test
    void test_ac6_rate_limit_applies_to_all_grpc_endpoints() {
        System.setProperty("AD_SERVICE_RATE_LIMIT_REQUESTS_PER_PERIOD", "1");
        System.setProperty("AD_SERVICE_RATE_LIMIT_PERIOD_DURATION_MS", "1000");

        // Test GetAds endpoint
        stub.getAds(AdRequest.newBuilder().build());
        assertThrows(StatusRuntimeException.class, () -> stub.getAds(AdRequest.newBuilder().build()),
                "GetAds endpoint should enforce rate limit");

        // Reset rate limit window
        try {
            TimeUnit.MILLISECONDS.sleep(1000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            fail("Test interrupted during rate limit reset wait");
        }

        // Test other existing endpoints (add other service methods here as applicable)
        // Example: if ListAds endpoint exists:
        // stub.listAds(ListAdRequest.newBuilder().build());
        // assertThrows(StatusRuntimeException.class, () -> stub.listAds(ListAdRequest.newBuilder().build()),
        //         "ListAds endpoint should enforce rate limit");
    }
}
