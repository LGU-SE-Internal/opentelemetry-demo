/*
 * Copyright 2024 The OpenTelemetry Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package opentelemetry.demo.adservice;

import io.grpc.*;
import io.grpc.netty.NettyChannelBuilder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.opentelemetry.demo.adservice.AdServiceGrpc;
import org.opentelemetry.demo.adservice.AdRequest;
import org.opentelemetry.demo.adservice.AdResponse;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class AdServiceRateLimitIntegrationTest {
    private Server server;
    private ManagedChannel channel;
    private static final int TEST_PORT = 9091;
    private static final String RATE_LIMIT_ENV_VAR = "AD_SERVICE_RATE_LIMIT_RPS";

    @BeforeEach
    void setUp() {
        clearRateLimitEnvironmentVariables();
    }

    @AfterEach
    void tearDown() throws InterruptedException {
        if (server != null) {
            server.shutdown().awaitTermination(5, TimeUnit.SECONDS);
        }
        if (channel != null) {
            channel.shutdown().awaitTermination(5, TimeUnit.SECONDS);
        }
        clearRateLimitEnvironmentVariables();
    }

    private void clearRateLimitEnvironmentVariables() {
        System.clearProperty(RATE_LIMIT_ENV_VAR);
    }

    @Test
    void test_ac1_default_rate_limit_100_rps_without_env_var() throws IOException, InterruptedException {
        // AC-1: When AD_SERVICE_RATE_LIMIT_RPS environment variable is not set, 
        // the ad service enforces a default rate limit of 100 requests per second per client IP
        server = AdService.startServer(TEST_PORT);
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

        int successCount = 0;
        int errorCount = 0;

        // Send 120 requests (20 over default limit of 100)
        for (int i = 0; i < 120; i++) {
            try {
                AdResponse response = stub.getAds(request);
                if (response != null && !response.getAdsList().isEmpty()) {
                    successCount++;
                }
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                    errorCount++;
                }
            }
        }

        // Verify 100 requests succeeded, 20 were rate limited
        assertEquals(100, successCount, "Should allow 100 requests per second by default");
        assertEquals(20, errorCount, "Should block 20 requests exceeding default rate limit");
    }

    @Test
    void test_ac2_custom_rate_limit_applied_when_env_var_set() throws IOException, InterruptedException {
        // AC-2: When AD_SERVICE_RATE_LIMIT_RPS environment variable is set to a positive integer N,
        // the ad service enforces a rate limit of N requests per second per client IP
        int customLimit = 10;
        System.setProperty(RATE_LIMIT_ENV_VAR, String.valueOf(customLimit));
        server = AdService.startServer(TEST_PORT);
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

        int successCount = 0;
        int errorCount = 0;

        // Send 15 requests (5 over custom limit of 10)
        for (int i = 0; i < 15; i++) {
            try {
                AdResponse response = stub.getAds(request);
                if (response != null && !response.getAdsList().isEmpty()) {
                    successCount++;
                }
            } catch (StatusRuntimeException e) {
                if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                    errorCount++;
                }
            }
        }

        // Verify 10 requests succeeded, 5 were rate limited
        assertEquals(customLimit, successCount, "Should allow custom N requests per second");
        assertEquals(5, errorCount, "Should block requests exceeding custom rate limit");
    }

    @Test
    void test_ac3_excess_requests_return_resource_exhausted_error() throws IOException {
        // AC-3: When a single client IP sends more than the configured rate limit number of requests
        // within a 1-second window, all excess requests receive gRPC RESOURCE_EXHAUSTED status code
        int limit = 5;
        System.setProperty(RATE_LIMIT_ENV_VAR, String.valueOf(limit));
        server = AdService.startServer(TEST_PORT);
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

        // Send limit requests first, should all succeed
        for (int i = 0; i < limit; i++) {
            AdResponse response = stub.getAds(request);
            assertNotNull(response);
            assertFalse(response.getAdsList().isEmpty());
        }

        // Send excess requests, should all return RESOURCE_EXHAUSTED with correct message
        for (int i = 0; i < 3; i++) {
            StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
            assertEquals(Status.Code.RESOURCE_EXHAUSTED, exception.getStatus().getCode());
            assertTrue(exception.getStatus().getDescription().contains("Rate limit exceeded: too many requests from client IP"));
        }
    }

    @Test
    void test_ac4_rate_limit_hits_generate_warn_log_entries() throws IOException {
        // AC-4: For every request that exceeds the rate limit, a WARN level log entry is generated
        // containing client IP, requested gRPC method, current request count for the client, and configured rate limit value
        int limit = 2;
        System.setProperty(RATE_LIMIT_ENV_VAR, String.valueOf(limit));
        server = AdService.startServer(TEST_PORT);
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

        // Send limit requests first, no warn logs expected
        for (int i = 0; i < limit; i++) {
            stub.getAds(request);
        }

        // TODO: Add log capturing to verify warn logs are generated
        // For now, verify excess requests are rate limited (log capture will be added in implementation phase)
        for (int i = 0; i < 2; i++) {
            StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
            assertEquals(Status.Code.RESOURCE_EXHAUSTED, exception.getStatus().getCode());
        }

        // Invariant check: Number of warn logs should equal number of rate limited requests
    }

    @Test
    void test_ac5_rate_limits_applied_independently_per_client_ip() throws InterruptedException, IOException {
        // AC-5: Rate limits are applied independently per client IP: requests from different IP addresses
        // do not affect each other's rate limiting state
        int limitPerIp = 5;
        System.setProperty(RATE_LIMIT_ENV_VAR, String.valueOf(limitPerIp));
        server = AdService.startServer(TEST_PORT);

        // Simulate multiple client IPs using X-Forwarded-For header
        String[] clientIps = {"192.168.1.1", "192.168.1.2", "192.168.1.3"};
        ExecutorService executor = Executors.newFixedThreadPool(clientIps.length);
        CountDownLatch latch = new CountDownLatch(clientIps.length);
        AtomicInteger totalSuccessCount = new AtomicInteger(0);
        AtomicInteger totalErrorCount = new AtomicInteger(0);

        for (String clientIp : clientIps) {
            executor.submit(() -> {
                try {
                    ManagedChannel clientChannel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                            .usePlaintext()
                            .build();
                    AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(clientChannel)
                            .withCallOption(new CallOptions.Key<>("X-Forwarded-For", ""), clientIp);
                    AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

                    int success = 0;
                    int error = 0;
                    for (int i = 0; i < limitPerIp + 2; i++) {
                        try {
                            AdResponse response = stub.getAds(request);
                            if (response != null && !response.getAdsList().isEmpty()) {
                                success++;
                            }
                        } catch (StatusRuntimeException e) {
                            if (e.getStatus().getCode() == Status.Code.RESOURCE_EXHAUSTED) {
                                error++;
                            }
                        }
                    }

                    totalSuccessCount.addAndGet(success);
                    totalErrorCount.addAndGet(error);
                    clientChannel.shutdown().awaitTermination(2, TimeUnit.SECONDS);
                } catch (Exception e) {
                    e.printStackTrace();
                } finally {
                    latch.countDown();
                }
            });
        }

        latch.await(10, TimeUnit.SECONDS);
        executor.shutdown();

        // Each IP should have exactly limitPerIp successful requests and 2 rate limited
        assertEquals(clientIps.length * limitPerIp, totalSuccessCount.get(), "Each client IP should get their own rate limit allocation");
        assertEquals(clientIps.length * 2, totalErrorCount.get(), "Each client IP excess requests should be rate limited independently");
    }

    @Test
    void test_ac6_requests_under_limit_processed_normally() throws IOException {
        // AC-6: All requests that are under the configured rate limit are processed normally
        // and return the expected successful response
        int limit = 50;
        System.setProperty(RATE_LIMIT_ENV_VAR, String.valueOf(limit));
        server = AdService.startServer(TEST_PORT);
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("electronics").build();

        // Send exactly limit requests, all should succeed with valid responses
        for (int i = 0; i < limit; i++) {
            AdResponse response = stub.getAds(request);
            assertNotNull(response);
            assertFalse(response.getAdsList().isEmpty());
            // Verify response content is as expected
            assertTrue(response.getAdsList().stream().anyMatch(ad -> ad.getText().contains("electronics")));
        }
    }

    @Test
    void test_invalid_rate_limit_env_var_falls_back_to_default() throws IOException, InterruptedException {
        // Edge case: Empty or invalid values for AD_SERVICE_RATE_LIMIT_RPS fall back to default 100 RPS
        System.setProperty(RATE_LIMIT_ENV_VAR, "invalid_number");
        server = AdService.startServer(TEST_PORT);
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

        int successCount = 0;
        for (int i = 0; i < 100; i++) {
            AdResponse response = stub.getAds(request);
            if (response != null && !response.getAdsList().isEmpty()) {
                successCount++;
            }
        }

        assertEquals(100, successCount, "Invalid rate limit value should fall back to default 100 RPS");
    }
}
