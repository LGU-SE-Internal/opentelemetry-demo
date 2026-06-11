/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package frauddetection

import io.grpc.*
import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.Metrics
import io.github.bucket4j.Bandwidth
import io.github.bucket4j.Bucket
import java.net.InetSocketAddress
import java.time.Duration
import java.util.concurrent.ConcurrentHashMap

class RateLimitServerInterceptor : ServerInterceptor {
    private val rateLimitRps: Int = System.getenv("FRAUD_DETECTION_RATE_LIMIT_RPS")?.toIntOrNull() ?: 100
    private val rateLimitDisabled: Boolean = rateLimitRps <= 0
    private val ipBuckets: ConcurrentHashMap<String, Bucket> = ConcurrentHashMap()

    private val rateLimitHitsCounter: Counter = Counter.builder("fraud_detection_rate_limit_hits_total")
        .description("Total number of requests rejected due to rate limiting")
        .register(Metrics.globalRegistry)

    override fun <ReqT : Any?, RespT : Any?> interceptCall(
        call: ServerCall<ReqT, RespT>,
        headers: Metadata,
        next: ServerCallHandler<ReqT, RespT>
    ): ServerCall.Listener<ReqT> {
        if (rateLimitDisabled) {
            return next.startCall(call, headers)
        }

        val clientIp = extractClientIp(call.attributes) ?: return next.startCall(call, headers)
        val methodName = call.methodDescriptor.fullMethodName

        val bucket = ipBuckets.computeIfAbsent(clientIp) {
            val limit = Bandwidth.simple(rateLimitRps.toLong(), Duration.ofSeconds(1))
            Bucket.builder().addLimit(limit).build()
        }

        if (bucket.tryConsume(1)) {
            return next.startCall(call, headers)
        }

        // Increment metrics
        rateLimitHitsCounter
            .tag("client_ip", clientIp)
            .tag("endpoint", methodName)
            .increment()

        // Reject the call
        val status = Status.RESOURCE_EXHAUSTED
            .withDescription("Rate limit exceeded. Max allowed requests per second: $rateLimitRps")
        call.close(status, headers)

        return object : ServerCall.Listener<ReqT>() {}
    }

    private fun extractClientIp(attributes: Attributes): String? {
        val remoteAddr = attributes.get(Grpc.TRANSPORT_ATTR_REMOTE_ADDR) as? InetSocketAddress
        return remoteAddr?.address?.hostAddress
    }
}
