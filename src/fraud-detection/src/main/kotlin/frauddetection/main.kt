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
import org.slf4j.MDC
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
import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.grpc.Context
import io.grpc.protobuf.services.HealthStatusManager
import io.grpc.health.v1.HealthCheckResponse.ServingStatus
import io.grpc.netty.shaded.io.netty.handler.ssl.SslContext
import io.grpc.netty.shaded.io.netty.channel.ChannelHandlerContext
import io.grpc.netty.shaded.io.netty.channel.ChannelInboundHandlerAdapter
import io.grpc.netty.shaded.io.netty.handler.codec.http.DefaultFullHttpResponse
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpResponseStatus
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpVersion
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpHeaderNames
import io.grpc.netty.shaded.io.netty.buffer.Unpooled
import io.grpc.netty.shaded.io.netty.util.CharsetUtil
import io.opentelemetry.demo.frauddetection.CheckTransactionRequest
import io.opentelemetry.demo.frauddetection.CheckTransactionResponse
import io.opentelemetry.demo.frauddetection.FraudDetectionServiceGrpc
import java.io.File
import java.io.FileInputStream
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread
import sun.misc.Signal
import sun.misc.SignalHandler
import frauddetection.health.ServiceReadinessCheck

val isShuttingDown = AtomicBoolean(false)
val kafkaConnected = AtomicBoolean(false)

class HealthCheckHandler : ChannelInboundHandlerAdapter() {
    override fun channelRead(ctx: ChannelHandlerContext, msg: Any) {
        if (msg is io.grpc.netty.shaded.io.netty.handler.codec.http.HttpRequest) {
            val uri = msg.uri()
            if (msg.method() == io.grpc.netty.shaded.io.netty.handler.codec.http.HttpMethod.GET) {
                when (uri) {
                    "/health/liveness" -> handleLiveness(ctx)
                    "/health/readiness" -> handleReadiness(ctx)
                    else -> ctx.fireChannelRead(msg)
                }
                return
            }
        }
        ctx.fireChannelRead(msg)
    }

    private fun handleLiveness(ctx: ChannelHandlerContext) {
        val status = if (isShuttingDown.get()) HttpResponseStatus.SERVICE_UNAVAILABLE else HttpResponseStatus.OK
        val responseBody = if (isShuttingDown.get()) """{"status":"DOWN","error":"Service is shutting down"}""" else """{"status":"UP"}"""
        sendJsonResponse(ctx, status, responseBody)
    }

    private fun handleReadiness(ctx: ChannelHandlerContext) {
        if (isShuttingDown.get()) {
            val status = HttpResponseStatus.SERVICE_UNAVAILABLE
            val responseBody = """{"status":"DOWN","error":"Service is shutting down"}"""
            sendJsonResponse(ctx, status, responseBody)
            return
        }

        val readinessCheck = ServiceReadinessCheck()
        val result = readinessCheck.call()
        val status = if (result.status == org.eclipse.microprofile.health.HealthCheckResponse.Status.UP) {
            HttpResponseStatus.OK
        } else {
            HttpResponseStatus.SERVICE_UNAVAILABLE
        }

        val dependencies = result.data.mapValues { entry ->
            entry.value.toString()
        }

        val responseBody = buildString {
            append("{")
            append("\"status\":\"${if (result.status == org.eclipse.microprofile.health.HealthCheckResponse.Status.UP) "UP" else "DOWN"}\"")
            if (dependencies.isNotEmpty()) {
                append(",\"dependencies\":{")
                append(dependencies.map { (key, value) -> "\"$key\":\"$value\"" }.joinToString(","))
                append("}")
            }
            if (result.status == org.eclipse.microprofile.health.HealthCheckResponse.Status.DOWN) {
                append(",\"error\":\"One or more dependencies are unavailable\"")
            }
            append("}")
        }
        sendJsonResponse(ctx, status, responseBody)
    }

    private fun sendJsonResponse(ctx: ChannelHandlerContext, status: HttpResponseStatus, body: String) {
        val response = DefaultFullHttpResponse(
            HttpVersion.HTTP_1_1,
            status,
            Unpooled.copiedBuffer(body, CharsetUtil.UTF_8)
        )
        response.headers().set(HttpHeaderNames.CONTENT_TYPE, "application/json")
        response.headers().set(HttpHeaderNames.CONTENT_LENGTH, response.content().readableBytes())
        ctx.writeAndFlush(response)
    }
}

class FraudDetectionServiceImpl : FraudDetectionServiceGrpc.FraudDetectionServiceImplBase() {
    override fun checkTransaction(
        request: CheckTransactionRequest,
        responseObserver: io.grpc.stub.StreamObserver<CheckTransactionResponse>
    ) {
        // Basic implementation for validation test
        val response = CheckTransactionResponse.newBuilder()
            .setFraudScore(0.0)
            .setIsFraudulent(false)
            .build()
        responseObserver.onNext(response)
        responseObserver.onCompleted()
    }
}


// Validation types
sealed class Result<out T> {
    data class Success<out T>(val value: T) : Result<T>()
    data class Failure(val error: Exception) : Result<Nothing>()
}

data class ValidatedAddress(
    val street: String,
    val city: String,
    val postalCode: String,
    val country: String
)

data class OrderItem(
    val id: String,
    val name: String,
    val quantity: Int,
    val price: Long
)

data class ValidatedOrder(
    val orderId: String,
    val userId: String,
    val items: List<OrderItem>,
    val shippingAddress: ValidatedAddress,
    val paymentMethodId: String,
    val totalAmount: Long
)

sealed class OrderValidationError(message: String) : Exception(message) {
    class MalformedProtobuf(message: String = "Failed to deserialize Protobuf message") : OrderValidationError(message)
    class MissingRequiredField(field: String) : OrderValidationError("Missing required field: $field")
    class InvalidFieldValue(field: String, reason: String) : OrderValidationError("Invalid value for field $field: $reason")
    class FieldTooLong(field: String, maxLength: Int) : OrderValidationError("Field $field exceeds maximum allowed length $maxLength")
}

const val topic = "orders"
const val groupID = "fraud-detection"
const val DEFAULT_HEALTH_PORT = 9091
const val DEFAULT_GRPC_TLS_PORT = 9092
const val POLL_TIMEOUT_MS = 100L
const val HEALTH_CHECK_INTERVAL_MS = 10000L // 10 seconds
const val MAX_UNHEALTHY_POLL_INTERVAL_MS = 60000L // 60 seconds
const val SHUTDOWN_WAIT_MS = 5000L // 5 seconds
const val DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_MS = 30000L // 30 seconds default

// gRPC context keys
val GRPC_CONTEXT_REQUEST_ID_KEY: Context.Key<String> = Context.key("x-request-id")

// TLS Configuration Environment Variables
const val ENV_GRPC_TLS_ENABLED = "GRPC_TLS_ENABLED"
const val ENV_GRPC_TLS_CERT_PATH = "GRPC_TLS_CERT_PATH"
const val ENV_GRPC_TLS_KEY_PATH = "GRPC_TLS_KEY_PATH"
const val ENV_GRPC_TLS_CLIENT_CA_PATH = "GRPC_TLS_CLIENT_CA_PATH"
const val ENV_GRPC_PLAINTEXT_ENABLED = "GRPC_PLAINTEXT_ENABLED"
const val ENV_KAFKA_TLS_ENABLED = "KAFKA_TLS_ENABLED"
const val ENV_KAFKA_TLS_CERT_PATH = "KAFKA_TLS_CERT_PATH"
const val ENV_KAFKA_TLS_KEY_PATH = "KAFKA_TLS_KEY_PATH"
const val ENV_KAFKA_TLS_CA_PATH = "KAFKA_TLS_CA_PATH"

data class TlsConfig(
    val grpcTlsEnabled: Boolean,
    val grpcTlsCertPath: String,
    val grpcTlsKeyPath: String,
    val grpcTlsClientCaPath: String,
    val grpcPlaintextEnabled: Boolean,
    val kafkaTlsEnabled: Boolean,
    val kafkaTlsCertPath: String,
    val kafkaTlsKeyPath: String,
    val kafkaTlsCaPath: String
)

fun loadTlsConfigFromEnv(): TlsConfig {
    return TlsConfig(
        grpcTlsEnabled = System.getenv(ENV_GRPC_TLS_ENABLED)?.toBooleanStrictOrNull() ?: false,
        grpcTlsCertPath = System.getenv(ENV_GRPC_TLS_CERT_PATH) ?: "",
        grpcTlsKeyPath = System.getenv(ENV_GRPC_TLS_KEY_PATH) ?: "",
        grpcTlsClientCaPath = System.getenv(ENV_GRPC_TLS_CLIENT_CA_PATH) ?: "",
        grpcPlaintextEnabled = System.getenv(ENV_GRPC_PLAINTEXT_ENABLED)?.toBooleanStrictOrNull() ?: true,
        kafkaTlsEnabled = System.getenv(ENV_KAFKA_TLS_ENABLED)?.toBooleanStrictOrNull() ?: false,
        kafkaTlsCertPath = System.getenv(ENV_KAFKA_TLS_CERT_PATH) ?: "",
        kafkaTlsKeyPath = System.getenv(ENV_KAFKA_TLS_KEY_PATH) ?: "",
        kafkaTlsCaPath = System.getenv(ENV_KAFKA_TLS_CA_PATH) ?: ""
    )
}

fun validateTlsConfig(config: TlsConfig): Result<Unit> {
    // Validate gRPC TLS config
    if (config.grpcTlsEnabled) {
        if (config.grpcTlsCertPath.isBlank()) {
            return Result.Failure(IllegalArgumentException("$ENV_GRPC_TLS_CERT_PATH must be set when $ENV_GRPC_TLS_ENABLED is true"))
        }
        if (config.grpcTlsKeyPath.isBlank()) {
            return Result.Failure(IllegalArgumentException("$ENV_GRPC_TLS_KEY_PATH must be set when $ENV_GRPC_TLS_ENABLED is true"))
        }
        val certFile = File(config.grpcTlsCertPath)
        if (!certFile.exists() || !certFile.isFile || !certFile.canRead()) {
            return Result.Failure(IllegalArgumentException("$ENV_GRPC_TLS_CERT_PATH '${config.grpcTlsCertPath}' does not exist or is not readable"))
        }
        val keyFile = File(config.grpcTlsKeyPath)
        if (!keyFile.exists() || !keyFile.isFile || !keyFile.canRead()) {
            return Result.Failure(IllegalArgumentException("$ENV_GRPC_TLS_KEY_PATH '${config.grpcTlsKeyPath}' does not exist or is not readable"))
        }
        if (config.grpcTlsClientCaPath.isNotBlank()) {
            val caFile = File(config.grpcTlsClientCaPath)
            if (!caFile.exists() || !caFile.isFile || !caFile.canRead()) {
                return Result.Failure(IllegalArgumentException("$ENV_GRPC_TLS_CLIENT_CA_PATH '${config.grpcTlsClientCaPath}' does not exist or is not readable"))
            }
        }
    }

    // Validate Kafka TLS config
    if (config.kafkaTlsEnabled) {
        if (config.kafkaTlsCaPath.isBlank()) {
            return Result.Failure(IllegalArgumentException("$ENV_KAFKA_TLS_CA_PATH must be set when $ENV_KAFKA_TLS_ENABLED is true"))
        }
        val caFile = File(config.kafkaTlsCaPath)
        if (!caFile.exists() || !caFile.isFile || !caFile.canRead()) {
            return Result.Failure(IllegalArgumentException("$ENV_KAFKA_TLS_CA_PATH '${config.kafkaTlsCaPath}' does not exist or is not readable"))
        }
        if (config.kafkaTlsCertPath.isNotBlank() && config.kafkaTlsKeyPath.isNotBlank()) {
            val certFile = File(config.kafkaTlsCertPath)
            if (!certFile.exists() || !certFile.isFile || !certFile.canRead()) {
                return Result.Failure(IllegalArgumentException("$ENV_KAFKA_TLS_CERT_PATH '${config.kafkaTlsCertPath}' does not exist or is not readable"))
            }
            val keyFile = File(config.kafkaTlsKeyPath)
            if (!keyFile.exists() || !keyFile.isFile || !keyFile.canRead()) {
                return Result.Failure(IllegalArgumentException("$ENV_KAFKA_TLS_KEY_PATH '${config.kafkaTlsKeyPath}' does not exist or is not readable"))
            }
        } else if (config.kafkaTlsCertPath.isNotBlank() || config.kafkaTlsKeyPath.isNotBlank()) {
            return Result.Failure(IllegalArgumentException("Both $ENV_KAFKA_TLS_CERT_PATH and $ENV_KAFKA_TLS_KEY_PATH must be set together when using client certificate authentication for Kafka"))
        }
    }

    return Result.Success(Unit)
}

private val logger: Logger = LogManager.getLogger(groupID)
private val lastSuccessfulPollTime = AtomicLong(0)
private var kafkaConsumerConnected = false
private val shutdownInitiated = AtomicBoolean(false)
private val activeOperations = AtomicLong(0)

data class ShutdownResult(
    val completedOperations: Int,
    val timedOutOperations: Int,
    val shutdownSuccess: Boolean
)

interface GracefulShutdownManager {
    /**
     * Initiates shutdown sequence, returns when shutdown completes or timeout expires
     * @param timeoutMs maximum time to wait for in-flight operations to complete, default 30000ms (30s)
     * @return ShutdownResult with counts of completed/timed out operations and success status
     */
    fun shutdown(timeoutMs: Long = System.getenv("fraud-detection.shutdown.timeout-seconds")?.toLongOrNull()?.times(1000) ?: DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_MS): ShutdownResult

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

    override fun shutdown(timeoutMs: Long): ShutdownResult {
        if (!resourcesRegistered.get()) {
            logger.error("Cannot shutdown: resources not registered")
            return ShutdownResult(0, 0, false)
        }
        if (shutdownInitiated.compareAndSet(false, true)) {
            logger.info("Received shutdown signal, initiating graceful shutdown with timeout ${timeoutMs}ms")

            // Step 1: Stop accepting new gRPC connections
            logger.info("Stopping gRPC server from accepting new connections")
            grpcServer.shutdown()

            // Step 2: Stop Kafka consumer polling
            kafkaConsumer.wakeup()
            logger.info("Kafka consumer polling stopped, no new messages will be processed")

            val startTime = System.currentTimeMillis()
            val endTime = startTime + timeoutMs

            // Step 3: Wait for all active fraud check operations to complete or timeout
            var remainingActive = activeOperations.get()
            while (remainingActive > 0 && System.currentTimeMillis() < endTime) {
                logger.info("Waiting for $remainingActive active fraud check operations to complete...")
                Thread.sleep(500)
                remainingActive = activeOperations.get()
            }

            val completedOperations = activeOperations.get()
            val timedOutOperations = remainingActive

            // Step 4: Wait for gRPC server to terminate
            val remainingTimeout = endTime - System.currentTimeMillis()
            val grpcShutdownSuccess = if (remainingTimeout > 0) {
                grpcServer.awaitTermination(remainingTimeout, java.util.concurrent.TimeUnit.MILLISECONDS)
            } else {
                false
            }

            // Step 5: Commit uncommitted Kafka offsets and close consumer
            try {
                kafkaConsumer.commitSync()
                logger.info("All uncommitted Kafka offsets successfully committed")
            } catch (e: Exception) {
                logger.error("Failed to commit Kafka offsets during shutdown", e)
            } finally {
                kafkaConsumer.close()
                logger.info("Kafka consumer connection closed cleanly")
            }

            val shutdownCompleted = timedOutOperations == 0 && grpcShutdownSuccess
            if (shutdownCompleted) {
                logger.info("Shutdown completed successfully: $completedOperations operations completed, 0 timed out")
            } else {
                if (timedOutOperations > 0) {
                    logger.warn("Shutdown timed out after ${timeoutMs}ms: $completedOperations operations completed, $timedOutOperations timed out")
                }
                if (!grpcShutdownSuccess) {
                    logger.warn("gRPC server did not terminate within timeout, forcing shutdown")
                    grpcServer.shutdownNow()
                }
            }
            return ShutdownResult(completedOperations.toInt(), timedOutOperations.toInt(), shutdownCompleted)
        }
        return ShutdownResult(0, 0, true)
    }
}

/**
 * Validates and sanitizes incoming OrderResult messages from Kafka orders topic
 * @param message Raw Kafka consumer record value (bytes)
 * @return Result<ValidatedOrder>: Success with sanitized valid order, Failure with validation error
 */
fun validateOrderMessage(message: ByteArray): Result<ValidatedOrder> {
    return try {
        val order = OrderResult.parseFrom(message)
        
        // Validate required fields
        if (order.orderId.isNullOrBlank()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("order_id"))
        }
        if (order.userId.isNullOrBlank()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("user_id"))
        }
        if (order.itemsList.isNullOrEmpty()) {
            return Result.Failure(OrderValidationError.InvalidFieldValue("items", "list is empty"))
        }
        if (!order.hasShippingAddress()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("shipping_address"))
        }
        val shippingAddress = order.shippingAddress
        if (shippingAddress.street.isNullOrBlank()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("shipping_address.street"))
        }
        if (shippingAddress.city.isNullOrBlank()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("shipping_address.city"))
        }
        if (shippingAddress.postalCode.isNullOrBlank()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("shipping_address.postal_code"))
        }
        if (shippingAddress.country.isNullOrBlank()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("shipping_address.country"))
        }
        if (order.paymentMethodId.isNullOrBlank()) {
            return Result.Failure(OrderValidationError.MissingRequiredField("payment_method_id"))
        }
        
        // Calculate total amount and validate it's non-negative
        var totalNanos: Long = 0
        for (item in order.itemsList) {
            val cost = item.cost
            totalNanos += cost.units * 1_000_000_000L + cost.nanos
        }
        totalNanos += order.shippingCost.units * 1_000_000_000L + order.shippingCost.nanos
        
        if (totalNanos < 0) {
            return Result.Failure(OrderValidationError.InvalidFieldValue("total_amount", "value is negative"))
        }
        
        // Sanitize string fields: trim and truncate to max lengths
        val sanitizedOrderId = order.orderId.trim().take(64)
        val sanitizedUserId = order.userId.trim().take(64)
        val sanitizedPaymentMethodId = order.paymentMethodId.trim().take(64)
        
        val sanitizedAddress = ValidatedAddress(
            street = shippingAddress.street.trim().take(256),
            city = shippingAddress.city.trim().take(256),
            postalCode = shippingAddress.postalCode.trim().take(256),
            country = shippingAddress.country.trim().take(256)
        )
        
        val sanitizedItems = order.itemsList.map { item ->
            OrderItem(
                id = item.itemId.trim().take(64),
                name = item.name.trim().take(256),
                quantity = item.quantity,
                price = item.cost.units * 1_000_000_000L + item.cost.nanos
            )
        }
        
        Result.Success(
            ValidatedOrder(
                orderId = sanitizedOrderId,
                userId = sanitizedUserId,
                items = sanitizedItems,
                shippingAddress = sanitizedAddress,
                paymentMethodId = sanitizedPaymentMethodId,
                totalAmount = totalNanos
            )
        )
    } catch (e: Exception) {
        if (e is com.google.protobuf.InvalidProtocolBufferException) {
            Result.Failure(OrderValidationError.MalformedProtobuf())
        } else {
            Result.Failure(e)
        }
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

data class GrpcTlsConfig(
    val enabled: Boolean,
    val certPath: String,
    val keyPath: String,
    val clientCaPath: String,
    val plaintextEnabled: Boolean
) {
    companion object {
        fun load(): GrpcTlsConfig {
            return GrpcTlsConfig(
                enabled = System.getenv("GRPC_TLS_ENABLED")?.toBoolean() ?: false,
                certPath = System.getenv("GRPC_TLS_CERT_PATH") ?: "",
                keyPath = System.getenv("GRPC_TLS_KEY_PATH") ?: "",
                clientCaPath = System.getenv("GRPC_TLS_CLIENT_CA_PATH") ?: "",
                plaintextEnabled = System.getenv("GRPC_PLAINTEXT_ENABLED")?.toBoolean() ?: true
            )
        }

        fun validate(config: GrpcTlsConfig) {
            if (!config.enabled) return

            if (config.certPath.isBlank()) {
                throw IllegalArgumentException("GRPC_TLS_CERT_PATH must be set when GRPC_TLS_ENABLED is true")
            }
            if (config.keyPath.isBlank()) {
                throw IllegalArgumentException("GRPC_TLS_KEY_PATH must be set when GRPC_TLS_ENABLED is true")
            }

            val certFile = File(config.certPath)
            if (!certFile.exists() || !certFile.isFile || !certFile.canRead()) {
                throw IllegalArgumentException("GRPC_TLS_CERT_PATH points to non-existent, unreadable, or non-file path: ${config.certPath}")
            }

            val keyFile = File(config.keyPath)
            if (!keyFile.exists() || !keyFile.isFile || !keyFile.canRead()) {
                throw IllegalArgumentException("GRPC_TLS_KEY_PATH points to non-existent, unreadable, or non-file path: ${config.keyPath}")
            }

            if (config.clientCaPath.isNotBlank()) {
                val caFile = File(config.clientCaPath)
                if (!caFile.exists() || !caFile.isFile || !caFile.canRead()) {
                    throw IllegalArgumentException("GRPC_TLS_CLIENT_CA_PATH points to non-existent, unreadable, or non-file path: ${config.clientCaPath}")
                }
            }
        }
    }
}

data class KafkaTlsConfig(
    val enabled: Boolean,
    val certPath: String,
    val keyPath: String,
    val caPath: String
) {
    companion object {
        fun load(): KafkaTlsConfig {
            return KafkaTlsConfig(
                enabled = System.getenv("KAFKA_TLS_ENABLED")?.toBoolean() ?: false,
                certPath = System.getenv("KAFKA_TLS_CERT_PATH") ?: "",
                keyPath = System.getenv("KAFKA_TLS_KEY_PATH") ?: "",
                caPath = System.getenv("KAFKA_TLS_CA_PATH") ?: ""
            )
        }

        fun validate(config: KafkaTlsConfig) {
            if (!config.enabled) return

            if (config.caPath.isBlank()) {
                throw IllegalArgumentException("KAFKA_TLS_CA_PATH must be set when KAFKA_TLS_ENABLED is true")
            }

            val caFile = File(config.caPath)
            if (!caFile.exists() || !caFile.isFile || !caFile.canRead()) {
                throw IllegalArgumentException("KAFKA_TLS_CA_PATH points to non-existent, unreadable, or non-file path: ${config.caPath}")
            }

            if (config.certPath.isNotBlank() || config.keyPath.isNotBlank()) {
                if (config.certPath.isBlank()) {
                    throw IllegalArgumentException("KAFKA_TLS_CERT_PATH must be set when KAFKA_TLS_KEY_PATH is provided")
                }
                if (config.keyPath.isBlank()) {
                    throw IllegalArgumentException("KAFKA_TLS_KEY_PATH must be set when KAFKA_TLS_CERT_PATH is provided")
                }

                val certFile = File(config.certPath)
                if (!certFile.exists() || !certFile.isFile || !certFile.canRead()) {
                    throw IllegalArgumentException("KAFKA_TLS_CERT_PATH points to non-existent, unreadable, or non-file path: ${config.certPath}")
                }

                val keyFile = File(config.keyPath)
                if (!keyFile.exists() || !keyFile.isFile || !keyFile.canRead()) {
                    throw IllegalArgumentException("KAFKA_TLS_KEY_PATH points to non-existent, unreadable, or non-file path: ${config.keyPath}")
                }
            }
        }
    }
}

fun buildGrpcSslContext(config: GrpcTlsConfig): SslContext {
    val sslContextBuilder = GrpcSslContexts.forServer(File(config.certPath), File(config.keyPath))

    if (config.clientCaPath.isNotBlank()) {
        sslContextBuilder.trustManager(File(config.clientCaPath))
        sslContextBuilder.clientAuth(ClientAuth.REQUIRE)
    } else {
        sslContextBuilder.clientAuth(ClientAuth.NONE)
    }

    return sslContextBuilder.build()
}

fun buildKafkaSslProperties(props: Properties, config: KafkaTlsConfig) {
    props["security.protocol"] = "SSL"
    props["ssl.truststore.type"] = "PEM"
    props["ssl.truststore.location"] = config.caPath

    if (config.certPath.isNotBlank() && config.keyPath.isNotBlank()) {
        props["ssl.keystore.type"] = "PEM"
        props["ssl.keystore.certificate.chain"] = FileInputStream(config.certPath).use { it.readAllBytes().toString(Charsets.UTF_8) }
        props["ssl.keystore.private.key"] = FileInputStream(config.keyPath).use { it.readAllBytes().toString(Charsets.UTF_8) }
    }
}

fun main() {
    // Load and validate TLS configurations first
    val grpcTlsConfig = GrpcTlsConfig.load()
    val kafkaTlsConfig = KafkaTlsConfig.load()

    GrpcTlsConfig.validate(grpcTlsConfig)
    KafkaTlsConfig.validate(kafkaTlsConfig)

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

    // Apply Kafka TLS configuration if enabled
    if (kafkaTlsConfig.enabled) {
        buildKafkaSslProperties(props, kafkaTlsConfig)
    }

    val consumer = KafkaConsumer<String, ByteArray>(props).apply {
        subscribe(listOf(topic))
    }

    // Initialize health check service
    val healthStatusManager = HealthStatusManager()
    healthStatusManager.setStatus(HealthStatusManager.SERVICE_NAME_ALL_SERVICES, ServingStatus.NOT_SERVING)

    // Configure ports
    val plaintextPort = System.getenv("FRAUD_DETECTION_GRPC_PORT")?.toIntOrNull() ?: DEFAULT_HEALTH_PORT
    val tlsPort = System.getenv("FRAUD_DETECTION_GRPC_TLS_PORT")?.toIntOrNull() ?: DEFAULT_GRPC_TLS_PORT

    val servers = mutableListOf<Server>()

    // Initialize fraud detection service
    val fraudDetectionService = FraudDetectionServiceImpl()

    // Start plaintext gRPC server if enabled
    if (grpcTlsConfig.plaintextEnabled) {
        val plaintextServer = ServerBuilder.forPort(plaintextPort)
            .addService(healthStatusManager.healthService)
            .addService(fraudDetectionService)
            .intercept(CheckTransactionValidationInterceptor())
            .build()
            .start()
        servers.add(plaintextServer)
        logger.info("Plaintext gRPC server started on port $plaintextPort")
    }

    // Start TLS gRPC server if enabled
    if (grpcTlsConfig.enabled) {
        val sslContext = buildGrpcSslContext(grpcTlsConfig)
        val tlsServer = NettyServerBuilder.forPort(tlsPort)
            .addService(healthStatusManager.healthService)
            .addService(fraudDetectionService)
            .intercept(CheckTransactionValidationInterceptor())
            .sslContext(sslContext)
            .build()
            .start()
        servers.add(tlsServer)
        logger.info("TLS gRPC server started on port $tlsPort")
    }

    if (servers.isEmpty()) {
        logger.error("No gRPC servers configured. Either GRPC_PLAINTEXT_ENABLED or GRPC_TLS_ENABLED must be true.")
        exitProcess(1)
    }

    // Start HTTP health check server on same port as plaintext gRPC service (8080 for test)
    val healthCheckPort = plaintextPort
    val httpServer = io.grpc.netty.shaded.io.netty.bootstrap.ServerBootstrap()
        .group(io.grpc.netty.shaded.io.netty.channel.nio.NioEventLoopGroup(), io.grpc.netty.shaded.io.netty.channel.nio.NioEventLoopGroup())
        .channel(io.grpc.netty.shaded.io.netty.channel.socket.nio.NioServerSocketChannel::class.java)
        .childHandler(object : io.grpc.netty.shaded.io.netty.channel.ChannelInitializer<io.grpc.netty.shaded.io.netty.channel.socket.SocketChannel>() {
            override fun initChannel(ch: io.grpc.netty.shaded.io.netty.channel.socket.SocketChannel) {
                val pipeline = ch.pipeline()
                pipeline.addLast(io.grpc.netty.shaded.io.netty.handler.codec.http.HttpServerCodec())
                pipeline.addLast(io.grpc.netty.shaded.io.netty.handler.codec.http.HttpObjectAggregator(1024 * 1024))
                pipeline.addLast(HealthCheckHandler())
            }
        })
        .bind(healthCheckPort)
        .sync()
        .channel()
    logger.info("HTTP health check endpoints available on port $healthCheckPort")

    // Initialize graceful shutdown manager
    val shutdownManager = GracefulShutdownManagerImpl()
    shutdownManager.registerResources(servers.first(), consumer, httpServer) // TODO: handle multiple servers if needed
    // Register signal handlers for SIGINT (2) and SIGTERM (15)
    val signalHandler = SignalHandler { signal ->
        isShuttingDown.set(true)
        val shutdownResult = shutdownManager.shutdown()
        val exitCode = if (shutdownResult.shutdownSuccess) {
            0
        } else {
            if (signal.number == 2) 130 else 143
        }
        exitProcess(exitCode)
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
            isShuttingDown.set(true)
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
            kafkaConnected.set(isHealthy)
            val newStatus = if (isHealthy) ServingStatus.SERVING else ServingStatus.NOT_SERVING

            healthStatusManager.setStatus(HealthStatusManager.SERVICE_NAME_ALL_SERVICES, newStatus)

            Thread.sleep(HEALTH_CHECK_INTERVAL_MS)
        }
    }
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
                        activeOperations.incrementAndGet()
                        try {
                            val validationResult = validateOrderMessage(record.value())

                            when (validationResult) {
                                is Result.Success -> {
                                    val validatedOrder = validationResult.value

                                    val newCount = accumulator + 1
                                    if (getFeatureFlagValue("kafkaQueueProblems") > 0) {
                                        logger.info("FeatureFlag 'kafkaQueueProblems' is enabled, sleeping 1 second")
                                        Thread.sleep(1000)
                                    }
                                    logger.info("Consumed record with orderId: ${validatedOrder.orderId}, and updated total count to: $newCount")
                                    newCount
                                }
                                is Result.Failure -> {
                                    logger.warn(
                                        "Validation failed for record from topic ${record.topic()}, partition ${record.partition()}, offset ${record.offset()}: ${validationResult.error.message}",
                                        validationResult.error
                                    )
                                    // Skip processing, commit offset normally
                                    accumulator + 1
                                }
                            }
                        } finally {
                            activeOperations.decrementAndGet()
                        }
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
 * gRPC service implementation for fraud detection
 */
class FraudDetectionServiceImpl : FraudDetectionServiceGrpcKt.FraudDetectionServiceCoroutineImplBase() {
    override suspend fun checkFraud(request: CheckFraudRequest): CheckFraudResponse {
        validateRequest(request)

        // Proceed with normal fraud scoring (existing logic would be here)
        return CheckFraudResponse.newBuilder()
            .setFraudScore(0.0f)
            .setIsFraud(false)
            .build()
    }

    private fun validateRequest(request: CheckFraudRequest) {
        // Validate user ID
        if (request.userId.isNullOrBlank()) {
            logValidationFailure("user_id", "user_id is required")
            throw Status.INVALID_ARGUMENT
                .withDescription("user_id is required")
                .asRuntimeException()
        }
        
        // Validate order ID
        if (request.orderId.isNullOrBlank()) {
            logValidationFailure("order_id", "order_id is required")
            throw Status.INVALID_ARGUMENT
                .withDescription("order_id is required")
                .asRuntimeException()
        }
        
        // Validate transaction amount
        if (request.amount <= 0) {
            logValidationFailure("transaction_amount", "transaction_amount must be greater than 0")
            throw Status.INVALID_ARGUMENT
                .withDescription("transaction_amount must be greater than 0")
                .asRuntimeException()
        }
        
        // Validate address fields
        val address = request.address
        if (address == null) {
            logValidationFailure("address", "address is required")
            throw Status.INVALID_ARGUMENT
                .withDescription("address is required")
                .asRuntimeException()
        }
        if (address.street.isNullOrBlank()) {
            logValidationFailure("address.street", "address.street is required")
            throw Status.INVALID_ARGUMENT
                .withDescription("address.street is required")
                .asRuntimeException()
        }
        if (address.city.isNullOrBlank()) {
            logValidationFailure("address.city", "address.city is required")
            throw Status.INVALID_ARGUMENT
                .withDescription("address.city is required")
                .asRuntimeException()
        }
        if (address.zipCode.isNullOrBlank()) {
            logValidationFailure("address.zip_code", "address.zip_code is required")
            throw Status.INVALID_ARGUMENT
                .withDescription("address.zip_code is required")
                .asRuntimeException()
        }
        if (address.country.isNullOrBlank()) {
            logValidationFailure("address.country", "address.country is required")
            throw Status.INVALID_ARGUMENT
                .withDescription("address.country is required")
                .asRuntimeException()
        }
    }
    
    private fun logValidationFailure(invalidField: String, errorMessage: String) {
        MDC.put("request_id", GRPC_CONTEXT_REQUEST_ID_KEY.get() ?: "unknown")
        MDC.put("invalid_field", invalidField)
        MDC.put("error_message", errorMessage)
        MDC.put("source", "request-validation")
        
        logger.warn("Request validation failed: $errorMessage")
        
        MDC.remove("request_id")
        MDC.remove("invalid_field")
        MDC.remove("error_message")
        MDC.remove("source")
    }

    private fun isValidUUIDv4(uuid: String): Boolean {
        val uuidV4Regex = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$".toRegex()
        return uuidV4Regex.matches(uuid)
    }

    private fun isValidOrderId(orderId: String): Boolean {
        if (orderId.length != 12) return false
        val orderIdRegex = "^[A-Z0-9]{12}$".toRegex()
        return orderIdRegex.matches(orderId)
    }

    private fun luhnCheck(cardNumber: String): Boolean {
        var sum = 0
        var alternate = false
        for (i in cardNumber.length - 1 downTo 0) {
            var n = cardNumber[i].digitToInt()
            if (alternate) {
                n *= 2
                if (n > 9) {
                    n -= 9
                }
            }
            sum += n
            alternate = !alternate
        }
        return sum % 10 == 0
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
