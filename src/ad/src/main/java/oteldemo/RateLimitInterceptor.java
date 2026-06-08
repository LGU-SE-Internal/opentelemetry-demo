/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package oteldemo;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.ratelimiter.RateLimiterConfig;
import io.github.resilience4j.ratelimiter.RateLimiterRegistry;
import io.grpc.*;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

public class RateLimitInterceptor implements ServerInterceptor {

    private static final Logger logger = LogManager.getLogger(RateLimitInterceptor.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final Pattern ENDPOINT_NAME_PATTERN = Pattern.compile("[^a-zA-Z0-9]");

    private final RateLimiterRegistry rateLimiterRegistry;
    private final Integer globalDefaultRps;
    private final Map<String, Integer> endpointSpecificLimits = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, ConcurrentHashMap<String, RateLimiter>> ipRateLimiters = new ConcurrentHashMap<>();

    public RateLimitInterceptor() {
        // Load configuration from environment variables
        String globalDefaultEnv = System.getenv("AD_SERVICE_RATE_LIMIT_GLOBAL_DEFAULT_RPS");
        this.globalDefaultRps = globalDefaultEnv != null ? Integer.parseInt(globalDefaultEnv) : null;

        // Load endpoint-specific limits
        for (Map.Entry<String, String> entry : System.getenv().entrySet()) {
            String key = entry.getKey();
            if (key.startsWith("AD_SERVICE_RATE_LIMIT_") && key.endsWith("_RPS")) {
                String endpointPart = key.substring("AD_SERVICE_RATE_LIMIT_".length(), key.length() - "_RPS".length());
                // Convert snake uppercase to method name format
                String endpointName = ENDPOINT_NAME_PATTERN.matcher(endpointPart).replaceAll("").toLowerCase();
                try {
                    int limit = Integer.parseInt(entry.getValue());
                    endpointSpecificLimits.put(endpointName, limit);
                } catch (NumberFormatException e) {
                    logger.warn("Invalid rate limit value for {}: {}", key, entry.getValue());
                }
            }
        }

        RateLimiterConfig defaultConfig = RateLimiterConfig.custom()
                .limitForPeriod(1)
                .limitRefreshPeriod(Duration.ofMillis(1))
                .timeoutDuration(Duration.ZERO)
                .build();
        this.rateLimiterRegistry = RateLimiterRegistry.of(defaultConfig);
    }

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
            ServerCall<ReqT, RespT> call,
            Metadata headers,
            ServerCallHandler<ReqT, RespT> next) {

        String fullMethodName = call.getMethodDescriptor().getFullMethodName();
        String endpointName = fullMethodName.substring(fullMethodName.lastIndexOf('/') + 1).toLowerCase();

        Integer limit = endpointSpecificLimits.get(endpointName);
        if (limit == null) {
            limit = globalDefaultRps;
        }

        // No rate limit configured, proceed normally
        if (limit == null || limit <= 0) {
            return next.startCall(call, headers);
        }

        // Extract client IP
        String clientIp = extractClientIp(call, headers);

        // Get or create rate limiter for this IP and endpoint
        RateLimiter rateLimiter = getRateLimiter(endpointName, clientIp, limit);

        // Check if request is allowed
        if (rateLimiter.acquirePermission()) {
            return next.startCall(call, headers);
        } else {
            // Rate limit exceeded, log event
            logRateLimitExceeded(clientIp, fullMethodName, limit);

            // Return RESOURCE_EXHAUSTED status
            call.close(
                    Status.RESOURCE_EXHAUSTED.withDescription(
                            String.format("Rate limit exceeded for endpoint %s, limit: %d RPS per client IP",
                                    fullMethodName, limit)
                    ),
                    new Metadata()
            );
            return new ServerCall.Listener<>() {};
        }
    }

    private String extractClientIp(ServerCall<?, ?> call, Metadata headers) {
        // Check X-Forwarded-For header first
        Metadata.Key<String> xForwardedForKey = Metadata.Key.of("X-Forwarded-For", Metadata.ASCII_STRING_MARSHALLER);
        String xForwardedFor = headers.get(xForwardedForKey);
        if (xForwardedFor != null && !xForwardedFor.isEmpty()) {
            // Get the first IP in the list
            return xForwardedFor.split(",")[0].trim();
        }
        // Fallback to remote address
        return call.getAttributes().get(Grpc.TRANSPORT_ATTR_REMOTE_ADDR).toString().split(":")[0];
    }

    private RateLimiter getRateLimiter(String endpoint, String clientIp, int limitRps) {
        return ipRateLimiters
                .computeIfAbsent(endpoint, k -> new ConcurrentHashMap<>())
                .computeIfAbsent(clientIp, k -> {
                    RateLimiterConfig config = RateLimiterConfig.custom()
                            .limitForPeriod(limitRps)
                            .limitRefreshPeriod(Duration.ofSeconds(1))
                            .timeoutDuration(Duration.ZERO)
                            .build();
                    return rateLimiterRegistry.rateLimiter(endpoint + "_" + clientIp, config);
                });
    }

    private void logRateLimitExceeded(String clientIp, String endpoint, int limitRps) {
        try {
            Map<String, Object> logEvent = Map.of(
                    "event_type", "rate_limit_exceeded",
                    "client_ip", clientIp,
                    "endpoint", endpoint,
                    "limit_rps", limitRps,
                    "timestamp", Instant.now().toString()
            );
            logger.info(objectMapper.writeValueAsString(logEvent));
        } catch (Exception e) {
            logger.warn("Failed to log rate limit event", e);
        }
    }
}
