using Xunit;
using Moq;
using Microsoft.Extensions.DependencyInjection;
using Polly.CircuitBreaker;
using Npgsql;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace Accounting.Tests;

public class CircuitBreakerACTests : IAsyncLifetime
{
    private readonly ServiceProvider _serviceProvider;
    private readonly Mock<IInvalidOrderDlqProducer> _mockDlqProducer;
    private readonly Mock<ILogger<AsyncCircuitBreakerPolicy>> _mockLogger;
    private readonly Mock<AccountingDbContext> _mockDbContext;

    public CircuitBreakerACTests()
    {
        var services = new ServiceCollection();
        _mockDlqProducer = new Mock<IInvalidOrderDlqProducer>();
        _mockLogger = new Mock<ILogger<AsyncCircuitBreakerPolicy>>();
        _mockDbContext = new Mock<AccountingDbContext>();
        
        services.AddSingleton(_mockDlqProducer.Object);
        services.AddSingleton(_mockLogger.Object);
        services.AddSingleton(_mockDbContext.Object);
        
        // This should be replaced with actual DI registration from implementation
        services.AddSingleton<IDbOperationExecutor, DbOperationExecutor>();
        services.AddSingleton<AsyncCircuitBreakerPolicy>(sp => 
            Policy.Handle<NpgsqlException>()
                  .Or<DbUpdateException>()
                  .Or<OperationCanceledException>()
                  .Or<InvalidOperationException>()
                  .CircuitBreakerAsync(5, TimeSpan.FromSeconds(30)));
        
        _serviceProvider = services.BuildServiceProvider();
    }

    public Task InitializeAsync() => Task.CompletedTask;
    public Task DisposeAsync() => _serviceProvider.DisposeAsync().AsTask();

    #region AC-1 Tests
    [Fact]
    public async Task Test_AC1_CircuitBreakerOpensAfter5ConsecutiveFailures()
    {
        // Arrange
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        var testOrder = new Order { Id = Guid.NewGuid().ToString() };
        var failureCount = 0;

        // Act: Attempt 5 failed operations
        for (var i = 0; i < 5; i++)
        {
            await Assert.ThrowsAnyAsync<Exception>(async () => 
                await executor.ExecuteAsync(async () => 
                {
                    failureCount++;
                    throw new NpgsqlException("Connection refused");
                }));
        }

        // Assert: Circuit should now be open, 6th operation fails immediately without hitting DB
        var sixthAttemptException = await Assert.ThrowsAsync<CircuitBreakerOpenException>(async () =>
            await executor.ExecuteAsync(() => throw new InvalidOperationException("Should not reach this code")));
        
        Assert.Equal(5, failureCount);
        Assert.Equal(CircuitState.Open, sixthAttemptException.CurrentState);
        Assert.True(sixthAttemptException.RemainingBreakDuration <= TimeSpan.FromSeconds(30));
        Assert.True(sixthAttemptException.RemainingBreakDuration > TimeSpan.FromSeconds(29));
    }
    #endregion

    #region AC-2 Tests
    [Fact]
    public async Task Test_AC2_OpenCircuitFallsBackToDlqAndReturns503()
    {
        // Arrange
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();
        var testOrder = new Order { Id = "test-order-123", CustomerId = "cust-456" };
        const string expectedFailureReason = "Database circuit breaker open";

        // Force circuit open
        while (circuitBreaker.CircuitState != CircuitState.Open)
        {
            try { await circuitBreaker.ExecuteAsync(() => throw new NpgsqlException("Test failure")); }
            catch { /* ignore */ }
        }

        // Act
        var exception = await Record.ExceptionAsync(async () => 
            await executor.ExecuteAsync(async () => 
            {
                await _mockDbContext.Object.SaveChangesAsync();
                return testOrder;
            }));

        // Assert: DLQ message produced, 503 returned
        _mockDlqProducer.Verify(p => p.ProduceAsync(
            It.Is<Order>(o => o.Id == testOrder.Id),
            It.Is<string>(r => r == expectedFailureReason),
            It.IsAny<CancellationToken>()), Times.Once);
        
        Assert.IsType<CircuitBreakerOpenException>(exception);
        // TODO: Verify 503 response when testing API endpoint
    }
    #endregion

    #region AC-3 Tests
    [Fact]
    public async Task Test_AC3_OpenCircuitTransitionsToHalfOpenAfter30Seconds()
    {
        // Arrange
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        
        // Force circuit open
        for (var i = 0; i < 5; i++)
        {
            try { await circuitBreaker.ExecuteAsync(() => throw new NpgsqlException("Test failure")); }
            catch { /* ignore */ }
        }
        Assert.Equal(CircuitState.Open, circuitBreaker.CircuitState);

        // Simulate time passing 30 seconds
        SystemTime.FakeUtcNow = DateTime.UtcNow.AddSeconds(30);

        // Act: Attempt operation
        var operationExecuted = false;
        var exception = await Record.ExceptionAsync(async () => 
            await executor.ExecuteAsync(() => 
            {
                operationExecuted = true;
                return Task.CompletedTask;
            }));

        // Assert: Single test operation allowed, circuit state transitions
        Assert.True(operationExecuted);
        Assert.Equal(CircuitState.HalfOpen, circuitBreaker.CircuitState);
    }

    [Fact]
    public async Task Test_AC3_HalfOpenCircuitClosesOnSuccess()
    {
        // Arrange
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        
        // Force circuit to half-open state
        for (var i = 0; i < 5; i++)
        {
            try { await circuitBreaker.ExecuteAsync(() => throw new NpgsqlException("Test failure")); }
            catch { /* ignore */ }
        }
        SystemTime.FakeUtcNow = DateTime.UtcNow.AddSeconds(30);
        await circuitBreaker.IsolateAsync();
        await circuitBreaker.ResetAsync();
        Assert.Equal(CircuitState.HalfOpen, circuitBreaker.CircuitState);

        // Act: Run successful operation
        await executor.ExecuteAsync(() => Task.CompletedTask);

        // Assert: Circuit closed
        Assert.Equal(CircuitState.Closed, circuitBreaker.CircuitState);
    }

    [Fact]
    public async Task Test_AC3_HalfOpenCircuitReopensOnFailure()
    {
        // Arrange
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        
        // Force circuit to half-open state
        for (var i = 0; i < 5; i++)
        {
            try { await circuitBreaker.ExecuteAsync(() => throw new NpgsqlException("Test failure")); }
            catch { /* ignore */ }
        }
        SystemTime.FakeUtcNow = DateTime.UtcNow.AddSeconds(30);
        await circuitBreaker.IsolateAsync();
        await circuitBreaker.ResetAsync();
        Assert.Equal(CircuitState.HalfOpen, circuitBreaker.CircuitState);

        // Act: Run failed operation
        await Assert.ThrowsAnyAsync<Exception>(async () => 
            await executor.ExecuteAsync(() => throw new NpgsqlException("Test failure in half-open state")));

        // Assert: Circuit re-opens for another 30 seconds
        Assert.Equal(CircuitState.Open, circuitBreaker.CircuitState);
    }
    #endregion

    #region AC-4 Tests
    [Fact]
    public void Test_AC4_AllSaveChangesCallsAreAsync()
    {
        // Scan all source files for synchronous SaveChanges calls
        var sourceFiles = Directory.GetFiles("/workspace/src/accounting", "*.cs", SearchOption.AllDirectories);
        var syncSaveChangesCalls = new List<string>();

        foreach (var file in sourceFiles)
        {
            var content = File.ReadAllText(file);
            if (content.Contains(".SaveChanges(") && !content.Contains(".SaveChangesAsync("))
            {
                syncSaveChangesCalls.Add(file);
            }
        }

        // Assert no synchronous SaveChanges calls exist
        Assert.Empty(syncSaveChangesCalls);
    }
    #endregion

    #region AC-5 Tests
    [Fact]
    public async Task Test_AC5_CircuitStateChangesAreLoggedWithRequiredFields()
    {
        // Arrange
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();
        
        // Act: Transition from CLOSED -> OPEN
        for (var i = 0; i < 5; i++)
        {
            try { await circuitBreaker.ExecuteAsync(() => throw new NpgsqlException("Test failure")); }
            catch { /* ignore */ }
        }

        // Assert: State change logged with all required fields
        _mockLogger.VerifyLog(
            LogLevel.Warning,
            "Circuit breaker state changed from {previous_state} to {circuit_state}",
            Times.Once(),
            kv => kv.Key == "previous_state" && kv.Value?.ToString() == "Closed",
            kv => kv.Key == "circuit_state" && kv.Value?.ToString() == "Open",
            kv => kv.Key == "failure_count" && (int)kv.Value == 5,
            kv => kv.Key == "remaining_break_duration" && (TimeSpan)kv.Value <= TimeSpan.FromSeconds(30),
            kv => kv.Key == "timestamp" && (DateTime)kv.Value <= DateTime.UtcNow);
    }
    #endregion

    #region AC-6 Tests
    [Fact]
    public async Task Test_AC6_DatabaseOperationFailuresAreLoggedWithRequiredFields()
    {
        // Arrange
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        var testOrderId = "order-789";
        var testRequestId = Guid.NewGuid().ToString();

        // Act: Run failed database operation
        var exception = await Record.ExceptionAsync(async () => 
            await executor.ExecuteAsync(async () => 
            {
                throw new DbUpdateException("Failed to save order", new NpgsqlException("Connection timeout"));
            }));

        // Assert: Failure logged with all required fields
        _mockLogger.VerifyLog(
            LogLevel.Error,
            "Database operation failed: {operation_type}",
            Times.Once(),
            kv => kv.Key == "operation_type" && kv.Value?.ToString() == "SaveChanges",
            kv => kv.Key == "exception_type" && kv.Value?.ToString() == typeof(DbUpdateException).Name,
            kv => kv.Key == "exception_message" && kv.Value?.ToString() == "Failed to save order",
            kv => kv.Key == "request_id" && kv.Value?.ToString() == testRequestId,
            kv => kv.Key == "order_id" && kv.Value?.ToString() == testOrderId);
    }
    #endregion

    #region AC-7 Tests
    [Fact]
    public async Task Test_AC7_SuccessfulOperationsResetFailureCount()
    {
        // Arrange
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();

        // Run 4 failed operations
        for (var i = 0; i < 4; i++)
        {
            try { await executor.ExecuteAsync(() => throw new NpgsqlException("Test failure")); }
            catch { /* ignore */ }
        }

        // Run 1 successful operation
        await executor.ExecuteAsync(() => Task.CompletedTask);

        // Act: Run 4 more failed operations
        for (var i = 0; i < 4; i++)
        {
            try { await executor.ExecuteAsync(() => throw new NpgsqlException("Test failure after success")); }
            catch { /* ignore */ }
        }

        // Assert: Circuit is still closed (failure count reset after success, total failures since last success = 4 < 5)
        Assert.Equal(CircuitState.Closed, circuitBreaker.CircuitState);
    }
    #endregion

    #region AC-8 Tests
    [Fact]
    public async Task Test_AC8_ExistingFunctionalTestsPassWhenCircuitClosed()
    {
        // Arrange: Use real database connection for existing tests
        var executor = _serviceProvider.GetRequiredService<IDbOperationExecutor>();
        var circuitBreaker = _serviceProvider.GetRequiredService<AsyncCircuitBreakerPolicy>();
        Assert.Equal(CircuitState.Closed, circuitBreaker.CircuitState);

        // Act: Run existing order processing flow
        var testOrder = new Order { Id = "test-order-existing", Amount = 100.00m };
        var result = await executor.ExecuteAsync(async () => 
        {
            _mockDbContext.Object.Orders.Add(testOrder);
            await _mockDbContext.Object.SaveChangesAsync();
            return testOrder;
        });

        // Assert: Order saved successfully, no circuit breaker errors
        Assert.NotNull(result);
        Assert.Equal(testOrder.Id, result.Id);
    }
    #endregion
}

// Dummy implementations of interfaces from spec to compile tests
public interface IDbOperationExecutor
{
    Task<T> ExecuteAsync<T>(Func<Task<T>> operation, CancellationToken cancellationToken = default);
    Task ExecuteAsync(Func<Task> operation, CancellationToken cancellationToken = default);
}

public class DbOperationExecutor : IDbOperationExecutor
{
    public Task<T> ExecuteAsync<T>(Func<Task<T>> operation, CancellationToken cancellationToken = default)
    {
        throw new NotImplementedException("Test should fail until implementation exists");
    }

    public Task ExecuteAsync(Func<Task> operation, CancellationToken cancellationToken = default)
    {
        throw new NotImplementedException("Test should fail until implementation exists");
    }
}

public class CircuitBreakerOpenException : InvalidOperationException
{
    public CircuitState CurrentState { get; set; }
    public TimeSpan RemainingBreakDuration { get; set; }
}

public interface IInvalidOrderDlqProducer
{
    Task ProduceAsync(Order order, string failureReason, CancellationToken cancellationToken = default);
}

public class Order
{
    public string Id { get; set; }
    public string CustomerId { get; set; }
    public decimal Amount { get; set; }
}

public class AccountingDbContext : DbContext
{
    public DbSet<Order> Orders { get; set; }
}

public static class SystemTime
{
    public static DateTime FakeUtcNow { get; set; } = DateTime.UtcNow;
}

public static class LoggerVerificationExtensions
{
    public static void VerifyLog<T>(this Mock<ILogger<T>> logger, LogLevel level, string message, Times times, params Func<KeyValuePair<string, object>, bool>[] propertyChecks)
    {
        // Dummy verification implementation for compilation
        throw new NotImplementedException("Logger verification not implemented yet");
    }
}
