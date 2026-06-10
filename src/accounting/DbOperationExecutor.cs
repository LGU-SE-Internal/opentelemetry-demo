// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Polly.CircuitBreaker;
using Microsoft.Extensions.Logging;
using Polly;
using Npgsql;
using Microsoft.EntityFrameworkCore;
using Polly.Retry;

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
    Task<T> ExecuteAsync<T>(Func<CancellationToken, Task<T>> operation, CancellationToken cancellationToken = default);

    /// <summary>
    /// Executes a void database operation wrapped in circuit breaker policy
    /// </summary>
    /// <param name="operation">Async database operation to execute</param>
    /// <param name="cancellationToken">Cancellation token</param>
    /// <exception cref="CircuitBreakerOpenException">Thrown when circuit is open</exception>
    Task ExecuteAsync(Func<CancellationToken, Task> operation, CancellationToken cancellationToken = default);
    
    /// <summary>
    /// Gets the combined database policy wrapping retry inside circuit breaker
    /// </summary>
    /// <returns>Combined Polly policy</returns>
    IAsyncPolicy GetCombinedDatabasePolicy();
}

public class DbOperationExecutor : IDbOperationExecutor
{
    private readonly AsyncCircuitBreakerPolicy _circuitBreakerPolicy;
    private readonly ILogger<DbOperationExecutor> _logger;
    private readonly int _maxRetryAttempts;
    private readonly int _initialDelayMs;
    private readonly AsyncRetryPolicy _retryPolicy;
    private readonly IAsyncPolicy _combinedPolicy;

    private static readonly HashSet<string> TransientErrorCodes = new()
    {
        "08006", // Connection Failure
        "40001", // Serialization Failure / Deadlock
        "53300", // Too Many Connections
        "53400", // Out of Memory
        "57P03", // Cannot Connect Now / Database Starting Up
        "XX000"  // Internal Error - transient cases only
    };

    public DbOperationExecutor(AsyncCircuitBreakerPolicy circuitBreakerPolicy, ILogger<DbOperationExecutor> logger, int maxRetryAttempts = 3, int initialDelayMs = 100)
    {
        _circuitBreakerPolicy = circuitBreakerPolicy;
        _logger = logger;
        _maxRetryAttempts = maxRetryAttempts;
        _initialDelayMs = initialDelayMs;

        _retryPolicy = Policy
            .Handle<NpgsqlException>(ex => TransientErrorCodes.Contains(ex.SqlState))
            .WaitAndRetryAsync(
                retryCount: _maxRetryAttempts,
                sleepDurationProvider: (attemptNumber, _) => TimeSpan.FromMilliseconds(_initialDelayMs * Math.Pow(2, attemptNumber - 1)),
                onRetryAsync: (exception, delay, attemptNumber, _) =>
                {
                    _logger.LogWarning(exception, "Database operation retry attempt {AttemptNumber} after {Delay}ms: {ErrorMessage}",
                        attemptNumber, delay.TotalMilliseconds, exception.Message);
                    return Task.CompletedTask;
                });

        _combinedPolicy = GetCombinedDatabasePolicy();
    }

    public IAsyncPolicy GetCombinedDatabasePolicy()
    {
        // Retry policy is placed inside circuit breaker to avoid retries when circuit is open
        return Policy.WrapAsync(_circuitBreakerPolicy, _retryPolicy);
    }

    public async Task<T> ExecuteAsync<T>(Func<CancellationToken, Task<T>> operation, CancellationToken cancellationToken = default)
    {
        try
        {
            return await _combinedPolicy.ExecuteAsync(operation, cancellationToken);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open, database operation failed fast");
            throw new CircuitBreakerOpenException(_circuitBreakerPolicy.CircuitState, ex.RetryAfter ?? TimeSpan.Zero, "Database circuit breaker open");
        }
    }

    public async Task ExecuteAsync(Func<CancellationToken, Task> operation, CancellationToken cancellationToken = default)
    {
        try
        {
            await _combinedPolicy.ExecuteAsync(operation, cancellationToken);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open, database operation failed fast");
            throw new CircuitBreakerOpenException(_circuitBreakerPolicy.CircuitState, ex.RetryAfter ?? TimeSpan.Zero, "Database circuit breaker open");
        }
    }
}
