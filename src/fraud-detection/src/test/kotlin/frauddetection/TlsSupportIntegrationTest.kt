package frauddetection

import io.grpc.ManagedChannel
import io.grpc.ManagedChannelBuilder
import io.grpc.StatusRuntimeException
import io.grpc.health.v1.HealthCheckRequest
import io.grpc.health.v1.HealthCheckResponse
import io.grpc.health.v1.HealthGrpc
import io.grpc.netty.GrpcSslContexts
import io.netty.handler.ssl.SslContextBuilder
import org.awaitility.kotlin.await
import org.awaitility.kotlin.until
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import java.io.File
import java.util.concurrent.TimeUnit

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class TlsSupportIntegrationTest {
    private lateinit var channel: ManagedChannel
    private lateinit var stub: HealthGrpc.HealthBlockingStub

    @AfterEach
    fun cleanup() {
        if (::channel.isInitialized) {
            channel.shutdown()
            channel.awaitTermination(5, TimeUnit.SECONDS)
        }
    }

    @Test
    fun `test_ac1_default_config_starts_with_plaintext_grpc_and_kafka`() {
        // Start service with all default TLS env vars unset
        val serviceProcess = startFraudDetectionService()

        try {
            // Wait for service to be available on plaintext gRPC port
            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9091)
                        .usePlaintext()
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    stub.check(HealthCheckRequest.getDefaultInstance()).status == HealthCheckResponse.ServingStatus.SERVING
                } catch (e: Exception) {
                    false
                }
            }

            // Verify service started successfully (no startup failure)
            assertTrue(serviceProcess.isAlive)
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    @Test
    fun `test_ac2_grpc_tls_enabled_rejects_plaintext_when_plaintext_disabled`() {
        // Load test certificates (these are test-only fixtures that will be added to test resources)
        val serverCert = File("src/test/resources/tls/server.crt").absolutePath
        val serverKey = File("src/test/resources/tls/server.key").absolutePath

        val env = mapOf(
            "FRD_GRPC_TLS_ENABLED" to "true",
            "FRD_GRPC_TLS_CERT_PATH" to serverCert,
            "FRD_GRPC_TLS_KEY_PATH" to serverKey
        )

        val serviceProcess = startFraudDetectionService(env)

        try {
            // Verify plaintext connection fails
            assertThrows<Exception> {
                val plaintextChannel = ManagedChannelBuilder.forAddress("localhost", 9091)
                    .usePlaintext()
                    .build()
                val plaintextStub = HealthGrpc.newBlockingStub(plaintextChannel)
                plaintextStub.check(HealthCheckRequest.getDefaultInstance())
            }

            // Verify TLS connection succeeds on TLS port (default gRPC TLS port 9092)
            val sslContext = GrpcSslContexts.forClient()
                .trustManager(File(serverCert))
                .build()

            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9092)
                        .sslContext(sslContext)
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    stub.check(HealthCheckRequest.getDefaultInstance()).status == HealthCheckResponse.ServingStatus.SERVING
                } catch (e: Exception) {
                    false
                }
            }
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    @Test
    fun `test_ac3_mtls_enabled_rejects_clients_without_valid_certificates`() {
        val serverCert = File("src/test/resources/tls/server.crt").absolutePath
        val serverKey = File("src/test/resources/tls/server.key").absolutePath
        val caCert = File("src/test/resources/tls/ca.crt").absolutePath
        val validClientCert = File("src/test/resources/tls/client.valid.crt").absolutePath
        val validClientKey = File("src/test/resources/tls/client.valid.key").absolutePath
        val invalidClientCert = File("src/test/resources/tls/client.invalid.crt").absolutePath
        val invalidClientKey = File("src/test/resources/tls/client.invalid.key").absolutePath

        val env = mapOf(
            "FRD_GRPC_TLS_ENABLED" to "true",
            "FRD_GRPC_TLS_CERT_PATH" to serverCert,
            "FRD_GRPC_TLS_KEY_PATH" to serverKey,
            "FRD_GRPC_MTLS_ENABLED" to "true",
            "FRD_GRPC_TLS_CA_CERT_PATH" to caCert
        )

        val serviceProcess = startFraudDetectionService(env)

        try {
            // Verify connection without client cert fails
            val noClientCertSslContext = GrpcSslContexts.forClient()
                .trustManager(File(caCert))
                .build()

            assertThrows<StatusRuntimeException> {
                channel = ManagedChannelBuilder.forAddress("localhost", 9092)
                    .sslContext(noClientCertSslContext)
                    .build()
                stub = HealthGrpc.newBlockingStub(channel)
                stub.check(HealthCheckRequest.getDefaultInstance())
            }

            // Verify connection with invalid client cert fails
            val invalidClientSslContext = GrpcSslContexts.forClient()
                .trustManager(File(caCert))
                .keyManager(File(invalidClientCert), File(invalidClientKey))
                .build()

            assertThrows<StatusRuntimeException> {
                channel = ManagedChannelBuilder.forAddress("localhost", 9092)
                    .sslContext(invalidClientSslContext)
                    .build()
                stub = HealthGrpc.newBlockingStub(channel)
                stub.check(HealthCheckRequest.getDefaultInstance())
            }

            // Verify connection with valid client cert succeeds
            val validClientSslContext = GrpcSslContexts.forClient()
                .trustManager(File(caCert))
                .keyManager(File(validClientCert), File(validClientKey))
                .build()

            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9092)
                        .sslContext(validClientSslContext)
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    stub.check(HealthCheckRequest.getDefaultInstance()).status == HealthCheckResponse.ServingStatus.SERVING
                } catch (e: Exception) {
                    false
                }
            }
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    @Test
    fun `test_ac4_kafka_tls_enabled_establishes_encrypted_connection`() {
        val kafkaCert = File("src/test/resources/tls/kafka.crt").absolutePath
        val kafkaKey = File("src/test/resources/tls/kafka.key").absolutePath
        val kafkaCaCert = File("src/test/resources/tls/kafka-ca.crt").absolutePath

        // Start Kafka container with TLS enabled
        val kafkaContainer = startKafkaContainerWithTls(kafkaCert, kafkaKey, kafkaCaCert)

        val env = mapOf(
            "KAFKA_TLS_ENABLED" to "true",
            "KAFKA_TLS_CERT_PATH" to kafkaCert,
            "KAFKA_TLS_KEY_PATH" to kafkaKey,
            "KAFKA_TLS_CA_PATH" to kafkaCaCert,
            "KAFKA_BROKER" to kafkaContainer.bootstrapServers
        )

        val serviceProcess = startFraudDetectionService(env)

        try {
            // Wait for service to become healthy (indicating successful Kafka TLS connection)
            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9091)
                        .usePlaintext()
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    stub.check(HealthCheckRequest.getDefaultInstance()).status == HealthCheckResponse.ServingStatus.SERVING
                } catch (e: Exception) {
                    false
                }
            }
        } finally {
            stopFraudDetectionService(serviceProcess)
            kafkaContainer.stop()
        }
    }

    @Test
    fun `test_ac5_invalid_tls_config_causes_fast_startup_failure`() {
        // Test case: gRPC TLS enabled but missing cert path
        val envMissingCert = mapOf(
            "GRPC_TLS_ENABLED" to "true",
            "GRPC_TLS_KEY_PATH" to "/invalid/path/key.pem"
        )

        val processMissingCert = startFraudDetectionService(envMissingCert)
        processMissingCert.waitFor(10, TimeUnit.SECONDS)
        assertFalse(processMissingCert.isAlive)
        assertNotEquals(0, processMissingCert.exitValue())

        // Test case: Kafka TLS enabled but invalid CA file
        val envInvalidCa = mapOf(
            "KAFKA_TLS_ENABLED" to "true",
            "KAFKA_TLS_CA_PATH" to "/nonexistent/ca.pem"
        )

        val processInvalidCa = startFraudDetectionService(envInvalidCa)
        processInvalidCa.waitFor(10, TimeUnit.SECONDS)
        assertFalse(processInvalidCa.isAlive)
        assertNotEquals(0, processInvalidCa.exitValue())
    }

    @Test
    fun `test_ac6_grpc_tls_and_plaintext_enabled_listens_on_both_ports`() {
        val serverCert = File("src/test/resources/tls/server.crt").absolutePath
        val serverKey = File("src/test/resources/tls/server.key").absolutePath

        val env = mapOf(
            "GRPC_TLS_ENABLED" to "true",
            "GRPC_TLS_CERT_PATH" to serverCert,
            "GRPC_TLS_KEY_PATH" to serverKey,
            "GRPC_PLAINTEXT_ENABLED" to "true"
        )

        val serviceProcess = startFraudDetectionService(env)

        try {
            // Verify plaintext connection works
            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9091)
                        .usePlaintext()
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    stub.check(HealthCheckRequest.getDefaultInstance()).status == HealthCheckResponse.ServingStatus.SERVING
                } catch (e: Exception) {
                    false
                }
            }

            // Verify TLS connection works
            val sslContext = GrpcSslContexts.forClient()
                .trustManager(File(serverCert))
                .build()

            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9092)
                        .sslContext(sslContext)
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    stub.check(HealthCheckRequest.getDefaultInstance()).status == HealthCheckResponse.ServingStatus.SERVING
                } catch (e: Exception) {
                    false
                }
            }
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    // Helper methods (stubs matching existing test framework pattern)
    private fun startFraudDetectionService(env: Map<String, String> = emptyMap()): Process {
        TODO("Test setup implementation")
    }

    private fun stopFraudDetectionService(process: Process) {
        process.destroyForcibly()
        process.waitFor(5, TimeUnit.SECONDS)
    }

    private fun startKafkaContainerWithTls(certPath: String, keyPath: String, caPath: String): KafkaTlsContainer {
        TODO("Test setup implementation")
    }

    // Stub class for TLS-enabled Kafka container
    class KafkaTlsContainer {
        val bootstrapServers: String = "localhost:9093"
        fun stop() {}
    }
}
