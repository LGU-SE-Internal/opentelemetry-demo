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
import io.grpc.netty.GrpcSslContexts;
import io.grpc.netty.NettyChannelBuilder;
import io.grpc.stub.StreamObserver;
import io.netty.handler.ssl.SslContext;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.opentelemetry.demo.adservice.AdServiceGrpc;
import org.opentelemetry.demo.adservice.AdRequest;
import org.opentelemetry.demo.adservice.AdResponse;
import java.io.File;
import java.io.IOException;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

class AdServiceTlsIntegrationTest {
    private Server server;
    private ManagedChannel channel;
    private static final int TEST_PORT = 9090;
    private static final String TEST_CERT_PATH = "src/test/resources/tls/test-server.crt";
    private static final String TEST_KEY_PATH = "src/test/resources/tls/test-server.key";
    private static final String TEST_CA_CERT_PATH = "src/test/resources/tls/test-ca.crt";
    private static final String TEST_CLIENT_CERT_PATH = "src/test/resources/tls/test-client.crt";
    private static final String TEST_CLIENT_KEY_PATH = "src/test/resources/tls/test-client.key";
    private static final String INVALID_PATH = "/nonexistent/path/file.pem";
    private static final String MALFORMED_CERT_PATH = "src/test/resources/tls/malformed.crt";

    @BeforeEach
    void setUp() {
        clearTlsEnvironmentVariables();
    }

    @AfterEach
    void tearDown() throws InterruptedException {
        if (server != null) {
            server.shutdown().awaitTermination(5, TimeUnit.SECONDS);
        }
        if (channel != null) {
            channel.shutdown().awaitTermination(5, TimeUnit.SECONDS);
        }
        clearTlsEnvironmentVariables();
    }

    private void clearTlsEnvironmentVariables() {
        System.clearProperty("AD_SERVICE_TLS_ENABLED");
        System.clearProperty("AD_SERVICE_TLS_CERT_PATH");
        System.clearProperty("AD_SERVICE_TLS_KEY_PATH");
        System.clearProperty("AD_SERVICE_TLS_CLIENT_CA_CERT_PATH");
    }

    @Test
    void test_ac1_tls_disabled_works_with_plaintext_connections() throws IOException, InterruptedException {
        // AC-1: TLS disabled by default, plaintext connections work, no behavior changes
        System.setProperty("AD_SERVICE_TLS_ENABLED", "false");

        // Start server
        server = AdService.startServer(TEST_PORT);

        // Create plaintext channel
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();

        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);

        // Verify request works as expected
        AdRequest request = AdRequest.newBuilder()
                .addContextKeys("test")
                .build();
        AdResponse response = stub.getAds(request);

        assertNotNull(response);
        assertFalse(response.getAdsList().isEmpty());
    }

    @Test
    void test_ac2_tls_enabled_rejects_plaintext_connections() throws IOException {
        // AC-2: TLS enabled, only accepts TLS 1.2+ connections, rejects plaintext
        System.setProperty("AD_SERVICE_TLS_ENABLED", "true");
        System.setProperty("AD_SERVICE_TLS_CERT_PATH", TEST_CERT_PATH);
        System.setProperty("AD_SERVICE_TLS_KEY_PATH", TEST_KEY_PATH);

        // Start server
        server = AdService.startServer(TEST_PORT);

        // Attempt plaintext connection
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();

        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

        // Verify plaintext connection fails
        StatusRuntimeException exception = assertThrows(StatusRuntimeException.class, () -> stub.getAds(request));
        assertEquals(Status.Code.UNAVAILABLE, exception.getStatus().getCode());
    }

    @Test
    void test_ac3_tls_enabled_missing_cert_fails_to_start() {
        // AC-3: TLS enabled but missing cert/key fails to start with correct error
        System.setProperty("AD_SERVICE_TLS_ENABLED", "true");
        // Missing AD_SERVICE_TLS_CERT_PATH and AD_SERVICE_TLS_KEY_PATH

        Exception exception = assertThrows(RuntimeException.class, () -> AdService.startServer(TEST_PORT));
        assertTrue(exception.getMessage().contains("TLS enabled but required certificate/key path configuration is missing"));
    }

    @Test
    void test_ac3_tls_enabled_invalid_file_path_fails_to_start() {
        // AC-3: TLS enabled with invalid file path fails to start with correct error
        System.setProperty("AD_SERVICE_TLS_ENABLED", "true");
        System.setProperty("AD_SERVICE_TLS_CERT_PATH", INVALID_PATH);
        System.setProperty("AD_SERVICE_TLS_KEY_PATH", TEST_KEY_PATH);

        Exception exception = assertThrows(RuntimeException.class, () -> AdService.startServer(TEST_PORT));
        assertTrue(exception.getMessage().contains("Failed to read TLS file at " + INVALID_PATH));
    }

    @Test
    void test_ac3_tls_enabled_malformed_cert_fails_to_start() {
        // AC-3: TLS enabled with malformed cert fails to start with correct error
        System.setProperty("AD_SERVICE_TLS_ENABLED", "true");
        System.setProperty("AD_SERVICE_TLS_CERT_PATH", MALFORMED_CERT_PATH);
        System.setProperty("AD_SERVICE_TLS_KEY_PATH", TEST_KEY_PATH);

        Exception exception = assertThrows(RuntimeException.class, () -> AdService.startServer(TEST_PORT));
        assertTrue(exception.getMessage().contains("Invalid TLS configuration:"));
    }

    @Test
    void test_ac4_mtls_enabled_enforces_client_certificates() throws Exception {
        // AC-4: mTLS enabled, only accepts connections with valid client cert signed by CA
        System.setProperty("AD_SERVICE_TLS_ENABLED", "true");
        System.setProperty("AD_SERVICE_TLS_CERT_PATH", TEST_CERT_PATH);
        System.setProperty("AD_SERVICE_TLS_KEY_PATH", TEST_KEY_PATH);
        System.setProperty("AD_SERVICE_TLS_CLIENT_CA_CERT_PATH", TEST_CA_CERT_PATH);

        // Start server with mTLS enabled
        server = AdService.startServer(TEST_PORT);

        // 1. Test connection without client certificate should fail with UNAUTHENTICATED
        SslContext noClientCertSslContext = GrpcSslContexts.forClient()
                .trustManager(new File(TEST_CA_CERT_PATH))
                .build();

        channel = NettyChannelBuilder.forAddress("localhost", TEST_PORT)
                .sslContext(noClientCertSslContext)
                .build();

        AdServiceGrpc.AdServiceBlockingStub noClientCertStub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();

        StatusRuntimeException noClientCertException = assertThrows(StatusRuntimeException.class, () -> noClientCertStub.getAds(request));
        assertEquals(Status.Code.UNAUTHENTICATED, noClientCertException.getStatus().getCode());

        // 2. Test connection with valid client certificate should succeed
        SslContext validClientCertSslContext = GrpcSslContexts.forClient()
                .trustManager(new File(TEST_CA_CERT_PATH))
                .keyManager(new File(TEST_CLIENT_CERT_PATH), new File(TEST_CLIENT_KEY_PATH))
                .build();

        ManagedChannel validCertChannel = NettyChannelBuilder.forAddress("localhost", TEST_PORT)
                .sslContext(validClientCertSslContext)
                .build();

        AdServiceGrpc.AdServiceBlockingStub validClientCertStub = AdServiceGrpc.newBlockingStub(validCertChannel);
        AdResponse response = validClientCertStub.getAds(request);

        assertNotNull(response);
        assertFalse(response.getAdsList().isEmpty());

        validCertChannel.shutdown().awaitTermination(5, TimeUnit.SECONDS);
    }

    @Test
    void test_ac5_functionality_identical_with_and_without_tls() throws Exception {
        // AC-5: API functionality and payloads identical when TLS enabled vs disabled
        AdResponse plaintextResponse;
        // First get plaintext response
        System.setProperty("AD_SERVICE_TLS_ENABLED", "false");
        server = AdService.startServer(TEST_PORT);

        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub plaintextStub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("electronics").build();
        plaintextResponse = plaintextStub.getAds(request);

        tearDown();

        // Get TLS enabled response
        System.setProperty("AD_SERVICE_TLS_ENABLED", "true");
        System.setProperty("AD_SERVICE_TLS_CERT_PATH", TEST_CERT_PATH);
        System.setProperty("AD_SERVICE_TLS_KEY_PATH", TEST_KEY_PATH);
        server = AdService.startServer(TEST_PORT);

        SslContext sslContext = GrpcSslContexts.forClient()
                .trustManager(new File(TEST_CA_CERT_PATH))
                .build();
        channel = NettyChannelBuilder.forAddress("localhost", TEST_PORT)
                .sslContext(sslContext)
                .build();
        AdServiceGrpc.AdServiceBlockingStub tlsStub = AdServiceGrpc.newBlockingStub(channel);
        AdResponse tlsResponse = tlsStub.getAds(request);

        // Verify responses are identical
        assertEquals(plaintextResponse.getAdsCount(), tlsResponse.getAdsCount());
        for (int i = 0; i < plaintextResponse.getAdsCount(); i++) {
            assertEquals(plaintextResponse.getAds(i).getText(), tlsResponse.getAds(i).getText());
            assertEquals(plaintextResponse.getAds(i).getRedirectUrl(), tlsResponse.getAds(i).getRedirectUrl());
        }
    }

    @Test
    void test_ac6_default_configuration_matches_original_behavior() throws IOException {
        // AC-6: No TLS config provided, defaults to original unencrypted behavior
        // No TLS environment variables set at all

        // Start server should work without errors
        server = AdService.startServer(TEST_PORT);

        // Plaintext connection works
        channel = ManagedChannelBuilder.forAddress("localhost", TEST_PORT)
                .usePlaintext()
                .build();
        AdServiceGrpc.AdServiceBlockingStub stub = AdServiceGrpc.newBlockingStub(channel);
        AdRequest request = AdRequest.newBuilder().addContextKeys("test").build();
        AdResponse response = stub.getAds(request);

        assertNotNull(response);
        assertFalse(response.getAdsList().isEmpty());
    }
}
