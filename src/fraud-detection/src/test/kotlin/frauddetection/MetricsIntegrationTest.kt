package frauddetection

import io.quarkus.test.junit.QuarkusTest
import io.restassured.RestAssured.given
import org.hamcrest.Matchers.containsString
import org.hamcrest.Matchers.equalTo
import org.junit.jupiter.api.Test
import io.grpc.ManagedChannel
import io.grpc.ManagedChannelBuilder
import oteldemo.FraudDetectionServiceGrpc
import oteldemo.FraudService
import java.util.concurrent.TimeUnit

@QuarkusTest
class MetricsIntegrationTest {

    @Test
    fun test_ac1_metrics_endpoint_returns_ok_correct_content_type() {
        given()
                .`when`().get("/q/metrics")
                .then()
                .statusCode(200)
                .header("Content-Type", equalTo("text/plain; version=0.0.4"))
    }

    @Test
    fun test_ac2_fraud_check_requests_total_counter_increments_with_correct_labels() {
        // First get initial counter value (should be 0 or absent before any requests)
        val initialResponse = given().`when`().get("/q/metrics").then().extract().asString()

        // Send a valid fraud check request, should increment status="success"
        val channel: ManagedChannel = ManagedChannelBuilder.forAddress("localhost", 9091)
                .usePlaintext()
                .build()
        val stub = FraudDetectionServiceGrpc.newBlockingStub(channel)
        val validRequest = FraudService.FraudCheckRequest.newBuilder()
                .setUserId("user123")
                .setOrderId("order123")
                .setTransactionId("txn123")
                .setAmount(100.0)
                .build()
        stub.check(validRequest)
        channel.shutdown()
        channel.awaitTermination(5, TimeUnit.SECONDS)

        // Check counter incremented with success label
        given()
                .`when`().get("/q/metrics")
                .then()
                .body(containsString("fraud_check_requests_total{status=\"success\""))

        // Send invalid request, should increment status="invalid"
        val invalidChannel: ManagedChannel = ManagedChannelBuilder.forAddress("localhost", 9091)
                .usePlaintext()
                .build()
        val invalidStub = FraudDetectionServiceGrpc.newBlockingStub(invalidChannel)
        val invalidRequest = FraudService.FraudCheckRequest.newBuilder()
                // Missing required fields to trigger validation failure
                .setOrderId("order123")
                .build()
        try {
            invalidStub.check(invalidRequest)
        } catch (e: Exception) {
            // Expected error for invalid request
        }
        invalidChannel.shutdown()
        invalidChannel.awaitTermination(5, TimeUnit.SECONDS)

        // Check counter incremented with invalid label
        given()
                .`when`().get("/q/metrics")
                .then()
                .body(containsString("fraud_check_requests_total{status=\"invalid\""))
    }

    @Test
    fun test_ac3_fraud_check_request_duration_histogram_present_after_request() {
        // Send a request first
        val channel: ManagedChannel = ManagedChannelBuilder.forAddress("localhost", 9091)
                .usePlaintext()
                .build()
        val stub = FraudDetectionServiceGrpc.newBlockingStub(channel)
        val validRequest = FraudService.FraudCheckRequest.newBuilder()
                .setUserId("user123")
                .setOrderId("order123")
                .setTransactionId("txn123")
                .setAmount(100.0)
                .build()
        stub.check(validRequest)
        channel.shutdown()
        channel.awaitTermination(5, TimeUnit.SECONDS)

        // Check histogram exists
        given()
                .`when`().get("/q/metrics")
                .then()
                .body(containsString("fraud_check_request_duration_seconds_bucket"))
                .body(containsString("fraud_check_request_duration_seconds_sum"))
                .body(containsString("fraud_check_request_duration_seconds_count"))
    }

    @Test
    fun test_ac4_fraud_check_success_rate_gauge_present() {
        // Send multiple requests to populate rate
        val channel: ManagedChannel = ManagedChannelBuilder.forAddress("localhost", 9091)
                .usePlaintext()
                .build()
        val stub = FraudDetectionServiceGrpc.newBlockingStub(channel)
        for (i in 1..5) {
            val validRequest = FraudService.FraudCheckRequest.newBuilder()
                    .setUserId("user$i")
                    .setOrderId("order$i")
                    .setTransactionId("txn$i")
                    .setAmount(100.0 * i)
                    .build()
            stub.check(validRequest)
        }
        channel.shutdown()
        channel.awaitTermination(5, TimeUnit.SECONDS)

        // Check success rate gauge exists, value between 0 and 100
        val metricsResponse = given().`when`().get("/q/metrics").then().extract().asString()
        assert(metricsResponse.contains("fraud_check_success_rate_percent"))
        val rateLine = metricsResponse.split("\n").firstOrNull { it.startsWith("fraud_check_success_rate_percent ") }
        if (rateLine != null) {
            val rate = rateLine.split(" ")[1].toDouble()
            assert(rate in 0.0..100.0)
        }
    }

    @Test
    fun test_ac5_jvm_runtime_metrics_present() {
        given()
                .`when`().get("/q/metrics")
                .then()
                .body(containsString("jvm_memory_used_bytes"))
                .body(containsString("jvm_threads_active"))
                .body(containsString("jvm_gc_collection_seconds_sum"))
                .body(containsString("jvm_gc_collection_seconds_count"))
    }

    @Test
    fun test_ac6_metrics_endpoint_allows_unauthenticated_access() {
        // No Authorization header provided, should return 200 (AC1 already checks 200, this just confirms no auth required)
        given()
                .header("Authorization", "") // Explicitly empty auth header
                .`when`().get("/q/metrics")
                .then()
                .statusCode(200)
    }

    @Test
    fun test_ac8_grpc_endpoints_still_function_correctly() {
        // Verify existing gRPC endpoints work as before, no breaking changes
        val channel: ManagedChannel = ManagedChannelBuilder.forAddress("localhost", 9091)
                .usePlaintext()
                .build()
        val stub = FraudDetectionServiceGrpc.newBlockingStub(channel)

        // Test valid request returns expected response
        val validRequest = FraudService.FraudCheckRequest.newBuilder()
                .setUserId("user123")
                .setOrderId("order123")
                .setTransactionId("txn123")
                .setAmount(100.0)
                .build()
        val response = stub.check(validRequest)
        assert(response.hasFraudScore())
        assert(response.fraudScore in 0.0..1.0)

        channel.shutdown()
        channel.awaitTermination(5, TimeUnit.SECONDS)
    }
}
