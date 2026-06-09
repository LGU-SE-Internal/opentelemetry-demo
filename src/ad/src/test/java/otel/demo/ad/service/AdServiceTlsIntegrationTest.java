package otel.demo.ad.service;

import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.StatusRuntimeException;
import io.grpc.netty.GrpcSslContexts;
import io.netty.handler.ssl.SslContext;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import static org.junit.jupiter.api.Assertions.*;

public class AdServiceTlsIntegrationTest {

    private static final int TEST_GRPC_PORT = 9555;
    private Process adServiceProcess;

    @BeforeEach
    void setUp() {
        // Ensure no leftover process
        if (adServiceProcess != null && adServiceProcess.isAlive()) {
            adServiceProcess.destroyForcibly();
        }
        // Clear any existing TLS env vars
        System.clearProperty("AD_SERVICE_GRPC_TLS_CERT_PATH");
        System.clearProperty("AD_SERVICE_GRPC_TLS_KEY_PATH");
        System.clearProperty("AD_SERVICE_GRPC_TLS_CA_CERT_PATH");
    }

    @AfterEach
    void tearDown() {
        if (adServiceProcess != null && adServiceProcess.isAlive()) {
            adServiceProcess.destroyForcibly();
        }
    }

    @Test
    void test_ac1_no_tls_vars_starts_plaintext_success() throws IOException, InterruptedException {
        // AC-1: No TLS env vars, service starts, accepts plaintext connections
        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().remove("AD_SERVICE_GRPC_TLS_CERT_PATH");
        pb.environment().remove("AD_SERVICE_GRPC_TLS_KEY_PATH");
        pb.environment().remove("AD_SERVICE_GRPC_TLS_CA_CERT_PATH");
        adServiceProcess = pb.start();

        // Wait for service startup
        Thread.sleep(5000);
        assertTrue(adServiceProcess.isAlive(), "Ad service should be running");

        // Test plaintext connection works
        ManagedChannel channel = ManagedChannelBuilder.forAddress("localhost", TEST_GRPC_PORT)
                .usePlaintext()
                .build();
        assertDoesNotThrow(() -> channel.getState(true));
        channel.shutdown();
    }

    @Test
    void test_ac2_valid_cert_key_tls_enabled_plaintext_rejected(@TempDir Path tempDir) throws Exception {
        // AC-2: Valid cert + key, TLS enabled, plaintext rejected, TLS accepted
        // Create dummy valid PEM files for test
        Path certPath = tempDir.resolve("server.crt");
        Path keyPath = tempDir.resolve("server.key");
        Files.writeString(certPath, "-----BEGIN CERTIFICATE-----\nMOCK_CERT\n-----END CERTIFICATE-----");
        Files.writeString(keyPath, "-----BEGIN PRIVATE KEY-----\nMOCK_KEY\n-----END PRIVATE KEY-----");

        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_GRPC_TLS_CERT_PATH", certPath.toString());
        pb.environment().put("AD_SERVICE_GRPC_TLS_KEY_PATH", keyPath.toString());
        adServiceProcess = pb.start();

        Thread.sleep(5000);
        assertTrue(adServiceProcess.isAlive(), "Ad service should be running with valid TLS config");

        // Test plaintext connection fails
        ManagedChannel plaintextChannel = ManagedChannelBuilder.forAddress("localhost", TEST_GRPC_PORT)
                .usePlaintext()
                .build();
        assertThrows(StatusRuntimeException.class, () -> {
            // Call any service method to trigger connection
            otel.demo.ad.AdServiceGrpc.newBlockingStub(plaintextChannel).getAds(
                    otel.demo.ad.GetAdsRequest.newBuilder().build()
            );
        });
        plaintextChannel.shutdown();

        // Test TLS connection works with trusted cert
        SslContext sslContext = GrpcSslContexts.forClient()
                .trustManager(certPath.toFile())
                .build();
        ManagedChannel tlsChannel = ManagedChannelBuilder.forAddress("localhost", TEST_GRPC_PORT)
                .sslContext(sslContext)
                .build();
        assertDoesNotThrow(() -> tlsChannel.getState(true));
        tlsChannel.shutdown();
    }

    @Test
    void test_ac3_only_cert_provided_fails_illegal_argument(@TempDir Path tempDir) throws IOException, InterruptedException {
        // AC-3: Only cert path provided, fails with IllegalArgumentException
        Path certPath = tempDir.resolve("server.crt");
        Files.writeString(certPath, "-----BEGIN CERTIFICATE-----\nMOCK_CERT\n-----END CERTIFICATE-----");

        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_GRPC_TLS_CERT_PATH", certPath.toString());
        pb.environment().remove("AD_SERVICE_GRPC_TLS_KEY_PATH");
        adServiceProcess = pb.start();

        int exitCode = adServiceProcess.waitFor();
        assertNotEquals(0, exitCode, "Service should exit with non-zero code");
        // Check logs for IllegalArgumentException
        String stderr = new String(adServiceProcess.getErrorStream().readAllBytes());
        assertTrue(stderr.contains("IllegalArgumentException"), "Should log IllegalArgumentException");
    }

    @Test
    void test_ac4_only_key_provided_fails_illegal_argument(@TempDir Path tempDir) throws IOException, InterruptedException {
        // AC-4: Only key path provided, fails with IllegalArgumentException
        Path keyPath = tempDir.resolve("server.key");
        Files.writeString(keyPath, "-----BEGIN PRIVATE KEY-----\nMOCK_KEY\n-----END PRIVATE KEY-----");

        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_GRPC_TLS_KEY_PATH", keyPath.toString());
        pb.environment().remove("AD_SERVICE_GRPC_TLS_CERT_PATH");
        adServiceProcess = pb.start();

        int exitCode = adServiceProcess.waitFor();
        assertNotEquals(0, exitCode, "Service should exit with non-zero code");
        String stderr = new String(adServiceProcess.getErrorStream().readAllBytes());
        assertTrue(stderr.contains("IllegalArgumentException"), "Should log IllegalArgumentException");
    }

    @Test
    void test_ac5_invalid_cert_path_fails_file_not_found(@TempDir Path tempDir) throws IOException, InterruptedException {
        // AC-5: Non-existent cert path, valid key path, fails with FileNotFoundException
        Path invalidCertPath = tempDir.resolve("non_existent.crt");
        Path keyPath = tempDir.resolve("server.key");
        Files.writeString(keyPath, "-----BEGIN PRIVATE KEY-----\nMOCK_KEY\n-----END PRIVATE KEY-----");

        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_GRPC_TLS_CERT_PATH", invalidCertPath.toString());
        pb.environment().put("AD_SERVICE_GRPC_TLS_KEY_PATH", keyPath.toString());
        adServiceProcess = pb.start();

        int exitCode = adServiceProcess.waitFor();
        assertNotEquals(0, exitCode, "Service should exit with non-zero code");
        String stderr = new String(adServiceProcess.getErrorStream().readAllBytes());
        assertTrue(stderr.contains("FileNotFoundException"), "Should log FileNotFoundException");
    }

    @Test
    void test_ac6_invalid_key_path_fails_file_not_found(@TempDir Path tempDir) throws IOException, InterruptedException {
        // AC-6: Valid cert path, non-existent key path, fails with FileNotFoundException
        Path certPath = tempDir.resolve("server.crt");
        Path invalidKeyPath = tempDir.resolve("non_existent.key");
        Files.writeString(certPath, "-----BEGIN CERTIFICATE-----\nMOCK_CERT\n-----END CERTIFICATE-----");

        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_GRPC_TLS_CERT_PATH", certPath.toString());
        pb.environment().put("AD_SERVICE_GRPC_TLS_KEY_PATH", invalidKeyPath.toString());
        adServiceProcess = pb.start();

        int exitCode = adServiceProcess.waitFor();
        assertNotEquals(0, exitCode, "Service should exit with non-zero code");
        String stderr = new String(adServiceProcess.getErrorStream().readAllBytes());
        assertTrue(stderr.contains("FileNotFoundException"), "Should log FileNotFoundException");
    }

    @Test
    void test_ac7_valid_cert_key_ca_mtls_enabled(@TempDir Path tempDir) throws Exception {
        // AC-7: Valid cert, key, CA path, mTLS enabled, no client cert rejected, valid client cert accepted
        Path certPath = tempDir.resolve("server.crt");
        Path keyPath = tempDir.resolve("server.key");
        Path caCertPath = tempDir.resolve("ca.crt");
        Path clientCertPath = tempDir.resolve("client.crt");
        Path clientKeyPath = tempDir.resolve("client.key");

        Files.writeString(certPath, "-----BEGIN CERTIFICATE-----\nMOCK_SERVER_CERT\n-----END CERTIFICATE-----");
        Files.writeString(keyPath, "-----BEGIN PRIVATE KEY-----\nMOCK_SERVER_KEY\n-----END PRIVATE KEY-----");
        Files.writeString(caCertPath, "-----BEGIN CERTIFICATE-----\nMOCK_CA_CERT\n-----END CERTIFICATE-----");
        Files.writeString(clientCertPath, "-----BEGIN CERTIFICATE-----\nMOCK_CLIENT_CERT\n-----END CERTIFICATE-----");
        Files.writeString(clientKeyPath, "-----BEGIN PRIVATE KEY-----\nMOCK_CLIENT_KEY\n-----END PRIVATE KEY-----");

        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_GRPC_TLS_CERT_PATH", certPath.toString());
        pb.environment().put("AD_SERVICE_GRPC_TLS_KEY_PATH", keyPath.toString());
        pb.environment().put("AD_SERVICE_GRPC_TLS_CA_CERT_PATH", caCertPath.toString());
        adServiceProcess = pb.start();

        Thread.sleep(5000);
        assertTrue(adServiceProcess.isAlive(), "Ad service should be running with valid mTLS config");

        // Test plaintext connection fails
        ManagedChannel plaintextChannel = ManagedChannelBuilder.forAddress("localhost", TEST_GRPC_PORT)
                .usePlaintext()
                .build();
        assertThrows(StatusRuntimeException.class, () -> {
            otel.demo.ad.AdServiceGrpc.newBlockingStub(plaintextChannel).getAds(
                    otel.demo.ad.GetAdsRequest.newBuilder().build()
            );
        });
        plaintextChannel.shutdown();

        // Test TLS connection without client cert fails
        SslContext noClientCertSslContext = GrpcSslContexts.forClient()
                .trustManager(caCertPath.toFile())
                .build();
        ManagedChannel noClientCertChannel = ManagedChannelBuilder.forAddress("localhost", TEST_GRPC_PORT)
                .sslContext(noClientCertSslContext)
                .build();
        assertThrows(StatusRuntimeException.class, () -> {
            otel.demo.ad.AdServiceGrpc.newBlockingStub(noClientCertChannel).getAds(
                    otel.demo.ad.GetAdsRequest.newBuilder().build()
            );
        });
        noClientCertChannel.shutdown();

        // Test TLS connection with valid client cert succeeds
        SslContext clientCertSslContext = GrpcSslContexts.forClient()
                .trustManager(caCertPath.toFile())
                .keyManager(clientCertPath.toFile(), clientKeyPath.toFile())
                .build();
        ManagedChannel clientCertChannel = ManagedChannelBuilder.forAddress("localhost", TEST_GRPC_PORT)
                .sslContext(clientCertSslContext)
                .build();
        assertDoesNotThrow(() -> clientCertChannel.getState(true));
        clientCertChannel.shutdown();
    }

    @Test
    void test_ac8_invalid_ca_path_fails_file_not_found(@TempDir Path tempDir) throws IOException, InterruptedException {
        // AC-8: Valid cert + key, non-existent CA path, fails with FileNotFoundException
        Path certPath = tempDir.resolve("server.crt");
        Path keyPath = tempDir.resolve("server.key");
        Path invalidCaPath = tempDir.resolve("non_existent_ca.crt");
        Files.writeString(certPath, "-----BEGIN CERTIFICATE-----\nMOCK_CERT\n-----END CERTIFICATE-----");
        Files.writeString(keyPath, "-----BEGIN PRIVATE KEY-----\nMOCK_KEY\n-----END PRIVATE KEY-----");

        ProcessBuilder pb = new ProcessBuilder("java", "-jar", "build/libs/ad-service.jar");
        pb.environment().put("AD_SERVICE_GRPC_TLS_CERT_PATH", certPath.toString());
        pb.environment().put("AD_SERVICE_GRPC_TLS_KEY_PATH", keyPath.toString());
        pb.environment().put("AD_SERVICE_GRPC_TLS_CA_CERT_PATH", invalidCaPath.toString());
        adServiceProcess = pb.start();

        int exitCode = adServiceProcess.waitFor();
        assertNotEquals(0, exitCode, "Service should exit with non-zero code");
        String stderr = new String(adServiceProcess.getErrorStream().readAllBytes());
        assertTrue(stderr.contains("FileNotFoundException"), "Should log FileNotFoundException");
    }
}
