package frauddetection

import io.grpc.ManagedChannel
import io.grpc.Server
import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.grpc.netty.NettyChannelBuilder
import io.grpc.netty.NettyServerBuilder
import org.apache.kafka.clients.consumer.ConsumerRecord
import org.apache.kafka.clients.consumer.KafkaConsumer
import org.apache.kafka.clients.producer.KafkaProducer
import org.apache.kafka.clients.producer.ProducerRecord
import org.apache.kafka.common.serialization.StringDeserializer
import org.apache.kafka.common.serialization.StringSerializer
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import org.testcontainers.containers.KafkaContainer
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.utility.DockerImageName
import java.time.Duration
import java.util.*
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

@Testcontainers
class GracefulShutdownIntegrationTest {

    @Container
    private val kafka = KafkaContainer(DockerImageName.parse("confluentinc/cp-kafka:7.4.0"))

    private lateinit var kafkaProducer: KafkaProducer<String, String>
    private lateinit var kafkaConsumer: KafkaConsumer<String, String>
    private lateinit var testGrpcServer: Server
    private lateinit var grpcChannel: ManagedChannel
    private val testTopic = "fraud-events-test"
    private val testGrpcPort = 50052
    private lateinit var shutdownManager: GracefulShutdownManager

    @BeforeEach
    fun setup() {
        // Initialize Kafka producer
        val producerProps = Properties().apply {
            put("bootstrap.servers", kafka.bootstrapServers)
            put("key.serializer", StringSerializer::class.java.name)
            put("value.serializer", StringSerializer::class.java.name)
        }
        kafkaProducer = KafkaProducer(producerProps)

        // Initialize Kafka consumer
        val consumerProps = Properties().apply {
            put("bootstrap.servers", kafka.bootstrapServers)
            put("group.id", "fraud-detection-test-group")
            put("key.deserializer", StringDeserializer::class.java.name)
            put("value.deserializer", StringDeserializer::class.java.name)
            put("enable.auto.commit", "false")
            put("auto.offset.reset", "earliest")
        }
        kafkaConsumer = KafkaConsumer<String, String>(consumerProps)
        kafkaConsumer.subscribe(listOf(testTopic))

        // Initialize gRPC server with test service
        testGrpcServer = NettyServerBuilder.forPort(testGrpcPort)
            .addService(FraudDetectionServiceTestImpl())
            .build()
        testGrpcServer.start()

        // Initialize gRPC channel
        grpcChannel = NettyChannelBuilder.forAddress("localhost", testGrpcPort)
            .usePlaintext()
            .build()

        // Initialize shutdown manager (SUT component)
        shutdownManager = GracefulShutdownManagerImpl()
        shutdownManager.registerResources(testGrpcServer, kafkaConsumer)
    }

    @AfterEach
    fun teardown() {
        if (::grpcChannel.isInitialized) grpcChannel.shutdownNow()
        if (::testGrpcServer.isInitialized) testGrpcServer.shutdownNow()
        if (::kafkaConsumer.isInitialized) kafkaConsumer.close()
        if (::kafkaProducer.isInitialized) kafkaProducer.close()
    }

    @Test
    fun test_ac1_signal_triggers_shutdown_sequence() {
        // AC-1: Signal handling triggers correct shutdown sequence
        // Precondition: produce records to Kafka, poll some to get uncommitted offsets
        for (i in 1..10) {
            kafkaProducer.send(ProducerRecord(testTopic, "key$i", "value$i")).get()
        }
        val records = kafkaConsumer.poll(Duration.ofSeconds(1))
        assertEquals(10, records.count())
        val initialPosition = kafkaConsumer.position(records.iterator().next().topicPartition())

        // Simulate signal receipt by triggering shutdown
        val shutdownFuture = thread { shutdownManager.shutdown() }

        // Verify within 1s: gRPC stops accepting new connections
        Thread.sleep(1000)
        val newChannel = NettyChannelBuilder.forAddress("localhost", testGrpcPort)
            .usePlaintext()
            .build()
        val stub = FraudDetectionServiceGrpc.newBlockingStub(newChannel)
        val exception = assertThrows(StatusRuntimeException::class.java) {
            stub.isFraud(IsFraudRequest.newBuilder().build())
        }
        assertEquals(Status.Code.UNAVAILABLE, exception.status.code)

        // Verify Kafka consumer no longer polls new records
        val postShutdownRecords = kafkaConsumer.poll(Duration.ofSeconds(1))
        assertEquals(0, postShutdownRecords.count())

        // Verify offsets are committed
        val currentPosition = kafkaConsumer.position(records.iterator().next().topicPartition())
        assertEquals(initialPosition + 10, currentPosition)

        // Verify shutdown completed
        shutdownFuture.join(5000)
        assertTrue(shutdownFuture.isAlive == false)
    }

    @Test
    fun test_ac2_in_flight_requests_complete_before_shutdown() {
        // AC-2: In-flight requests complete successfully before shutdown
        val latch = CountDownLatch(1)
        var requestSuccess = false

        // Start long-running gRPC request (5s)
        thread {
            try {
                val stub = FraudDetectionServiceGrpc.newBlockingStub(grpcChannel)
                stub.isFraud(IsFraudRequest.newBuilder().setDelayMs(5000).build())
                requestSuccess = true
            } finally {
                latch.countDown()
            }
        }

        // Wait 1s then trigger shutdown
        Thread.sleep(1000)
        val shutdownCompleted = shutdownManager.shutdown()

        // Verify request completed successfully
        assertTrue(latch.await(6, TimeUnit.SECONDS))
        assertTrue(requestSuccess)
        assertTrue(shutdownCompleted)
        // Verify exit code would be 0 (simulated by successful shutdown)
    }

    @Test
    fun test_ac3_shutdown_timeout_enforces_forced_exit() {
        // AC-3: Shutdown timeout enforces forced exit for long running requests
        val latch = CountDownLatch(1)
        var requestTerminated = false

        // Start 40s long running request
        thread {
            try {
                val stub = FraudDetectionServiceGrpc.newBlockingStub(grpcChannel)
                stub.isFraud(IsFraudRequest.newBuilder().setDelayMs(40000).build())
            } catch (e: StatusRuntimeException) {
                requestTerminated = true
            } finally {
                latch.countDown()
            }
        }

        // Wait 1s then trigger shutdown with 30s timeout
        Thread.sleep(1000)
        val shutdownStartTime = System.currentTimeMillis()
        val shutdownCompleted = shutdownManager.shutdown(timeoutMs = 30000)
        val shutdownDuration = System.currentTimeMillis() - shutdownStartTime

        // Verify shutdown timed out after ~30s
        assertFalse(shutdownCompleted)
        assertTrue(shutdownDuration >= 29000 && shutdownDuration <= 31000)
        // Verify request was terminated
        assertTrue(latch.await(2, TimeUnit.SECONDS))
        assertTrue(requestTerminated)
        // Verify exit code would be 130/143 as per signal type (checked in system test)
    }

    @Test
    fun test_ac4_shutdown_events_properly_logged() {
        // AC-4: Shutdown events are properly logged for observability
        val logAppender = InMemoryLogAppender()
        LogManager.getRootLogger().addAppender(logAppender)

        // Trigger shutdown
        shutdownManager.shutdown()

        // Verify expected logs exist
        val logs = logAppender.getLogs()
        assertTrue(logs.any { it.level == Level.INFO && it.message.contains("Received shutdown signal") })
        assertTrue(logs.any { it.level == Level.INFO && it.message.contains("gRPC server stopped accepting new connections") })
        assertTrue(logs.any { it.level == Level.INFO && it.message.contains("Kafka consumer polling stopped, all uncommitted offsets successfully committed") })
        assertTrue(logs.any { it.level == Level.INFO && it.message.contains("Graceful shutdown completed successfully") })

        // Test timeout log case
        val logAppender2 = InMemoryLogAppender()
        LogManager.getRootLogger().addAppender(logAppender2)

        // Start long request to trigger timeout
        thread {
            val stub = FraudDetectionServiceGrpc.newBlockingStub(grpcChannel)
            stub.isFraud(IsFraudRequest.newBuilder().setDelayMs(40000).build())
        }
        Thread.sleep(1000)
        shutdownManager.shutdown(timeoutMs = 1000)

        val timeoutLogs = logAppender2.getLogs()
        assertTrue(timeoutLogs.any { it.level == Level.WARN && it.message.contains("Graceful shutdown timed out after") })
    }

    @Test
    fun test_ac5_no_data_loss_after_graceful_shutdown() {
        // AC-5: No data loss after graceful shutdown
        // Produce 100 records to Kafka
        for (i in 1..100) {
            kafkaProducer.send(ProducerRecord(testTopic, "key$i", "value$i")).get()
        }

        // Poll and process 50 records
        val records = kafkaConsumer.poll(Duration.ofSeconds(1))
        var processedCount = 0
        records.forEach { _ ->
            processedCount++
            if (processedCount == 50) {
                // Trigger shutdown after 50 processed
                shutdownManager.shutdown()
            }
        }
        assertEquals(100, records.count())
        assertEquals(50, processedCount)

        // Verify offsets committed for first 50 records
        val partition = records.iterator().next().topicPartition()
        val committedOffset = kafkaConsumer.committed(setOf(partition))[partition]?.offset() ?: 0
        assertEquals(50, committedOffset)

        // Restart consumer to verify no duplicates on reprocessing
        kafkaConsumer.close()
        val newConsumer = KafkaConsumer<String, String>(Properties().apply {
            put("bootstrap.servers", kafka.bootstrapServers)
            put("group.id", "fraud-detection-test-group")
            put("key.deserializer", StringDeserializer::class.java.name)
            put("value.deserializer", StringDeserializer::class.java.name)
            put("enable.auto.commit", "false")
            put("auto.offset.reset", "earliest")
        })
        newConsumer.subscribe(listOf(testTopic))
        val reprocessedRecords = newConsumer.poll(Duration.ofSeconds(2))
        // Should only get records 51-100, total 50
        assertEquals(50, reprocessedRecords.count())
        newConsumer.close()
    }

    // Test implementation dependencies
    interface GracefulShutdownManager {
        fun shutdown(timeoutMs: Long = 30000): Boolean
        fun registerResources(grpcServer: Server, kafkaConsumer: KafkaConsumer<*, *>)
    }

    class GracefulShutdownManagerImpl : GracefulShutdownManager {
        // Dummy implementation for test failure (no actual logic)
        override fun shutdown(timeoutMs: Long): Boolean = true
        override fun registerResources(grpcServer: Server, kafkaConsumer: KafkaConsumer<*, *>) {}
    }

    class FraudDetectionServiceTestImpl : FraudDetectionServiceGrpc.FraudDetectionServiceImplBase() {
        override fun isFraud(request: IsFraudRequest, responseObserver: io.grpc.stub.StreamObserver<IsFraudResponse>) {
            if (request.delayMs > 0) {
                Thread.sleep(request.delayMs)
            }
            responseObserver.onNext(IsFraudResponse.newBuilder().setFraud(false).build())
            responseObserver.onCompleted()
        }
    }

    // Log appender for test
    class InMemoryLogAppender : org.apache.logging.log4j.core.appender.AbstractAppender("InMemoryAppender", null, null, false, null) {
        private val logs = mutableListOf<LogEvent>()

        override fun append(event: org.apache.logging.log4j.core.LogEvent) {
            logs.add(LogEvent(event.level, event.message.formattedMessage))
        }

        fun getLogs() = logs.toList()
    }

    data class LogEvent(val level: Level, val message: String)
}
