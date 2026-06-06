/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package frauddetection

import org.apache.kafka.clients.consumer.ConsumerConfig.*
import org.apache.kafka.clients.consumer.KafkaConsumer
import org.apache.kafka.common.errors.WakeupException
import org.apache.kafka.common.serialization.ByteArrayDeserializer
import org.apache.kafka.common.serialization.StringDeserializer
import org.apache.logging.log4j.LogManager
import org.apache.logging.log4j.Logger
import oteldemo.Demo.*
import java.time.Duration.ofMillis
import java.util.*
import kotlin.system.exitProcess
import dev.openfeature.contrib.providers.flagd.FlagdOptions
import dev.openfeature.contrib.providers.flagd.FlagdProvider
import dev.openfeature.sdk.Client
import dev.openfeature.sdk.EvaluationContext
import dev.openfeature.sdk.ImmutableContext
import dev.openfeature.sdk.Value
import dev.openfeature.sdk.OpenFeatureAPI
import io.grpc.Server
import io.grpc.ServerBuilder
import io.grpc.protobuf.services.HealthStatusManager
import io.grpc.health.v1.HealthCheckResponse.ServingStatus
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread
import sun.misc.Signal
import sun.misc.SignalHandler

const val topic = "orders"
const val groupID = "fraud-detection"
const val DEFAULT_HEALTH_PORT = 9091
const val POLL_TIMEOUT_MS = 100L
const val HEALTH_CHECK_INTERVAL_MS = 10000L // 10 seconds
const val MAX_UNHEALTHY_POLL_INTERVAL_MS = 60000L // 60 seconds
const val SHUTDOWN_WAIT_MS = 5000L // 5 seconds

private val logger: Logger = LogManager.getLogger(groupID)
private val lastSuccessfulPollTime = AtomicLong(0)
private var kafkaConsumerConnected = false
private val shutdownInitiated = AtomicBoolean(false)

interface GracefulShutdownManager {
    /**
     * Initiates shutdown sequence, returns when shutdown completes or timeout expires
     * @param timeoutMs maximum time to wait for in-flight operations to complete, default 30000ms (30s)
     * @return true if shutdown completed gracefully before timeout, false if timed out
     */
    fun shutdown(timeoutMs: Long = System.getenv("GRACEFUL_SHUTDOWN_TIMEOUT_MS")?.toLongOrNull() ?: DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_MS): Boolean

    /**
     * Registers resources to be managed during shutdown
     * @param grpcServer running gRPC server instance
     * @param kafkaConsumer active Kafka consumer instance
     */
    fun registerResources(grpcServer: Server, kafkaConsumer: KafkaConsumer<*, *>)
}

class GracefulShutdownManagerImpl : GracefulShutdownManager {
    private lateinit var grpcServer: Server
    private lateinit var kafkaConsumer: KafkaConsumer<*, *>
    private val resourcesRegistered = AtomicBoolean(false)

    override fun registerResources(grpcServer: Server, kafkaConsumer: KafkaConsumer<*, *>) {
        this.grpcServer = grpcServer
        this.kafkaConsumer = kafkaConsumer
        resourcesRegistered.set(true)
    }

    override fun shutdown(timeoutMs: Long): Boolean {
        if (!resourcesRegistered.get()) {
            logger.error("Cannot shutdown: resources not registered")
            return false
        }
        if (shutdownInitiated.compareAndSet(false, true)) {
            logger.info("Received shutdown signal, initiating graceful shutdown")

            // Step 1: Stop accepting new gRPC connections
            logger.info("gRPC server stopped accepting new connections")
            grpcServer.shutdown()

            // Step 2: Stop Kafka consumer polling
            kafkaConsumer.wakeup()

            // Step 3: Wait for gRPC server to terminate
            val grpcShutdownSuccess = grpcServer.awaitTermination(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)

            // Step 4: Commit uncommitted Kafka offsets
            try {
                kafkaConsumer.commitSync()
                logger.info("Kafka consumer polling stopped, all uncommitted offsets successfully committed")
            } catch (e: Exception) {
                logger.error("Failed to commit Kafka offsets during shutdown", e)
            } finally {
                kafkaConsumer.close()
            }

            val shutdownCompleted = grpcShutdownSuccess
            if (shutdownCompleted) {
                logger.info("Graceful shutdown completed successfully")
            } else {
                logger.warn("Graceful shutdown timed out after ${timeoutMs}ms, terminating remaining in-flight operations")
                grpcServer.shutdownNow()
            }
            return shutdownCompleted
        }
        return true
    }
}

data class FlagdClientConfig(
    val connectionTimeoutMs: Int,
    val requestTimeoutMs: Int,
    val maxRetryAttempts: Int
) {
    companion object {
        fun load(): FlagdClientConfig {
            return FlagdClientConfig(
                connectionTimeoutMs = System.getProperty("FLAGD_CONNECTION_TIMEOUT_MS")?.toIntOrNull() 
                    ?: System.getenv("FLAGD_CONNECTION_TIMEOUT_MS")?.toIntOrNull() 
                    ?: 2000,
                requestTimeoutMs = System.getProperty("FLAGD_REQUEST_TIMEOUT_MS")?.toIntOrNull() 
                    ?: System.getenv("FLAGD_REQUEST_TIMEOUT_MS")?.toIntOrNull() 
                    ?: 1000,
                maxRetryAttempts = System.getProperty("FLAGD_RETRY_MAX_ATTEMPTS")?.toIntOrNull() 
                    ?: System.getenv("FLAGD_RETRY_MAX_ATTEMPTS")?.toIntOrNull() 
                    ?: 2
            )
        }
    }
}

fun main() {
    val flagdConfig = FlagdClientConfig.load()
    
    val options = FlagdOptions.builder()
        .withGlobalTelemetry(true)
        .withDeadline(flagdConfig.requestTimeoutMs)
        .withConnectTimeout(flagdConfig.connectionTimeoutMs)
        .withMaxRetries(flagdConfig.maxRetryAttempts)
        .withRetryBackoffMultiplier(2.0) // Exponential backoff
        .withInitialRetryDelay(100) // Initial delay 100ms
        .withMaxRetryDelay(1000) // Max delay 1s
        .build()
    val flagdProvider = FlagdProvider(options)
    OpenFeatureAPI.getInstance().setProvider(flagdProvider)

    val props = Properties()
    props[KEY_DESERIALIZER_CLASS_CONFIG] = StringDeserializer::class.java.name
    props[VALUE_DESERIALIZER_CLASS_CONFIG] = ByteArrayDeserializer::class.java.name
    props[GROUP_ID_CONFIG] = groupID
    props[ENABLE_AUTO_COMMIT_CONFIG] = "false"
    val bootstrapServers = System.getenv("KAFKA_ADDR")
    if (bootstrapServers == null) {
        println("KAFKA_ADDR is not supplied")
        exitProcess(1)
    }
    props[BOOTSTRAP_SERVERS_CONFIG] = bootstrapServers
    val consumer = KafkaConsumer<String, ByteArray>(props).apply {
        subscribe(listOf(topic))
    }

    // Initialize health check service
    val healthStatusManager = HealthStatusManager()
    healthStatusManager.setStatus(HealthStatusManager.SERVICE_NAME_ALL_SERVICES, ServingStatus.NOT_SERVING)

    // Configure health port
    val healthPort = System.getenv("FRAUD_DETECTION_HEALTH_PORT")?.toIntOrNull() ?: DEFAULT_HEALTH_PORT
    val grpcServer: Server = ServerBuilder.forPort(healthPort)
        .addService(healthStatusManager.healthService)
        .build()
        .start()

    logger.info("Health check server started on port $healthPort")

    // Initialize graceful shutdown manager
    val shutdownManager = GracefulShutdownManagerImpl()
    shutdownManager.registerResources(grpcServer, consumer)

    // Register signal handlers for SIGINT (2) and SIGTERM (15)
    val signalHandler = SignalHandler { signal ->
        val shutdownSuccess = shutdownManager.shutdown()
        exitProcess(if (shutdownSuccess) 0 else if (signal.number == 2) 130 else 143)
    }

    try {
        Signal.handle(Signal("INT"), signalHandler)
        Signal.handle(Signal("TERM"), signalHandler)
    } catch (e: IllegalArgumentException) {
        logger.warn("Signal handling not supported on this platform, falling back to JVM shutdown hook", e)
    }

    // Add backup shutdown hook
    Runtime.getRuntime().addShutdownHook(thread(start = false) {
        if (!shutdownInitiated.get()) {
            logger.info("Received shutdown request via JVM shutdown hook, initiating graceful shutdown")
            shutdownManager.shutdown()
        }
    })

    // Background thread to monitor Kafka health
    thread(start = true, isDaemon = true) {
        while (true) {
            val currentTime = System.currentTimeMillis()
            val timeSinceLastPoll = currentTime - lastSuccessfulPollTime.get()

            val isHealthy = kafkaConsumerConnected && timeSinceLastPoll < MAX_UNHEALTHY_POLL_INTERVAL_MS
            val newStatus = if (isHealthy) ServingStatus.SERVING else ServingStatus.NOT_SERVING

            healthStatusManager.setStatus(HealthStatusManager.SERVICE_NAME_ALL_SERVICES, newStatus)

            Thread.sleep(HEALTH_CHECK_INTERVAL_MS)
        }
    }

    var totalCount = 0L

    consumer.use {
        try {
            while (!shutdownInitiated.get()) {
                val records = consumer.poll(ofMillis(POLL_TIMEOUT_MS))
                if (!records.isEmpty) {
                    kafkaConsumerConnected = true
                    lastSuccessfulPollTime.set(System.currentTimeMillis())
                }
                totalCount = records
                    .fold(totalCount) { accumulator, record ->
                        val orders = OrderResult.parseFrom(record.value())
                        
                        // Validate order amount per AC-1: check if total amount is negative or zero
                        var totalNanos: Long = 0
                        for (item in orders.itemsList) {
                            val cost = item.cost
                            totalNanos += cost.units * 1_000_000_000L + cost.nanos
                        }
                        totalNanos += orders.shippingCost.units * 1_000_000_000L + orders.shippingCost.nanos
                        
                        if (totalNanos <= 0) {
                            logger.warn("Order ID {} has invalid amount (null or negative). Skipping further processing.", orders.orderId)
                            return@fold accumulator + 1
                        }
                        
                        val newCount = accumulator + 1
                        if (getFeatureFlagValue("kafkaQueueProblems") > 0) {
                            logger.info("FeatureFlag 'kafkaQueueProblems' is enabled, sleeping 1 second")
                            Thread.sleep(1000)
                        }
                        logger.info("Consumed record with orderId: ${orders.orderId}, and updated total count to: $newCount")
                        newCount
                    }
            }
        } catch (e: WakeupException) {
            // Expected when shutdown is initiated, ignore
            logger.info("Kafka consumer woken up for shutdown")
        } catch (e: Exception) {
            logger.error("Unexpected error in consumer loop", e)
        }
    }
}

/**
* Retrieves the status of a feature flag from the Feature Flag service.
*
* @param ff The name of the feature flag to retrieve.
* @return `true` if the feature flag is enabled, `false` otherwise or in case of errors.
*/
fun getFeatureFlagValue(ff: String): Int {
    val client = OpenFeatureAPI.getInstance().client
    // TODO: Plumb the actual session ID from the frontend via baggage?
    val uuid = UUID.randomUUID()

    val clientAttrs = mutableMapOf<String, Value>()
    clientAttrs["session"] = Value(uuid.toString())
    client.evaluationContext = ImmutableContext(clientAttrs)
    
    return try {
        val intValue = client.getIntegerValue(ff, 0)
        intValue
    } catch (ex: Exception) {
        logger.error(
            "Flag evaluation failed for flag '{}', error: {}. {} retries attempted, falling back to default value 0.",
            ff,
            ex.message,
            FlagdClientConfig.load().maxRetryAttempts,
            ex
        )
        0
    }
}
