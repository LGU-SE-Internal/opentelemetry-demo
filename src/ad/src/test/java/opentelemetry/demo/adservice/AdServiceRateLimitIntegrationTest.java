/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package opentelemetry.demo.adservice;

import io.grpc.*;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.stub.StreamObserver;
import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.Refill;
import org.apache.logging.log4j.Level;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.core.Appender;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.LoggerContext;
import org.apache.logging.log4j.core.appender.AbstractAppender;
import org.apache.logging.log4j.core.config.Configuration;
import org.apache.logging.log4j.core.config.LoggerConfig;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import oteldemo.AdServiceGrpc;
import oteldemo.Demo.AdRequest;
import oteldemo.Demo.AdResponse;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

public class AdServiceRateLimitIntegrationTest {

    private Server server;
    private ManagedChannel channel;
    private AdServiceGrpc.AdServiceBlockingStub blockingStub;
    private AdServiceGrpc.AdServiceStub asyncStub;
    private TestAppender testAppender;

    @BeforeEach
    void setUp() throws Exception {
        String serverName = InProcessServerBuilder.generateName();
        // Reset system property before each test
        System.clearProperty("AD_SERVICE_RATE_LIMIT_RPS");

        // Create test appender for log verification
        testAppender = new TestAppender("TestAppender", null);
        testAppender.start();
        LoggerContext context = (LoggerContext) LogManager.getContext(false);
        Configuration config = context.getConfiguration();
        LoggerConfig rootConfig = config.getLoggerConfig(LogManager.ROOT_LOGGER_NAME);
        rootConfig.addAppender(testAppender, Level.WARN, null);
        context.updateLoggers();

        server = InProcessServerBuilder.forName(serverName)
                .intercept(new oteldemo.AdService.RateLimitInterceptor())
                .addService(new AdServiceImpl())
                .build()
                .start();

        channel = InProcessChannelBuilder.forName(serverName)
                .usePlaintext()
                .build();

        blockingStub = AdServiceGrpc.newBlockingStub(channel);
        asyncStub = AdServiceGrpc.newStub(channel);
    }

    @AfterEach
    void tearDown() throws Exception {
        channel.shutdownNow();
        server.shutdownNow();
        server.awaitTermination(5, TimeUnit.SECONDS);

        // Remove test appender
        LoggerContext context = (LoggerContext) LogManager.getContext(false);
        Configuration config = context.getConfiguration();
        LoggerConfig rootConfig = config.getLoggerConfig(LogManager.ROOT_LOGGER_NAME);
        rootConfig.removeAppender(testAppender.getName());
        testAppender.stop();
        context.updateLoggers();
    }

    @Test
    void test_ac1_default_rate_limit_100_rps_without_env_var() {
        // Default rate limit is 100 RPS
        int successCount = 0;
        int errorCount = 0;

        // Send 150 requests
        for (int i = 0; i < 150; i++) {
            try {
                blockingStub.getAds(AdRequest.newBuilder().build());
                successCount++;
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                    errorCount++;
                } else {
                    throw e;
                }
            }
        }

        assertEquals(100, successCount);
        assertEquals(50, errorCount);
    }

    @Test
    void test_ac2_custom_rate_limit_applied_when_env_var_set() {
        // Set custom rate limit of 10 RPS
        System.setProperty("AD_SERVICE_RATE_LIMIT_RPS", "10");

        // Create new server with custom rate limit
        String serverName = InProcessServerBuilder.generateName();
        Server customServer = InProcessServerBuilder.forName(serverName)
                .intercept(new oteldemo.AdService.RateLimitInterceptor())
                .addService(new AdServiceImpl())
                .build();
        try {
            customServer.start();
            ManagedChannel customChannel = InProcessChannelBuilder.forName(serverName).usePlaintext().build();
            AdServiceGrpc.AdServiceBlockingStub customStub = AdServiceGrpc.newBlockingStub(customChannel);

            int successCount = 0;
            int errorCount = 0;

            // Send 20 requests
            for (int i = 0; i < 20; i++) {
                try {
                    customStub.getAds(AdRequest.newBuilder().build());
                    successCount++;
                } catch (StatusRuntimeException e) {
                    if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                        errorCount++;
                    } else {
                        throw e;
                    }
                }
            }

            assertEquals(10, successCount);
            assertEquals(10, errorCount);
            customChannel.shutdownNow();
        } finally {
            customServer.shutdownNow();
        }
    }

    @Test
    void test_ac3_excess_requests_return_resource_exhausted_error() {
        System.setProperty("AD_SERVICE_RATE_LIMIT_RPS", "5");

        String serverName = InProcessServerBuilder.generateName();
        Server customServer = InProcessServerBuilder.forName(serverName)
                .intercept(new oteldemo.AdService.RateLimitInterceptor())
                .addService(new AdServiceImpl())
                .build();
        try {
            customServer.start();
            ManagedChannel customChannel = InProcessChannelBuilder.forName(serverName).usePlaintext().build();
            AdServiceGrpc.AdServiceBlockingStub customStub = AdServiceGrpc.newBlockingStub(customChannel);

            // Send 10 requests
            for (int i = 0; i < 10; i++) {
                try {
                    customStub.getAds(AdRequest.newBuilder().build());
                    if (i >= 5) {
                        fail("Expected RESOURCE_EXHAUSTED error for request " + (i + 1));
                    }
                } catch (StatusRuntimeException e) {
                    if (i < 5) {
                        throw e;
                    }
                    assertEquals(Status.Code.RESOURCE_EXHAUSTED, e.getStatus().getCode());
                    assertTrue(e.getStatus().getDescription().contains("Rate limit exceeded: too many requests from client IP"));
                }
            }
            customChannel.shutdownNow();
        } finally {
            customServer.shutdownNow();
        }
    }

    @Test
    void test_ac4_rate_limit_hits_generate_warn_log_entries() throws InterruptedException {
        System.setProperty("AD_SERVICE_RATE_LIMIT_RPS", "2");

        String serverName = InProcessServerBuilder.generateName();
        Server customServer = InProcessServerBuilder.forName(serverName)
                .intercept(new oteldemo.AdService.RateLimitInterceptor())
                .addService(new AdServiceImpl())
                .build();
        try {
            customServer.start();
            ManagedChannel customChannel = InProcessChannelBuilder.forName(serverName).usePlaintext().build();
            AdServiceGrpc.AdServiceBlockingStub customStub = AdServiceGrpc.newBlockingStub(customChannel);

            testAppender.getEvents().clear();

            // Send 5 requests
            for (int i = 0; i < 5; i++) {
                try {
                    customStub.getAds(AdRequest.newBuilder().build());
                } catch (StatusRuntimeException ignored) {
                }
            }

            // Should have 3 warn log entries
            List<LogEvent> warnEvents = testAppender.getEvents().stream()
                    .filter(e -> e.getLevel() == Level.WARN)
                    .filter(e -> e.getMessage().getFormattedMessage().contains("Rate limit exceeded"))
                    .toList();

            assertEquals(3, warnEvents.size());
            for (LogEvent event : warnEvents) {
                assertTrue(event.getMessage().getFormattedMessage().contains("client IP="));
                assertTrue(event.getMessage().getFormattedMessage().contains("method=oteldemo.AdService/GetAds"));
                assertTrue(event.getMessage().getFormattedMessage().contains("current requests="));
                assertTrue(event.getMessage().getFormattedMessage().contains("configured limit=2"));
            }
            customChannel.shutdownNow();
        } finally {
            customServer.shutdownNow();
        }
    }

    @Test
    void test_ac5_rate_limits_applied_independently_per_client_ip() {
        // Create interceptor that simulates different client IPs
        ServerInterceptor ipSimulatingInterceptor = new ServerInterceptor() {
            private final AtomicInteger counter = new AtomicInteger(0);
            @Override
            public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(ServerCall<ReqT, RespT> call, Metadata headers, ServerCallHandler<ReqT, RespT> next) {
                int clientNum = counter.getAndIncrement() % 3;
                String clientIp = "192.168.0." + (10 + clientNum);
                headers.put(Metadata.Key.of("X-Forwarded-For", Metadata.ASCII_STRING_MARSHALLER), clientIp);
                return next.startCall(call, headers);
            }
        };

        System.setProperty("AD_SERVICE_RATE_LIMIT_RPS", "2");

        String serverName = InProcessServerBuilder.generateName();
        Server customServer = InProcessServerBuilder.forName(serverName)
                .intercept(ipSimulatingInterceptor)
                .intercept(new oteldemo.AdService.RateLimitInterceptor())
                .addService(new AdServiceImpl())
                .build();
        try {
            customServer.start();
            ManagedChannel customChannel = InProcessChannelBuilder.forName(serverName).usePlaintext().build();
            AdServiceGrpc.AdServiceBlockingStub customStub = AdServiceGrpc.newBlockingStub(customChannel);

            // Send 9 requests (3 per client, each client has 2 RPS limit)
            int successCount = 0;
            int errorCount = 0;

            for (int i = 0; i < 9; i++) {
                try {
                    customStub.getAds(AdRequest.newBuilder().build());
                    successCount++;
                } catch (StatusRuntimeException e) {
                    if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                        errorCount++;
                    } else {
                        throw e;
                    }
                }
            }

            // 3 clients * 2 allowed requests = 6 success, 3 errors
            assertEquals(6, successCount);
            assertEquals(3, errorCount);
            customChannel.shutdownNow();
        } finally {
            customServer.shutdownNow();
        }
    }

    @Test
    void test_ac6_requests_under_limit_processed_normally() {
        System.setProperty("AD_SERVICE_RATE_LIMIT_RPS", "20");

        String serverName = InProcessServerBuilder.generateName();
        Server customServer = InProcessServerBuilder.forName(serverName)
                .intercept(new oteldemo.AdService.RateLimitInterceptor())
                .addService(new AdServiceImpl())
                .build();
        try {
            customServer.start();
            ManagedChannel customChannel = InProcessChannelBuilder.forName(serverName).usePlaintext().build();
            AdServiceGrpc.AdServiceBlockingStub customStub = AdServiceGrpc.newBlockingStub(customChannel);

            // Send 15 requests (all under limit)
            for (int i = 0; i < 15; i++) {
                AdResponse response = customStub.getAds(AdRequest.newBuilder().build());
                assertNotNull(response);
                assertFalse(response.getAdsList().isEmpty());
            }
            customChannel.shutdownNow();
        } finally {
            customServer.shutdownNow();
        }
    }

    // Simple AdService implementation for testing
    private static class AdServiceImpl extends AdServiceGrpc.AdServiceImplBase {
        @Override
        public void getAds(AdRequest request, StreamObserver<AdResponse> responseObserver) {
            AdResponse response = AdResponse.newBuilder()
                    .addAds(oteldemo.Demo.Ad.newBuilder()
                            .setRedirectUrl("/test")
                            .setText("Test Ad")
                            .build())
                    .build();
            responseObserver.onNext(response);
            responseObserver.onCompleted();
        }
    }

    // Test appender to capture log events
    private static class TestAppender extends AbstractAppender {
        private final List<LogEvent> events = new ArrayList<>();

        protected TestAppender(String name, org.apache.logging.log4j.core.Filter filter) {
            super(name, filter, null, true, null);
        }

        @Override
        public void append(LogEvent event) {
            events.add(event.toImmutable());
        }

        public List<LogEvent> getEvents() {
            return events;
        }
    }
}
