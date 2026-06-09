// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Polly;
using Npgsql;
using System.Diagnostics.Metrics;

namespace Accounting;

public static class PostgresRetryPolicy
{
    public static readonly IReadOnlySet<string> TransientErrorCodes = new HashSet<string> 
    { 
        "40001", // Serialization failure
        "40P01", // Deadlock detected
        "08006", // Connection failure
        "57P01", // Admin shutdown
        "57P03", // Cannot connect now
        "53300"  // Too many connections
    };

    public static IAsyncPolicy CreateWriteRetryPolicy(IMeterFactory meterFactory)
    {
        var meter = meterFactory.Create("Accounting");
        var retryAttemptsCounter = meter.CreateCounter<long>("accounting_db_retry_attempts", description: "Number of PostgreSQL write retry attempts");
        var failedRetriesCounter = meter.CreateCounter<long>("accounting_db_failed_retries_total", description: "Number of times all PostgreSQL write retries failed");

        var retryPolicy = Policy
            .Handle<PostgresException>(ex => TransientErrorCodes.Contains(ex.SqlState))
            .WaitAndRetryAsync(
                retryCount: 3,
                sleepDurationProvider: (attemptNumber, _) =>
                {
                    var baseDelay = TimeSpan.FromMilliseconds(100 * Math.Pow(2, attemptNumber - 1));
                    var jitter = TimeSpan.FromMilliseconds(baseDelay.TotalMilliseconds * 0.2 * (Random.Shared.NextDouble() - 0.5));
                    return baseDelay + jitter;
                },
                onRetryAsync: (exception, timeSpan, attemptNumber, context) =>
                {
                    var pgException = (PostgresException)exception;
                    retryAttemptsCounter.Add(1, 
                        new KeyValuePair<string, object?>("error_code", pgException.SqlState), 
                        new KeyValuePair<string, object?>("success", true));
                    return Task.CompletedTask;
                });

        var fallbackPolicy = Policy
            .Handle<PostgresException>(ex => TransientErrorCodes.Contains(ex.SqlState))
            .FallbackAsync(
                fallbackAction: (ctx, ct) => throw ctx.Exception,
                onFallbackAsync: (exception, context) =>
                {
                    var pgException = (PostgresException)exception.Exception;
                    retryAttemptsCounter.Add(1, 
                        new KeyValuePair<string, object?>("error_code", pgException.SqlState), 
                        new KeyValuePair<string, object?>("success", false));
                    failedRetriesCounter.Add(1);
                    return Task.CompletedTask;
                });

        return fallbackPolicy.WrapAsync(retryPolicy);
    }
}
