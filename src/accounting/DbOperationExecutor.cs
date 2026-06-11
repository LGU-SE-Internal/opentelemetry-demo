// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Polly.CircuitBreaker;
using Microsoft.Extensions.Logging;
using Polly;
using Npgsql;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using System.Collections.Generic;
using System;

namespace Accounting;

public interface IDbOperationExecutor
{
    /// <summary>
    /// Executes a database operation wrapped in circuit breaker policy
    /// </summary>
    /// <param name="operation">Async database operation to execute</param>
    /// <param name="cancellationToken">Cancellation token</param>
    /// <returns>Result of the database operation</returns>
    /// <exception cref="CircuitBreakerOpenException">Thrown when circuit is open</exception>
    Task<T> ExecuteAsync<T>(Func<Task<T>> operation, CancellationToken cancellationToken = default);

    /// <summary>
    /// Executes a void database operation wrapped in circuit breaker policy
    /// </summary>
    /// <param name="operation">Async database operation to execute</param>
    /// <param name="cancellationToken">Cancellation token</param>
    /// <exception cref="CircuitBreakerOpenException">Thrown when circuit is open</exception>
    Task ExecuteAsync(Func<Task> operation, CancellationToken cancellationToken = default);

    /// <summary>
    /// Executes a database operation wrapped in retry and circuit breaker policies
    /// </summary>
    /// <param name="operationName">Name of the operation for logging context</param>
    /// <param name="operation">Async database operation to execute</param>
    /// <param name="cancellationToken">Cancellation token</param>
    /// <returns>Result of the database operation</returns>
    /// <exception cref="CircuitBreakerOpenException">Thrown when circuit is open</exception>
    Task<T> ExecuteAsync<T>(string operationName, Func<CancellationToken, Task<T>> operation, CancellationToken cancellationToken = default);

    /// <summary>
    /// Executes a void database operation wrapped in retry and circuit breaker policies
    /// </summary>
    /// <param name="operationName">Name of the operation for logging context</param>
    /// <param name="operation">Async database operation to execute</param>
    /// <param name="cancellationToken">Cancellation token</param>
    /// <exception cref="CircuitBreakerOpenException">Thrown when circuit is open</exception>
    Task ExecuteAsync(string operationName, Func<CancellationToken, Task> operation, CancellationToken cancellationToken = default);
}

public class DbOperationExecutor : IDbOperationExecutor
{
    private readonly AsyncCircuitBreakerPolicy _circuitBreakerPolicy;
    private readonly ILogger<DbOperationExecutor> _logger;
    private readonly AsyncPolicy _combinedPolicy;
    private readonly int _maxRetryAttempts;
    private readonly int _initialDelayMs;
    private readonly int _maxDelayMs;

    private static readonly HashSet<string> TransientPostgresErrorCodes = new()
    {
        "08006", // Connection failure
        "57P01", // Admin shutdown
        "57P03", // Cannot connect now
        "08001", // SQL client unable to establish connection
        "40001", // Serialization failure
        "08004"  // Server rejected connection
    };

    public DbOperationExecutor(AsyncCircuitBreakerPolicy circuitBreakerPolicy, ILogger<DbOperationExecutor> logger)
    {
        _circuitBreakerPolicy = circuitBreakerPolicy;
        _logger = logger;
        _maxRetryAttempts = 3;
        _initialDelayMs = 100;
        _maxDelayMs = 2000;
        _combinedPolicy = CreateCombinedPolicy();
    }

    public DbOperationExecutor(IConfiguration configuration, ILogger<DbOperationExecutor> logger)
    {
        _logger = logger;

        // Read and parse retry parameters from config with defaults
        _maxRetryAttempts = int.TryParse(configuration["ACCOUNTING_DB_RETRY_MAX_ATTEMPTS"], out var maxAttempts) && maxAttempts > 0 
            ? maxAttempts 
            : 3;
        
        _initialDelayMs = int.TryParse(configuration["ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS"], out var initialDelay) && initialDelay > 0
            ? initialDelay
            : 100;
        
        _maxDelayMs = int.TryParse(configuration["ACCOUNTING_DB_RETRY_MAX_DELAY_MS"], out var maxDelay) && maxDelay > 0
            ? maxDelay
            : 2000;

        // Create circuit breaker policy from config (maintain existing behavior)
        var failureThreshold = int.TryParse(configuration["DB_CIRCUIT_BREAKER_FAILURE_THRESHOLD"], out var threshold) && threshold > 0
            ? threshold
            : 5;
        var samplingDuration = TimeSpan.TryParse(configuration["DB_CIRCUIT_BREAKER_SAMPLING_DURATION"], out var sampling)
            ? sampling
            : TimeSpan.FromSeconds(30);
        var breakDuration = TimeSpan.TryParse(configuration["DB_CIRCUIT_BREAKER_BREAK_DURATION"], out var breakDur)
            ? breakDur
            : TimeSpan.FromMinutes(1);

        _circuitBreakerPolicy = Policy
            .Handle<Exception>(IsTransientPostgresError)
            .CircuitBreakerAsync(
                exceptionsAllowedBeforeBreaking: failureThreshold,
                durationOfBreak: breakDuration,
                samplingDuration: samplingDuration);

        _combinedPolicy = CreateCombinedPolicy();
    }

    public async Task<T> ExecuteAsync<T>(Func<Task<T>> operation, CancellationToken cancellationToken = default)
    {
        return await ExecuteAsync("UnnamedOperation", ct => operation(), cancellationToken);
    }

    public async Task ExecuteAsync(Func<Task> operation, CancellationToken cancellationToken = default)
    {
        await ExecuteAsync("UnnamedOperation", ct => operation(), cancellationToken);
    }

    public async Task<T> ExecuteAsync<T>(string operationName, Func<CancellationToken, Task<T>> operation, CancellationToken cancellationToken = default)
    {
        try
        {
            var context = new Context("DbOperation", new Dictionary<string, object>
            {
                { "OperationName", operationName }
            });
            return await _combinedPolicy.ExecuteAsync(async (ctx, ct) => await operation(ct), context, cancellationToken);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open, database operation failed fast");
            throw new CircuitBreakerOpenException(_circuitBreakerPolicy.CircuitState, ex.RetryAfter ?? TimeSpan.Zero, "Database circuit breaker open");
        }
    }

    public async Task ExecuteAsync(string operationName, Func<CancellationToken, Task> operation, CancellationToken cancellationToken = default)
    {
        try
        {
            var context = new Context("DbOperation", new Dictionary<string, object>
            {
                { "OperationName", operationName }
            });
            await _combinedPolicy.ExecuteAsync(async (ctx, ct) => await operation(ct), context, cancellationToken);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open, database operation failed fast");
            throw new CircuitBreakerOpenException(_circuitBreakerPolicy.CircuitState, ex.RetryAfter ?? TimeSpan.Zero, "Database circuit breaker open");
        }
    }

    private AsyncPolicy CreateCombinedPolicy()
    {
        var retryPolicy = Policy
            .Handle<Exception>(IsTransientPostgresError)
            .WaitAndRetryAsync(
                retryCount: _maxRetryAttempts,
                sleepDurationProvider: (attemptNumber, _, _) =>
                {
                    var delay = Math.Min(_initialDelayMs * Math.Pow(2, attemptNumber - 1), _maxDelayMs);
                    return TimeSpan.FromMilliseconds(delay);
                },
                onRetryAsync: (exception, delay, attemptNumber, context, _) =>
                {
                    var operationName = context.TryGetValue("OperationName", out var opNameObj) 
                        ? opNameObj.ToString() 
                        : "UnnamedOperation";
                    var errorCode = GetPostgresErrorCode(exception);
                    _logger.LogWarning(exception,
                        "Retrying database operation {OperationName} (attempt {AttemptNumber}/{MaxAttempts}) after {DelayMs}ms delay. Error code: {ErrorCode}, Message: {ErrorMessage}",
                        operationName, attemptNumber, _maxRetryAttempts, delay.TotalMilliseconds, errorCode, exception.Message);
                    return Task.CompletedTask;
                });

        // Retry policy wraps circuit breaker policy
        return retryPolicy.WrapAsync(_circuitBreakerPolicy);
    }

    private bool IsTransientPostgresError(Exception ex)
    {
        var errorCode = GetPostgresErrorCode(ex);
        return errorCode != null && TransientPostgresErrorCodes.Contains(errorCode);
    }

    private string? GetPostgresErrorCode(Exception ex)
    {
        if (ex is PostgresException pgEx)
        {
            return pgEx.SqlState;
        }
        if (ex.InnerException is PostgresException innerPgEx)
        {
            return innerPgEx.SqlState;
        }
        if (ex is NpgsqlException npgsqlEx && npgsqlEx.InnerException is PostgresException npgsqlInnerPgEx)
        {
            return npgsqlInnerPgEx.SqlState;
        }
        return null;
    }
}
