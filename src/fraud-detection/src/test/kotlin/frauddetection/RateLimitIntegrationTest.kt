/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package frauddetection

import io.grpc.*
import io.grpc.Metadata
import io.grpc.stub.StreamObserver
import io.micrometer.core.instrument.Metrics
import io.micrometer.core.instrument.simple.SimpleMeterRegistry
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import oteldemo.frauddetector.v1.CheckFraudRequest
import oteldemo.frauddetector.v1.CheckFraudResponse
import oteldemo.frauddetector.v1.FraudDetectorServiceGrpc
import java.net.InetSocketAddress
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class RateLimitIntegrationTest {
    private lateinit var server: Server
    private lateinit var channel: ManagedChannel
    private lateinit var stub: FraudDetectorServiceGrpc.FraudDetectorServiceBlockingStub
    private lateinit var meterRegistry: SimpleMeterRegistry

    private val testService = object : FraudDetectorServiceGrpc.FraudDetectorServiceImplBase() {
        override fun checkFraud(request: CheckFraudRequest, responseObserver: StreamObserver<CheckFraudResponse>) {
            val response = CheckFraudResponse.newBuilder()
                .setFraudScore(0.0f)
                .build()
            responseObserver.onNext(response)
            responseObserver.onCompleted()
        }
    }

    @BeforeEach
    fun setup() {
        meterRegistry = SimpleMeterRegistry()
        Metrics.globalRegistry.add(meterRegistry)

        // Start test server with rate limit interceptor
        server = ServerBuilder.forPort(0)
            .addService(testService)
            .intercept(RateLimitServerInterceptor())
            .build()
            .start()

        channel = ManagedChannelBuilder.forAddress("localhost", server.port)
            .usePlaintext()
            .build()
        stub = FraudDetectorServiceGrpc.newBlockingStub(channel)
    }

    @AfterEach
    fun teardown() {
        server.shutdownNow()
        channel.shutdownNow()
        Metrics.globalRegistry.remove(meterRegistry)
        System.clearProperty("FRAUD_DETECTION_RATE_LIMIT_RPS")
    }

    @Test
    fun test_ac1_single_ip_exceeding_rps_returns_resource_exhausted() {
        // Set rate limit to 5 RPS
        System.setProperty("FRAUD_DETECTION_RATE_LIMIT_RPS", "5")

        // Send 10 requests quickly
        val successCount = AtomicInteger(0)
        val errorCount = AtomicInteger(0)

        repeat(10) {
            try {
                stub.checkFraud(CheckFraudRequest.newBuilder().build())
                successCount.incrementAndGet()
            } catch (e: StatusRuntimeException) {
                assertEquals(Status.Code.RESOURCE_EXHAUSTED, e.status.code)
                errorCount.incrementAndGet()
            }
        }

        // We should have 5 successful, 5 rate limited
        assertEquals(5, successCount.get())
        assertEquals(5, errorCount.get())
    }

    @Test
    fun test_ac2_rps_set_to_0_disables_all_rate_limiting() {
        // Set rate limit to 0 to disable
        System.setProperty("FRAUD_DETECTION_RATE_LIMIT_RPS", "0")

        // Send 100 requests, all should pass
        repeat(100) {
            val response = stub.checkFraud(CheckFraudRequest.newBuilder().build())
            assertNotNull(response)
        }
    }

    @Test
    fun test_ac3_rejected_request_error_message_contains_configured_rps_value() {
        // Set rate limit to 10 RPS
        System.setProperty("FRAUD_DETECTION_RATE_LIMIT_RPS", "10")

        // Consume all tokens first
        repeat(10) {
            stub.checkFraud(CheckFraudRequest.newBuilder().build())
        }

        // Next request should fail with correct message
        val exception = org.junit.jupiter.api.assertThrows<StatusRuntimeException> {
            stub.checkFraud(CheckFraudRequest.newBuilder().build())
        }

        assertEquals(Status.Code.RESOURCE_EXHAUSTED, exception.status.code)
        assertTrue(exception.status.description?.contains("10") == true)
        assertEquals("Rate limit exceeded. Max allowed requests per second: 10", exception.status.description)
    }

    @Test
    fun test_ac4_rate_limit_hit_increments_counter_with_correct_labels() {
        // Set rate limit to 2 RPS
        System.setProperty("FRAUD_DETECTION_RATE_LIMIT_RPS", "2")

        // Consume all tokens
        repeat(2) {
            stub.checkFraud(CheckFraudRequest.newBuilder().build())
        }

        // Trigger rate limit hit
        val exception = org.junit.jupiter.api.assertThrows<StatusRuntimeException> {
            stub.checkFraud(CheckFraudRequest.newBuilder().build())
        }
        assertEquals(Status.Code.RESOURCE_EXHAUSTED, exception.status.code)

        // Verify counter
        val counter = meterRegistry.find("fraud_detection_rate_limit_hits_total").counter()
        assertNotNull(counter)
        assertEquals(1.0, counter.count())
        assertEquals("127.0.0.1", counter.id.getTag("client_ip"))
        assertEquals("oteldemo.frauddetector.v1.FraudDetectorService/CheckFraud", counter.id.getTag("endpoint"))
    }

    @Test
    fun test_ac5_rate_limit_check_runs_before_business_logic_processing() {
        val businessLogicCallCount = AtomicInteger(0)

        // Create test service that tracks calls
        val testServiceWithTracking = object : FraudDetectorServiceGrpc.FraudDetectorServiceImplBase() {
            override fun checkFraud(request: CheckFraudRequest, responseObserver: StreamObserver<CheckFraudResponse>) {
                businessLogicCallCount.incrementAndGet()
                super.checkFraud(request, responseObserver)
            }
        }

        // Set rate limit to 3 RPS
        System.setProperty("FRAUD_DETECTION_RATE_LIMIT_RPS", "3")

        // Restart server with tracked service
        server.shutdownNow()
        server = ServerBuilder.forPort(0)
            .addService(testServiceWithTracking)
            .intercept(RateLimitServerInterceptor())
            .build()
            .start()

        channel = ManagedChannelBuilder.forAddress("localhost", server.port)
            .usePlaintext()
            .build()
        stub = FraudDetectorServiceGrpc.newBlockingStub(channel)

        // Send 10 requests
        repeat(10) {
            runCatching { stub.checkFraud(CheckFraudRequest.newBuilder().build()) }
        }

        // Only first 3 should have hit business logic
        assertEquals(3, businessLogicCallCount.get())
    }

    @Test
    fun test_ac6_rate_limits_are_per_client_ip_independent() {
        // Set rate limit to 5 RPS
        System.setProperty("FRAUD_DETECTION_RATE_LIMIT_RPS", "5")

        // Test direct interceptor IP isolation logic
        val interceptor = RateLimitServerInterceptor()
        val ip1 = "192.168.1.1"
        val ip2 = "192.168.1.2"

        // Mock calls from IP 1: consume 5 tokens
        repeat(5) {
            val mockAttrs = Attributes.newBuilder()
                .set(Grpc.TRANSPORT_ATTR_REMOTE_ADDR, InetSocketAddress(ip1, 12345))
                .build()
            val mockCall = object : ServerCall<Any, Any>() {
                override fun getMethodDescriptor(): MethodDescriptor<Any, Any> =
                    MethodDescriptor.newBuilder<Any, Any>()
                        .setFullMethodName("test/Test")
                        .setType(MethodDescriptor.MethodType.UNARY)
                        .build()
                override fun getAttributes(): Attributes = mockAttrs
                override fun request(p0: Int) {}
                override fun sendHeaders(p0: Metadata?) {}
                override fun sendMessage(p0: Any) {}
                override fun close(p0: Status?, p1: Metadata?) {}
                override fun isCancelled(): Boolean = false
            }
            interceptor.interceptCall(mockCall, Metadata(), object : ServerCallHandler<Any, Any> {
                override fun startCall(p0: ServerCall<Any, Any>?, p1: Metadata?): ServerCall.Listener<Any> {
                    return object : ServerCall.Listener<Any>() {}
                }
            })
        }

        // Next call from IP1 should be rejected
        var ip1Rejected = false
        val mockAttrsIp1 = Attributes.newBuilder()
            .set(Grpc.TRANSPORT_ATTR_REMOTE_ADDR, InetSocketAddress(ip1, 12345))
            .build()
        val mockCallIp1 = object : ServerCall<Any, Any>() {
            override fun getMethodDescriptor(): MethodDescriptor<Any, Any> =
                MethodDescriptor.newBuilder<Any, Any>()
                    .setFullMethodName("test/Test")
                    .setType(MethodDescriptor.MethodType.UNARY)
                    .build()
            override fun getAttributes(): Attributes = mockAttrsIp1
            override fun request(p0: Int) {}
            override fun sendHeaders(p0: Metadata?) {}
            override fun sendMessage(p0: Any) {}
            override fun close(status: Status?, p1: Metadata?) {
                if (status?.code == Status.Code.RESOURCE_EXHAUSTED) {
                    ip1Rejected = true
                }
            }
            override fun isCancelled(): Boolean = false
        }
        interceptor.interceptCall(mockCallIp1, Metadata(), object : ServerCallHandler<Any, Any> {
            override fun startCall(p0: ServerCall<Any, Any>?, p1: Metadata?): ServerCall.Listener<Any> {
                return object : ServerCall.Listener<Any>() {}
            }
        })
        assertTrue(ip1Rejected, "IP1 should be rate limited after 5 requests")

        // Now call from IP2, should not be rejected (independent quota)
        var ip2Rejected = false
        val mockAttrsIp2 = Attributes.newBuilder()
            .set(Grpc.TRANSPORT_ATTR_REMOTE_ADDR, InetSocketAddress(ip2, 12345))
            .build()
        val mockCallIp2 = object : ServerCall<Any, Any>() {
            override fun getMethodDescriptor(): MethodDescriptor<Any, Any> =
                MethodDescriptor.newBuilder<Any, Any>()
                    .setFullMethodName("test/Test")
                    .setType(MethodDescriptor.MethodType.UNARY)
                    .build()
            override fun getAttributes(): Attributes = mockAttrsIp2
            override fun request(p0: Int) {}
            override fun sendHeaders(p0: Metadata?) {}
            override fun sendMessage(p0: Any) {}
            override fun close(status: Status?, p1: Metadata?) {
                if (status?.code == Status.Code.RESOURCE_EXHAUSTED) {
                    ip2Rejected = true
                }
            }
            override fun isCancelled(): Boolean = false
        }
        interceptor.interceptCall(mockCallIp2, Metadata(), object : ServerCallHandler<Any, Any> {
            override fun startCall(p0: ServerCall<Any, Any>?, p1: Metadata?): ServerCall.Listener<Any> {
                return object : ServerCall.Listener<Any>() {}
            }
        })
        assertEquals(false, ip2Rejected, "IP2 should not be rate limited when IP1 is already limited")
    }
}
