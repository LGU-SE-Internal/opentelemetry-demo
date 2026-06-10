// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Polly.CircuitBreaker;
using Microsoft.Extensions.Logging;
using Polly;
using Npgsql;
using Microsoft.EntityFrameworkCore;

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
}

public class DbOperationExecutor : IDbOperationExecutor
{
    private readonly AsyncCircuitBreakerPolicy _circuitBreakerPolicy;
    private readonly ILogger<DbOperationExecutor> _logger;

    public DbOperationExecutor(AsyncCircuitBreakerPolicy circuitBreakerPolicy, ILogger<DbOperationExecutor> logger)
    {
        _circuitBreakerPolicy = circuitBreakerPolicy;
        _logger = logger;
    }

    public async Task<T> ExecuteAsync<T>(Func<Task<T>> operation, CancellationToken cancellationToken = default)
    {
        try
        {
            return await _circuitBreakerPolicy.ExecuteAsync(operation, cancellationToken);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open, database operation failed fast");
            throw new CircuitBreakerOpenException(_circuitBreakerPolicy.CircuitState, ex.RetryAfter ?? TimeSpan.Zero, "Database circuit breaker open");
        }
    }

    public async Task ExecuteAsync(Func<Task> operation, CancellationToken cancellationToken = default)
    {
        try
        {
            await _circuitBreakerPolicy.ExecuteAsync(operation, cancellationToken);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open, database operation failed fast");
            throw new CircuitBreakerOpenException(_circuitBreakerPolicy.CircuitState, ex.RetryAfter ?? TimeSpan.Zero, "Database circuit breaker open");
        }
    }
}
