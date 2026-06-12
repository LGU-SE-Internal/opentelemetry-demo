/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */
package frauddetection

import dev.openfeature.contrib.providers.flagd.FlagdProvider
import dev.openfeature.sdk.EvaluationContext
import org.eclipse.microprofile.faulttolerance.CircuitBreaker
import org.eclipse.microprofile.faulttolerance.Fallback
import org.eclipse.microprofile.faulttolerance.Retry
import java.time.temporal.ChronoUnit

class FlagdClientWrapper(
    private val flagdProvider: FlagdProvider,
    private val config: FlagdClientConfig = FlagdClientConfig.load()
) {

    @Retry(
        maxRetries = 3,
        delay = 100,
        delayUnit = ChronoUnit.MILLIS,
        maxDuration = 1000,
        durationUnit = ChronoUnit.MILLIS,
        retryOn = [Exception::class]
    )
    @CircuitBreaker(
        failureRatio = 0.5,
        requestVolumeThreshold = 10,
        delay = 30,
        delayUnit = ChronoUnit.SECONDS,
        successThreshold = 3,
        skipOn = []
    )
    @Fallback(fallbackMethod = "getBooleanValueFallback")
    fun getBooleanValue(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
        return try {
            flagdProvider.getBooleanValue(flagKey, defaultValue, ctx)
        } catch (e: Exception) {
            throw e
        }
    }

    fun getBooleanValueFallback(flagKey: String, defaultValue: Boolean, ctx: EvaluationContext): Boolean {
        return defaultValue
    }
}
