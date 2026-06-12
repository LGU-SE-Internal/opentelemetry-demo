/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */
package frauddetection

import java.time.Duration

data class FlagdClientConfig(
    val circuitBreakerFailureRateThreshold: Int = 50,
    val circuitBreakerSlidingWindowSize: Int = 10,
    val circuitBreakerWaitDurationInOpenState: Duration = Duration.ofSeconds(30),
    val circuitBreakerPermittedCallsInHalfOpenState: Int = 3,
    val connectionTimeoutMs: Long = 5000,
    val requestTimeoutMs: Long = 10000,
    val retryMaxAttempts: Int = 3
) {
    companion object {
        fun load(): FlagdClientConfig {
            return FlagdClientConfig(
                circuitBreakerFailureRateThreshold = System.getenv("FLAGD_CIRCUIT_BREAKER_FAILURE_RATE_THRESHOLD")?.toIntOrNull()
                    ?: System.getProperty("FLAGD_CIRCUIT_BREAKER_FAILURE_RATE_THRESHOLD")?.toIntOrNull()
                    ?: 50,
                circuitBreakerSlidingWindowSize = System.getenv("FLAGD_CIRCUIT_BREAKER_SLIDING_WINDOW_SIZE")?.toIntOrNull()
                    ?: System.getProperty("FLAGD_CIRCUIT_BREAKER_SLIDING_WINDOW_SIZE")?.toIntOrNull()
                    ?: 10,
                circuitBreakerWaitDurationInOpenState = System.getenv("FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE")
                    ?.let { Duration.parse(it) }
                    ?: System.getProperty("FLAGD_CIRCUIT_BREAKER_WAIT_DURATION_IN_OPEN_STATE")
                        ?.let { Duration.parse(it) }
                    ?: Duration.ofSeconds(30),
                circuitBreakerPermittedCallsInHalfOpenState = System.getenv("FLAGD_CIRCUIT_BREAKER_PERMITTED_CALLS_IN_HALF_OPEN_STATE")?.toIntOrNull()
                    ?: System.getProperty("FLAGD_CIRCUIT_BREAKER_PERMITTED_CALLS_IN_HALF_OPEN_STATE")?.toIntOrNull()
                    ?: 3,
                connectionTimeoutMs = System.getenv("FLAGD_CONNECTION_TIMEOUT_MS")?.toLongOrNull()
                    ?: System.getProperty("FLAGD_CONNECTION_TIMEOUT_MS")?.toLongOrNull()
                    ?: 5000,
                requestTimeoutMs = System.getenv("FLAGD_REQUEST_TIMEOUT_MS")?.toLongOrNull()
                    ?: System.getProperty("FLAGD_REQUEST_TIMEOUT_MS")?.toLongOrNull()
                    ?: 10000,
                retryMaxAttempts = System.getenv("FLAGD_RETRY_MAX_ATTEMPTS")?.toIntOrNull()
                    ?: System.getProperty("FLAGD_RETRY_MAX_ATTEMPTS")?.toIntOrNull()
                    ?: 3
            )
        }
    }
}
