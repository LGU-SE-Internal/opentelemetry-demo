/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package oteldemo;

import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.ratelimiter.RateLimiterConfig;
import io.github.resilience4j.ratelimiter.RateLimiterRegistry;
import io.grpc.*;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.apache.logging.log4j.message.MapMessage;

import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class RateLimitInterceptor implements ServerInterceptor {
    private static final Logger logger = LogManager.getLogger(RateLimitInterceptor.class);
    private static final Pattern ENDPOINT_PATTERN = Pattern.compile("^/([^/]+)/([^/]+)$");
    private static final String GLOBAL_DEFAULT_RPS_ENV = "AD_SERVICE_RATE_LIMIT_GLOBAL_DEFAULT_RPS";
    private static final String ENDPOINT_RPS_ENV_PREFIX = "AD_SERVICE_RATE_LIMIT_";
    private static final int DEFAULT_LIMIT = Integer.MAX_VALUE;

    private final RateLimiterRegistry rateLimiterRegistry;
    private final Map<String, Map<String, RateLimiter>> ipRateLimiters = new ConcurrentHashMap<>();
    private final Integer globalDefaultRps;

    public RateLimitInterceptor() {
        this.rateLimiterRegistry = RateLimiterRegistry.ofDefaults();
        
        // Read global default limit
        String globalDefaultStr = System.getenv(GLOBAL_DEFAULT_RPS_ENV);
        if (globalDefaultStr != null && !globalDefaultStr.isEmpty()) {
            this.globalDefaultRps = Integer.parseInt(globalDefaultStr);
        } else {
            this.globalDefaultRps = null;
        }
    }

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
            ServerCall<ReqT, RespT> call,
            Metadata headers,
            ServerCallHandler<ReqT, RespT> next) {
        
        String fullMethodName = call.getMethodDescriptor().getFullMethodName();
        String endpointName = extractEndpointName(fullMethodName);
        
        // Get rate limit for this endpoint
        int limit = getLimitForEndpoint(endpointName);
        
        if (limit == DEFAULT_LIMIT) {
            // No rate limit applied
            return next.startCall(call, headers);
        }
        
        // Extract client IP
        String clientIp = extractClientIp(call, headers);
        
        // Get or create rate limiter for this IP and endpoint
        RateLimiter rateLimiter = getOrCreateRateLimiter(clientIp, endpointName, limit);
        
        // Try to acquire a permit
        if (rateLimiter.acquirePermission()) {
            // Permit acquired, proceed with call
            return next.startCall(call, headers);
        } else {
            // Rate limit exceeded
            logRateLimitExceeded(clientIp, fullMethodName, limit);
            
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

    private String extractEndpointName(String fullMethodName) {
        Matcher matcher = ENDPOINT_PATTERN.matcher(fullMethodName);
        if (matcher.matches()) {
            return matcher.group(2);
        }
        return fullMethodName;
    }

    private int getLimitForEndpoint(String endpointName) {
        // Convert endpoint name to snake uppercase
        String envName = ENDPOINT_RPS_ENV_PREFIX + toSnakeUpperCase(endpointName) + "_RPS";
        String limitStr = System.getenv(envName);
        
        if (limitStr != null && !limitStr.isEmpty()) {
            try {
                return Integer.parseInt(limitStr);
            } catch (NumberFormatException e) {
                logger.warn("Invalid value for {}: {}", envName, limitStr);
            }
        }
        
        // Fallback to global default if set
        if (globalDefaultRps != null) {
            return globalDefaultRps;
        }
        
        return DEFAULT_LIMIT;
    }

    private String toSnakeUpperCase(String input) {
        return input.replaceAll("([a-z0-9])([A-Z])", "$1_$2").toUpperCase();
    }

    private String extractClientIp(ServerCall<?, ?> call, Metadata headers) {
        // Check X-Forwarded-For header first
        Metadata.Key<String> xForwardedForKey = Metadata.Key.of("X-Forwarded-For", Metadata.ASCII_STRING_MARSHALLER);
        String xForwardedFor = headers.get(xForwardedForKey);
        
        if (xForwardedFor != null && !xForwardedFor.isEmpty()) {
            // Take the first IP in the list
            return xForwardedFor.split(",")[0].trim();
        }
        
        // Fallback to remote address
        Attributes attributes = call.getAttributes();
        if (attributes != null) {
            SocketAddress remoteAddr = attributes.get(Grpc.TRANSPORT_ATTR_REMOTE_ADDR);
            if (remoteAddr instanceof InetSocketAddress inetAddr) {
                return inetAddr.getAddress().getHostAddress();
            }
        }
        
        return "unknown";
    }

    private RateLimiter getOrCreateRateLimiter(String clientIp, String endpointName, int limit) {
        // Get or create map for endpoint
        Map<String, RateLimiter> endpointLimiters = ipRateLimiters.computeIfAbsent(
            endpointName,
            k -> new ConcurrentHashMap<>()
        );
        
        // Get or create rate limiter for IP
        return endpointLimiters.computeIfAbsent(
            clientIp,
            k -> {
                RateLimiterConfig config = RateLimiterConfig.custom()
                    .limitForPeriod(limit)
                    .limitRefreshPeriod(Duration.ofSeconds(1))
                    .build();
                
                return rateLimiterRegistry.rateLimiter(
                    String.format("%s-%s", endpointName, clientIp),
                    config
                );
            }
        );
    }

    private void logRateLimitExceeded(String clientIp, String endpoint, int limitRps) {
        logger.info(new MapMessage<>()
            .with("event_type", "rate_limit_exceeded")
            .with("client_ip", clientIp)
            .with("endpoint", endpoint)
            .with("limit_rps", limitRps)
        );
    }
}
