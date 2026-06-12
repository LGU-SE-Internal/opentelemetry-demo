package frauddetection

import io.grpc.*
import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.DistributionSummary
import io.micrometer.core.instrument.Gauge
import io.micrometer.core.instrument.MeterRegistry
import jakarta.enterprise.context.ApplicationScoped
import jakarta.inject.Inject
import java.time.Duration
import java.util.concurrent.atomic.AtomicLong

@ApplicationScoped
class MetricsInterceptor @Inject constructor(
    private val meterRegistry: MeterRegistry
) : ServerInterceptor {

    private val fraudCheckRequestsTotal: Counter = Counter.builder("fraud_check_requests_total")
        .description("Total count of processed fraud check gRPC requests")
        .register(meterRegistry)

    private val fraudCheckDuration: DistributionSummary = DistributionSummary.builder("fraud_check_request_duration_seconds")
        .description("Distribution of fraud check request processing latency in seconds")
        .publishPercentileHistogram()
        .register(meterRegistry)

    private val successCount = AtomicLong(0)
    private val totalValidCount = AtomicLong(0)

    init {
        Gauge.builder("fraud_check_success_rate_percent") {
            val total = totalValidCount.get()
            if (total == 0) 0.0 else (successCount.get().toDouble() / total.toDouble()) * 100.0
        }
        .description("Percentage of successful fraud check requests over the last 5 minute window")
        .register(meterRegistry)

        // Reset counters every 5 minutes for sliding window
        Thread {
            while (true) {
                Thread.sleep(Duration.ofMinutes(5).toMillis())
                successCount.set(0)
                totalValidCount.set(0)
            }
        }.start()
    }

    override fun <ReqT : Any, RespT : Any> interceptCall(
        call: ServerCall<ReqT, RespT>,
        headers: Metadata,
        next: ServerCallHandler<ReqT, RespT>
    ): ServerCall.Listener<ReqT> {
        val startTime = System.nanoTime()

        val forwardCall = object : ForwardingServerCall.SimpleForwardingServerCall<ReqT, RespT>(call) {
            var status: String = "success"
            var reason: String? = null

            override fun close(status: Status, trailers: Metadata) {
                val durationSeconds = (System.nanoTime() - startTime) / 1e9
                fraudCheckDuration.record(durationSeconds)

                when {
                    status.code == Status.Code.INVALID_ARGUMENT -> {
                        this.status = "invalid"
                        reason = status.description ?: "validation failed"
                    }
                    !status.isOk -> {
                        this.status = "failure"
                        reason = status.description ?: "internal error"
                        totalValidCount.incrementAndGet()
                    }
                    else -> {
                        successCount.incrementAndGet()
                        totalValidCount.incrementAndGet()
                    }
                }

                fraudCheckRequestsTotal
                    .tag("status", this.status)
                    .tag("reason", reason ?: "none")
                    .increment()

                super.close(status, trailers)
            }
        }

        return next.startCall(forwardCall, headers)
    }
}
