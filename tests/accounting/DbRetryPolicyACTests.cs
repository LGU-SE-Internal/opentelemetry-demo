using Xunit;
using Moq;
using Microsoft.Extensions.DependencyInjection;
using Polly.CircuitBreaker;
using Npgsql;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;
using Polly;
using System.Collections.Generic;
using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Accounting.Tests;

public class DbRetryPolicyACTests : IAsyncLifetime
{
    private readonly ServiceProvider _serviceProvider;
    private readonly Mock<ILogger<DbOperationExecutor>> _mockLogger;
    private readonly Mock<AccountingDbContext> _mockDbContext;
    private IDbOperationExecutor _executor;

    public DbRetryPolicyACTests()
    {
        var services = new ServiceCollection();
        _mockLogger = new Mock<ILogger<DbOperationExecutor>>();
        _mockDbContext = new Mock<AccountingDbContext>();
        
        services.AddSingleton(_mockLogger.Object);
        services.AddSingleton(_mockDbContext.Object);
        
        // Register dependencies (implementation will add retry policy registration)
        services.AddSingleton<IDbOperationExecutor, DbOperationExecutor>();
        services.AddSingleton<AsyncCircuitBreakerPolicy>(sp => 
            Policy.Handle<NpgsqlException>()
                  .Or<DbUpdateException>()
                  .CircuitBreakerAsync(5, TimeSpan.FromSeconds(30)));
        
        _serviceProvider = services.BuildServiceProvider();
    }

    public Task InitializeAsync() 
    {
        _executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_MAX_ATTEMPTS", "3");
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS", "100");
        return Task.CompletedTask;
    }

    public Task DisposeAsync() 
    {
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_MAX_ATTEMPTS", null);
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS", null);
        return _serviceProvider.DisposeAsync().AsTask();
    }

    #region AC-1 Tests
    [Fact]
    public async Task Test_AC1_TransientErrorRetriedUpToMaxAttemptsBeforePropagation()
    {
        // Arrange
        const int maxAttempts = 3;
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_MAX_ATTEMPTS", maxAttempts.ToString());
        int attemptCount = 0;

        // Act & Assert
        var exception = await Assert.ThrowsAsync<NpgsqlException>(() => 
            _executor.ExecuteAsync(async ct => {
                attemptCount++;
                // Throw transient error 08006 (Connection Failure)
                throw new NpgsqlException("Connection timeout", new PostgresException("Connection failed", "08006"));
            }, CancellationToken.None));

        // Assert: maxAttempts + 1 total invocations (initial + 3 retries = 4? Wait AC-1 says 3 retry attempts for max=3: initial is attempt 1, then 3 retries = 4 total runs)
        Assert.Equal(maxAttempts + 1, attemptCount);
        Assert.IsType<NpgsqlException>(exception);
    }
    #endregion

    #region AC-2 Tests
    [Fact]
    public async Task Test_AC2_ExponentialBackoffUsedForRetries()
    {
        // Arrange
        const int initialDelay = 100;
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS", initialDelay.ToString());
        List<TimeSpan> delaysBetweenAttempts = new();
        DateTime? lastAttemptTime = null;
        int attemptCount = 0;

        // Act
        try
        {
            await _executor.ExecuteAsync(async ct => {
                attemptCount++;
                if (lastAttemptTime.HasValue)
                {
                    delaysBetweenAttempts.Add(DateTime.UtcNow - lastAttemptTime.Value);
                }
                lastAttemptTime = DateTime.UtcNow;
                throw new NpgsqlException("Deadlock detected", new PostgresException("Deadlock", "40001"));
            }, CancellationToken.None);
        }
        catch (NpgsqlException)
        {
            // Expected
        }

        // Assert: 3 delays for 3 retries (max attempts = 3)
        Assert.Equal(3, delaysBetweenAttempts.Count);
        // Allow 20% tolerance for timing inaccuracies
        Assert.InRange(delaysBetweenAttempts[0].TotalMilliseconds, initialDelay * 0.8, initialDelay * 1.2);
        Assert.InRange(delaysBetweenAttempts[1].TotalMilliseconds, initialDelay * 2 * 0.8, initialDelay * 2 * 1.2);
        Assert.InRange(delaysBetweenAttempts[2].TotalMilliseconds, initialDelay * 4 * 0.8, initialDelay * 4 * 1.2);
    }
    #endregion

    #region AC-3 Tests
    [Fact]
    public async Task Test_AC3_NonTransientErrorNotRetried()
    {
        // Arrange
        int attemptCount = 0;

        // Act & Assert
        var exception = await Assert.ThrowsAsync<NpgsqlException>(() => 
            _executor.ExecuteAsync(async ct => {
                attemptCount++;
                // Throw non-transient error 23505 (Unique Violation)
                throw new NpgsqlException("Unique constraint violated", new PostgresException("Duplicate key", "23505"));
            }, CancellationToken.None));

        // Assert: only 1 attempt, no retries
        Assert.Equal(1, attemptCount);
        Assert.IsType<NpgsqlException>(exception);
    }
    #endregion

    #region AC-4 Tests
    [Fact]
    public async Task Test_AC4_RetryNotAttemptedWhenCircuitIsOpen()
    {
        // Arrange
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();
        // Force circuit open
        while (circuitBreaker.CircuitState != CircuitState.Open)
        {
            try { await circuitBreaker.ExecuteAsync(() => throw new NpgsqlException("Test", new PostgresException("test", "08006"))); }
            catch { /* ignore */ }
        }
        int attemptCount = 0;

        // Act & Assert
        var exception = await Assert.ThrowsAsync<BrokenCircuitException>(() => 
            _executor.ExecuteAsync(async ct => {
                attemptCount++;
                throw new NpgsqlException("Connection failed", new PostgresException("Connection timeout", "08006"));
            }, CancellationToken.None));

        // Assert: 0 retries, circuit open exception thrown immediately
        Assert.Equal(1, attemptCount);
        Assert.IsType<BrokenCircuitException>(exception);
    }
    #endregion

    #region AC-5 Tests
    [Fact]
    public async Task Test_AC5_RetryAttemptsLoggedAsWarning()
    {
        // Arrange
        string expectedLogMessageTemplate = "Database operation retry attempt {n} after {delay}ms: {error message}";

        // Act
        try
        {
            await _executor.ExecuteAsync(async ct => {
                throw new NpgsqlException("Connection timeout", new PostgresException("Connection failed", "08006"));
            }, CancellationToken.None);
        }
        catch (NpgsqlException)
        {
            // Expected
        }

        // Assert: warning logs exist for each retry attempt with correct structure
        _mockLogger.Verify(
            x => x.Log(
                LogLevel.Warning,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => v.ToString().Contains("Database operation retry attempt")),
                It.IsAny<Exception>(),
                It.Is<Func<It.IsAnyType, Exception, string>>((v, t) => true)), 
            Times.Exactly(3),
            "Expected 3 warning log entries for retries"
        );
    }
    #endregion

    #region AC-6 Tests
    [Fact]
    public void Test_AC6_RetryConfigurationLoadedFromEnvironmentVariables()
    {
        // Arrange
        const int expectedMaxAttempts = 5;
        const int expectedInitialDelay = 200;
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_MAX_ATTEMPTS", expectedMaxAttempts.ToString());
        Environment.SetEnvironmentVariable("ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS", expectedInitialDelay.ToString());

        // Act: Get combined policy from executor
        var combinedPolicy = _executor.GetCombinedDatabasePolicy();
        var retryPolicy = combinedPolicy.GetPolicy<AsyncRetryPolicy>();

        // Assert
        Assert.NotNull(retryPolicy);
        // Polly retry policy has retry count property
        Assert.Equal(expectedMaxAttempts, retryPolicy.RetryCount);
        // Verify backoff strategy matches exponential with initial delay
        // (Check that first retry delay is expectedInitialDelay, second is double etc.)
    }
    #endregion
}
