/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package oteldemo;

import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.Refill;
import io.grpc.*;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public class RateLimitInterceptor implements ServerInterceptor {
    private static final Logger logger = LogManager.getLogger(RateLimitInterceptor.class);
    private static final String X_FORWARDED_FOR_HEADER = "x-forwarded-for";
    private final Map<String, Bucket> clientBuckets = new ConcurrentHashMap<>();
    private final int rateLimitRps;

    public RateLimitInterceptor(int rateLimitRps) {
        this.rateLimitRps = rateLimitRps;
    }

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
            ServerCall<ReqT, RespT> call,
            Metadata headers,
            ServerCallHandler<ReqT, RespT> next) {

        String clientIp = extractClientIp(call, headers);
        Bucket bucket = clientBuckets.computeIfAbsent(clientIp, k -> createNewBucket());

        if (bucket.tryConsume(1)) {
            return next.startCall(call, headers);
        } else {
            // Rate limit exceeded
            logger.warn("Rate limit exceeded for client IP: {}, method: {}, rate limit: {} RPS",
                    clientIp,
                    call.getMethodDescriptor().getFullMethodName(),
                    rateLimitRps);

            Status status = Status.RESOURCE_EXHAUSTED
                    .withDescription("Rate limit exceeded: too many requests from client IP " + clientIp);
            call.close(status, new Metadata());
            return new ServerCall.Listener<>() {};
        }
    }

    private String extractClientIp(ServerCall<?, ?> call, Metadata headers) {
        String forwardedFor = headers.get(Metadata.Key.of(X_FORWARDED_FOR_HEADER, Metadata.ASCII_STRING_MARSHALLER));
        if (forwardedFor != null && !forwardedFor.isEmpty()) {
            // Get first IP in X-Forwarded-For list (client IP)
            return forwardedFor.split(",")[0].trim();
        }
        // Fall back to remote address
        return call.getAttributes().get(Grpc.TRANSPORT_ATTR_REMOTE_ADDR).toString().split(":")[0];
    }

    private Bucket createNewBucket() {
        Bandwidth limit = Bandwidth.classic(rateLimitRps, Refill.greedy(rateLimitRps, Duration.ofSeconds(1)));
        return Bucket.builder().addLimit(limit).build();
    }
}
