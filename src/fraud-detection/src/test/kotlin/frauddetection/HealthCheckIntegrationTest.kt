package frauddetection

import io.grpc.ManagedChannel
import io.grpc.ManagedChannelBuilder
import io.grpc.health.v1.HealthCheckRequest
import io.grpc.health.v1.HealthCheckResponse
import io.grpc.health.v1.HealthGrpc
import org.awaitility.kotlin.await
import org.awaitility.kotlin.until
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import java.util.concurrent.TimeUnit
import kotlin.test.assertIs

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class HealthCheckIntegrationTest {
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
    fun `test_ac1_healthy_kafka_connection_returns_serving_status_on_default_port`() {
        // Start fraud detection service with healthy Kafka broker available
        val serviceProcess = startFraudDetectionService()

        try {
            // Wait for service to initialize and connect to Kafka
            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9091)
                        .usePlaintext()
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)

                    val response = stub.check(HealthCheckRequest.getDefaultInstance())
                    response.status == HealthCheckResponse.ServingStatus.SERVING
                } catch (e: Exception) {
                    false
                }
            }

            val response = stub.check(HealthCheckRequest.getDefaultInstance())
            assertEquals(HealthCheckResponse.ServingStatus.SERVING, response.status)
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    @Test
    fun `test_ac2_custom_health_port_configured_via_environment_variable`() {
        val customPort = 9092
        val env = mapOf("FRAUD_DETECTION_HEALTH_PORT" to customPort.toString())

        // Start service with custom port env var
        val serviceProcess = startFraudDetectionService(env)

        try {
            // Wait for service to be available on custom port
            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", customPort)
                        .usePlaintext()
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    stub.check(HealthCheckRequest.getDefaultInstance())
                    true
                } catch (e: Exception) {
                    false
                }
            }

            // Verify default port 9091 is NOT listening
            assertThrows<Exception> {
                val defaultChannel = ManagedChannelBuilder.forAddress("localhost", 9091)
                    .usePlaintext()
                    .build()
                val defaultStub = HealthGrpc.newBlockingStub(defaultChannel)
                defaultStub.check(HealthCheckRequest.getDefaultInstance())
            }
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    @Test
    fun `test_ac3_kafka_connection_broken_over_60_seconds_returns_not_serving`() {
        // Start service with healthy Kafka
        val kafkaContainer = startKafkaContainer()
        val serviceProcess = startFraudDetectionService(mapOf("KAFKA_BROKER" to kafkaContainer.bootstrapServers))

        try {
            // Wait for service to be healthy first
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

            // Stop Kafka completely to break connection
            kafkaContainer.stop()

            // Wait 61 seconds (over 60s threshold)
            Thread.sleep(61000)

            // Verify status is NOT_SERVING
            val response = stub.check(HealthCheckRequest.getDefaultInstance())
            assertEquals(HealthCheckResponse.ServingStatus.NOT_SERVING, response.status)
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    @Test
    fun `test_ac4_no_successful_poll_over_60_seconds_returns_not_serving`() {
        // Start Kafka but block consumer poll operations (e.g. network partition that allows TCP connection but no traffic)
        val kafkaContainer = startKafkaContainerWithBlockedPoll()
        val serviceProcess = startFraudDetectionService(mapOf("KAFKA_BROKER" to kafkaContainer.bootstrapServers))

        try {
            // Wait for initial connection to succeed (TCP is open)
            await.atMost(30, TimeUnit.SECONDS).until {
                try {
                    channel = ManagedChannelBuilder.forAddress("localhost", 9091)
                        .usePlaintext()
                        .build()
                    stub = HealthGrpc.newBlockingStub(channel)
                    // Initially might be NOT_SERVING during startup, but wait for first connection
                    true
                } catch (e: Exception) {
                    false
                }
            }

            // Wait 61 seconds with no successful polls
            Thread.sleep(61000)

            // Verify status is NOT_SERVING even though TCP connection is still open
            val response = stub.check(HealthCheckRequest.getDefaultInstance())
            assertEquals(HealthCheckResponse.ServingStatus.NOT_SERVING, response.status)
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    @Test
    fun `test_ac5_sigterm_triggers_immediate_not_serving_and_5s_shutdown_wait`() {
        val serviceProcess = startFraudDetectionService()

        try {
            // Wait for service to be healthy
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

            // Send SIGTERM to process
            serviceProcess.destroy() // SIGTERM on Unix

            // Immediately check health should be NOT_SERVING
            val immediateResponse = stub.check(HealthCheckRequest.getDefaultInstance())
            assertEquals(HealthCheckResponse.ServingStatus.NOT_SERVING, immediateResponse.status)

            // Verify process is still running after 4 seconds
            Thread.sleep(4000)
            assertTrue(serviceProcess.isAlive)

            // Verify process has exited after 6 seconds (total wait 5s minimum)
            Thread.sleep(2000)
            assertFalse(serviceProcess.isAlive)
        } finally {
            if (serviceProcess.isAlive) {
                stopFraudDetectionService(serviceProcess)
            }
        }
    }

    @Test
    fun `test_ac6_initial_startup_before_kafka_connection_returns_not_serving`() {
        // Start service without any Kafka broker available
        val serviceProcess = startFraudDetectionService(mapOf("KAFKA_BROKER" to "localhost:9999")) // Invalid broker

        try {
            // Check health immediately after process starts (before any connection attempts complete)
            channel = ManagedChannelBuilder.forAddress("localhost", 9091)
                .usePlaintext()
                .build()
            stub = HealthGrpc.newBlockingStub(channel)

            // Wait for health endpoint to be available, but status should still be NOT_SERVING
            await.atMost(10, TimeUnit.SECONDS).until {
                try {
                    val response = stub.check(HealthCheckRequest.getDefaultInstance())
                    response.status == HealthCheckResponse.ServingStatus.NOT_SERVING
                } catch (e: Exception) {
                    false
                }
            }

            // Verify status remains NOT_SERVING after 10 seconds (still can't connect to Kafka)
            Thread.sleep(10000)
            val response = stub.check(HealthCheckRequest.getDefaultInstance())
            assertEquals(HealthCheckResponse.ServingStatus.NOT_SERVING, response.status)
        } finally {
            stopFraudDetectionService(serviceProcess)
        }
    }

    // Helper methods (stubs for test framework, to be implemented with actual test container setup)
    private fun startFraudDetectionService(env: Map<String, String> = emptyMap()): Process {
        // Implementation will start the service process with given env vars
        TODO("Test setup implementation")
    }

    private fun stopFraudDetectionService(process: Process) {
        process.destroyForcibly()
        process.waitFor(5, TimeUnit.SECONDS)
    }

    private fun startKafkaContainer(): KafkaContainer {
        TODO("Test setup implementation")
    }

    private fun startKafkaContainerWithBlockedPoll(): KafkaContainer {
        TODO("Test setup implementation")
    }

    // Stub class for Kafka container
    class KafkaContainer {
        val bootstrapServers: String = "localhost:9092"
        fun stop() {}
    }
}
