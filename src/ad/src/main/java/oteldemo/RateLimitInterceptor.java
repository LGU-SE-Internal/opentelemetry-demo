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
    private static final int DEFAULT_RATE_LIMIT_RPS = 100;
    private final int rateLimitRps;
    private final Map<String, Bucket> clientBuckets = new ConcurrentHashMap<>();

    public RateLimitInterceptor() {
        int configuredRate = DEFAULT_RATE_LIMIT_RPS;
        String envVar = System.getenv("AD_SERVICE_RATE_LIMIT_RPS");
        if (envVar != null && !envVar.isBlank()) {
            try {
                configuredRate = Integer.parseInt(envVar.trim());
                if (configuredRate <= 0) {
                    logger.warn("Invalid AD_SERVICE_RATE_LIMIT_RPS value: {} (must be positive integer), falling back to default: {}",
                            envVar, DEFAULT_RATE_LIMIT_RPS);
                    configuredRate = DEFAULT_RATE_LIMIT_RPS;
                }
            } catch (NumberFormatException e) {
                logger.warn("Failed to parse AD_SERVICE_RATE_LIMIT_RPS value: {}, falling back to default: {}",
                        envVar, DEFAULT_RATE_LIMIT_RPS);
                configuredRate = DEFAULT_RATE_LIMIT_RPS;
            }
        }
        this.rateLimitRps = configuredRate;
        logger.info("Rate limit interceptor initialized with {} RPS per client IP", rateLimitRps);
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
            long currentCount = rateLimitRps - bucket.getAvailableTokens();
            logger.warn("Rate limit exceeded for client IP: {}, method: {}, current count: {}, limit: {}",
                    clientIp,
                    call.getMethodDescriptor().getFullMethodName(),
                    currentCount,
                    rateLimitRps);

            Status status = Status.RESOURCE_EXHAUSTED
                    .withDescription(String.format("Rate limit exceeded: too many requests from client IP %s", clientIp));
            call.close(status, new Metadata());
            return new ServerCall.Listener<>() {};
        }
    }

    private String extractClientIp(ServerCall<?, ?> call, Metadata headers) {
        String xForwardedFor = headers.get(Metadata.Key.of(X_FORWARDED_FOR_HEADER, Metadata.ASCII_STRING_MARSHALLER));
        if (xForwardedFor != null && !xForwardedFor.isBlank()) {
            String[] ips = xForwardedFor.split(",");
            if (ips.length > 0) {
                return ips[0].trim();
            }
        }
        return call.getAttributes().get(Grpc.TRANSPORT_ATTR_REMOTE_ADDR).toString();
    }

    private Bucket createNewBucket() {
        Bandwidth limit = Bandwidth.classic(rateLimitRps, Refill.greedy(rateLimitRps, Duration.ofSeconds(1)));
        return Bucket.builder().addLimit(limit).build();
    }

    public int getRateLimitRps() {
        return rateLimitRps;
    }
}
