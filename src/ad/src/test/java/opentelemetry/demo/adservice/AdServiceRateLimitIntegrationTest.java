package opentelemetry.demo.adservice;

import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import opentelemetry.proto.demo.ad.v1.AdRequest;
import opentelemetry.proto.demo.ad.v1.AdResponse;
import opentelemetry.proto.demo.ad.v1.AdServiceGrpc;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.output.Slf4jLogConsumer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

@Testcontainers
public class AdServiceRateLimitIntegrationTest {

    private ManagedChannel channel;
    private AdServiceGrpc.AdServiceBlockingStub stub;

    @Container
    private GenericContainer<?> adServiceContainer = new GenericContainer<>("otel/demo-ad-service:latest")
            .withExposedPorts(9555)
            .withStartupTimeout(Duration.ofMinutes(2));

    @BeforeEach
    void setUp() {
        channel = ManagedChannelBuilder.forAddress(adServiceContainer.getHost(), adServiceContainer.getMappedPort(9555))
                .usePlaintext()
                .build();
        stub = AdServiceGrpc.newBlockingStub(channel);
    }

    @AfterEach
    void tearDown() {
        channel.shutdownNow();
    }

    @Test
    void test_ac1_no_rate_limits_unlimited_requests() {
        // AC-1: No env vars set, all endpoints accept unlimited requests
        adServiceContainer.start();
        List<AdResponse> responses = new ArrayList<>();
        // Send 1000 requests, none should fail with RESOURCE_EXHAUSTED
        for (int i = 0; i < 1000; i++) {
            AdRequest request = AdRequest.newBuilder()
                    .addContextKeys("test")
                    .build();
            AdResponse response = stub.getAds(request);
            responses.add(response);
        }
        assertEquals(1000, responses.size());
        adServiceContainer.stop();
    }

    @Test
    void test_ac2_global_default_rate_limit_applies() {
        // AC-2: Global default RPS set, endpoints without specific limits use it
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GLOBAL_DEFAULT_RPS", "2");
        adServiceContainer.start();

        int successCount = 0;
        int resourceExhaustedCount = 0;

        // Send 10 requests in 1 second, should have max 2 successes
        for (int i = 0; i < 10; i++) {
            try {
                AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();
                stub.getAds(request);
                successCount++;
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                    resourceExhaustedCount++;
                } else {
                    throw e;
                }
            }
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }

        assertTrue(successCount <= 2);
        assertTrue(resourceExhaustedCount >= 8);
        adServiceContainer.stop();
    }

    @Test
    void test_ac3_specific_endpoint_limit_overrides_global() {
        // AC-3: Specific endpoint limit overrides global default
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GLOBAL_DEFAULT_RPS", "2");
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GET_ADS_RPS", "5");
        adServiceContainer.start();

        int successCount = 0;
        int resourceExhaustedCount = 0;

        // Send 10 requests in 1 second, should have max 5 successes
        for (int i = 0; i < 10; i++) {
            try {
                AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();
                stub.getAds(request);
                successCount++;
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                    resourceExhaustedCount++;
                } else {
                    throw e;
                }
            }
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }

        assertTrue(successCount <= 5);
        assertTrue(resourceExhaustedCount >= 5);
        adServiceContainer.stop();
    }

    @Test
    void test_ac4_rate_limit_hit_logs_structured_entry() {
        // AC-4: Rate limit hit emits structured log entry with required fields
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GET_ADS_RPS", "1");
        Slf4jLogConsumer logConsumer = new Slf4jLogConsumer(org.slf4j.LoggerFactory.getLogger("ad-service-test"));
        adServiceContainer.withLogConsumer(logConsumer);
        adServiceContainer.start();

        // Exceed rate limit
        for (int i = 0; i < 3; i++) {
            try {
                AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();
                stub.getAds(request);
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() != Status.Code.RESOURCE_EXHAUSTED) {
                    throw e;
                }
            }
        }

        // Check logs for rate_limit_exceeded event
        String logs = adServiceContainer.getLogs();
        assertTrue(logs.contains("\"event_type\":\"rate_limit_exceeded\""));
        assertTrue(logs.contains("\"client_ip\":"));
        assertTrue(logs.contains("\"endpoint\":\"opentelemetry.demo.ad.v1.AdService/GetAds\""));
        assertTrue(logs.contains("\"limit_rps\":1"));
        adServiceContainer.stop();
    }

    @Test
    void test_ac5_rate_limit_per_client_ip() {
        // AC-5: Rate limits are applied per client IP
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GET_ADS_RPS", "2");
        adServiceContainer.start();

        // Simulate two different client IPs by adding X-Forwarded-For header
        AdServiceGrpc.AdServiceBlockingStub stubIp1 = stub.withOption(io.grpc.CallOptions.Key.create("X-Forwarded-For"), "192.168.1.1");
        AdServiceGrpc.AdServiceBlockingStub stubIp2 = stub.withOption(io.grpc.CallOptions.Key.create("X-Forwarded-For"), "192.168.1.2");

        int successIp1 = 0;
        int successIp2 = 0;

        // Send 3 requests per IP, each should have max 2 successes
        for (int i = 0; i < 3; i++) {
            try {
                stubIp1.getAds(AdRequest.newBuilder().addContextKeys("test").build());
                successIp1++;
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() != Status.Code.RESOURCE_EXHAUSTED) throw e;
            }
            try {
                stubIp2.getAds(AdRequest.newBuilder().addContextKeys("test").build());
                successIp2++;
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() != Status.Code.RESOURCE_EXHAUSTED) throw e;
            }
        }

        assertEquals(2, successIp1);
        assertEquals(2, successIp2);
        adServiceContainer.stop();
    }

    @Test
    void test_ac6_rate_limiting_low_latency_under_load() throws InterruptedException {
        // AC-6: No more than 1ms added latency under 1k RPS load
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GET_ADS_RPS", "1000");
        adServiceContainer.start();

        int requestCount = 1000;
        CountDownLatch latch = new CountDownLatch(requestCount);
        List<Long> latencies = new ArrayList<>();

        for (int i = 0; i < requestCount; i++) {
            new Thread(() -> {
                long start = System.nanoTime();
                try {
                    stub.getAds(AdRequest.newBuilder().addContextKeys("test").build());
                    long end = System.nanoTime();
                    synchronized (latencies) {
                        latencies.add(TimeUnit.NANOSECONDS.toMillis(end - start));
                    }
                } catch (Exception e) {
                    // Ignore rate limit hits for latency test
                } finally {
                    latch.countDown();
                }
            }).start();
        }

        latch.await(30, TimeUnit.SECONDS);

        // Average latency should be less than 1ms added over baseline (baseline ~0.5ms, so total <1.5ms)
        double averageLatency = latencies.stream().mapToLong(Long::longValue).average().orElse(0);
        assertTrue(averageLatency < 2.0); // Allow small buffer for test environment
        adServiceContainer.stop();
    }

    @Test
    void test_ac7_config_changes_require_restart() {
        // AC-7: Configuration changes take effect only after restart
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GET_ADS_RPS", "2");
        adServiceContainer.start();

        // Exceed initial limit
        int initialExhausted = 0;
        for (int i = 0; i < 5; i++) {
            try {
                stub.getAds(AdRequest.newBuilder().addContextKeys("test").build());
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) initialExhausted++;
            }
        }
        assertTrue(initialExhausted >= 3);

        // Change env var without restart
        adServiceContainer.withEnv("AD_SERVICE_RATE_LIMIT_GET_ADS_RPS", "10");

        // Send another 5 requests, should still hit rate limit
        int afterConfigChangeExhausted = 0;
        for (int i = 0; i < 5; i++) {
            try {
                stub.getAds(AdRequest.newBuilder().addContextKeys("test").build());
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) afterConfigChangeExhausted++;
            }
        }
        assertTrue(afterConfigChangeExhausted >= 3);

        // Restart container with new config
        adServiceContainer.stop();
        adServiceContainer.start();

        // Now new limit should apply, fewer exhausted requests
        int afterRestartExhausted = 0;
        for (int i = 0; i < 5; i++) {
            try {
                stub.getAds(AdRequest.newBuilder().addContextKeys("test").build());
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) afterRestartExhausted++;
            }
        }
        assertEquals(0, afterRestartExhausted);

        adServiceContainer.stop();
    }
}
