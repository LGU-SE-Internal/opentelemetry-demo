// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Grpc.Core;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Moq;
using Oteldemo;
using StackExchange.Redis;
using Xunit;
using static Oteldemo.CartService;

namespace cart.tests;

public class ValkeyCartStoreCircuitBreakerTests : IDisposable
{
    private readonly IHost _testHost;
    private readonly Mock<IDatabase> _mockRedisDb;
    private readonly Mock<IConnectionMultiplexer> _mockRedisMultiplexer;
    private readonly List<LogEntry> _capturedLogs = new();
    private const string TestCartId = "test-cart-123";

    public ValkeyCartStoreCircuitBreakerTests()
    {
        _mockRedisDb = new Mock<IDatabase>();
        _mockRedisMultiplexer = new Mock<IConnectionMultiplexer>();
        _mockRedisMultiplexer.Setup(m => m.GetDatabase(It.IsAny<int>(), It.IsAny<object>())).Returns(_mockRedisDb.Object);

        _testHost = new HostBuilder()
            .ConfigureWebHost(webBuilder =>
            {
                webBuilder.UseTestServer();
                webBuilder.ConfigureServices(services =>
                {
                    services.AddSingleton(_mockRedisMultiplexer.Object);
                    services.AddSingleton<ILoggerProvider, TestLoggerProvider>(sp => new TestLoggerProvider(_capturedLogs));
                });
            })
            .Build();
        _testHost.StartAsync().Wait();
    }

    public void Dispose()
    {
        _testHost.Dispose();
    }

    [Fact]
    public async Task Test_AC1_CircuitOpensAfter5ConsecutiveFailuresWithin10Seconds()
    {
        // Arrange: Configure Redis to always fail with transient error
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis connection timeout"));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act: Make 5 consecutive failed calls
        for (int i = 0; i < 5; i++)
        {
            try
            {
                await client.GetCartAsync(new GetCartRequest { UserId = TestCartId });
            }
            catch (RpcException)
            {
                // Expected
            }
        }

        // Assert: Circuit is now open - 6th call returns UNAVAILABLE immediately without Redis call
        _mockRedisDb.Invocations.Clear();
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            client.GetCartAsync(new GetCartRequest { UserId = TestCartId }));

        Assert.Equal(StatusCode.Unavailable, exception.StatusCode);
        Assert.Contains("Redis circuit breaker is open", exception.Status.Detail);
        _mockRedisDb.Verify(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()), Times.Never);
    }

    [Fact]
    public async Task Test_AC2_OpenCircuitReturnsUnavailableFor30SecondsWithoutRedisCalls()
    {
        // Arrange: Force circuit open by failing 5 times
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis connection timeout"));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());
        for (int i = 0; i < 5; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }
        }
        _mockRedisDb.Invocations.Clear();

        // Act & Assert: All calls for 30 seconds return UNAVAILABLE with no Redis calls
        var endTime = DateTime.UtcNow.AddSeconds(29);
        int callCount = 0;
        while (DateTime.UtcNow < endTime)
        {
            callCount++;
            var exception = await Assert.ThrowsAsync<RpcException>(() =>
                client.GetCartAsync(new GetCartRequest { UserId = TestCartId }));
            Assert.Equal(StatusCode.Unavailable, exception.StatusCode);
            await Task.Delay(100);
        }

        _mockRedisDb.Verify(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()), Times.Never);
        Assert.True(callCount > 0, "Expected at least one call during open circuit window");
    }

    [Fact]
    public async Task Test_AC3_HalfOpenStateAllows1TestCallAfter30Seconds()
    {
        // Arrange: Open circuit first
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis connection timeout"));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());
        for (int i = 0; i < 5; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }
        }

        // Wait 30 seconds for circuit to go to half-open
        await Task.Delay(TimeSpan.FromSeconds(30));

        // Act 1: First call in half-open state - should allow test call to Redis
        _mockRedisDb.Invocations.Clear();
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ReturnsAsync(new HashEntry[] { });
        await client.GetCartAsync(new GetCartRequest { UserId = TestCartId });

        // Assert: 1 Redis call made, circuit is now closed
        _mockRedisDb.Verify(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()), Times.Once);
        var stateTransitionLogs = _capturedLogs.Where(l => l.Message.Contains("HALF-OPEN → CLOSED")).ToList();
        Assert.Single(stateTransitionLogs);

        // Act 2: Next call works normally, circuit is closed
        _mockRedisDb.Invocations.Clear();
        await client.GetCartAsync(new GetCartRequest { UserId = TestCartId });
        _mockRedisDb.Verify(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()), Times.Once);

        // Now test failure scenario in half-open state
        _capturedLogs.Clear();
        // Re-open circuit
        for (int i = 0; i < 5; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }
        }
        await Task.Delay(TimeSpan.FromSeconds(30));

        // Half-open test call fails
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis still down"));
        try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }

        // Assert: Circuit re-opens
        stateTransitionLogs = _capturedLogs.Where(l => l.Message.Contains("HALF-OPEN → OPEN")).ToList();
        Assert.Single(stateTransitionLogs);
    }

    [Fact]
    public async Task Test_AC4_AllCircuitStateTransitionsAreLoggedAtInformationLevel()
    {
        // Arrange
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis connection timeout"));
        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // 1. Test CLOSED → OPEN transition
        for (int i = 0; i < 5; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }
        }
        var closedToOpenLog = _capturedLogs.FirstOrDefault(l => l.Message.Contains("CLOSED → OPEN"));
        Assert.NotNull(closedToOpenLog);
        Assert.Equal(LogLevel.Information, closedToOpenLog.LogLevel);
        Assert.Contains("timestamp", closedToOpenLog.Message);
        Assert.Contains("reason", closedToOpenLog.Message);

        // 2. Test OPEN → HALF-OPEN transition
        await Task.Delay(TimeSpan.FromSeconds(30));
        var openToHalfOpenLog = _capturedLogs.FirstOrDefault(l => l.Message.Contains("OPEN → HALF-OPEN"));
        Assert.NotNull(openToHalfOpenLog);
        Assert.Equal(LogLevel.Information, openToHalfOpenLog.LogLevel);

        // 3. Test HALF-OPEN → OPEN transition
        try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }
        var halfOpenToOpenLog = _capturedLogs.FirstOrDefault(l => l.Message.Contains("HALF-OPEN → OPEN"));
        Assert.NotNull(halfOpenToOpenLog);
        Assert.Equal(LogLevel.Information, halfOpenToOpenLog.LogLevel);

        // 4. Test HALF-OPEN → CLOSED transition
        await Task.Delay(TimeSpan.FromSeconds(30));
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ReturnsAsync(new HashEntry[] { });
        await client.GetCartAsync(new GetCartRequest { UserId = TestCartId });
        var halfOpenToClosedLog = _capturedLogs.FirstOrDefault(l => l.Message.Contains("HALF-OPEN → CLOSED"));
        Assert.NotNull(halfOpenToClosedLog);
        Assert.Equal(LogLevel.Information, halfOpenToClosedLog.LogLevel);
    }

    [Fact]
    public async Task Test_AC5_ExistingRetryPolicyRunsBeforeCircuitCountsFailure()
    {
        // Arrange: Set retry policy to 3 retries, make Redis fail 3 times then succeed for first 4 operations, then fail all
        int operationCounter = 0;
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ReturnsAsync(() =>
            {
                operationCounter++;
                // First 4 operations: retry 3 times then succeed (so no circuit failure counted)
                if (operationCounter <= 4 * 4) // 4 operations * (3 retries + 1 initial) = 16 calls
                {
                    if (operationCounter % 4 != 0) // Fail first 3 attempts of each operation
                        throw new RedisConnectionException(ConnectionFailureType.Timeout, "Transient error");
                    return new HashEntry[] { };
                }
                // All subsequent attempts fail completely (retries exhausted)
                throw new RedisConnectionException(ConnectionFailureType.Timeout, "Permanent error");
            });

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act: 4 operations that succeed after retries - should not count towards circuit breaker
        for (int i = 0; i < 4; i++)
        {
            await client.GetCartAsync(new GetCartRequest { UserId = TestCartId });
        }

        // Assert: Circuit is still closed, no open state log
        var openLogs = _capturedLogs.Where(l => l.Message.Contains("CLOSED → OPEN")).ToList();
        Assert.Empty(openLogs);

        // Now 5 operations that fail all retries - should open circuit
        for (int i = 0; i < 5; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }
        }

        openLogs = _capturedLogs.Where(l => l.Message.Contains("CLOSED → OPEN")).ToList();
        Assert.Single(openLogs);
    }

    [Fact]
    public async Task Test_AC6_OpenCircuitReturnsUnavailableStatusForAllValkeyCartStoreMethods()
    {
        // Arrange: Open circuit first
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis down"));
        _mockRedisDb.Setup(db => db.HashSetAsync(It.IsAny<RedisKey>(), It.IsAny<HashEntry[]>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis down"));
        _mockRedisDb.Setup(db => db.KeyDeleteAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis down"));
        _mockRedisDb.Setup(db => db.HashDeleteAsync(It.IsAny<RedisKey>(), It.IsAny<RedisValue>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.Timeout, "Redis down"));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());
        for (int i = 0; i < 5; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = TestCartId }); } catch { }
        }

        // Act & Assert: Test all ValkeyCartStore methods return UNAVAILABLE
        var testCases = new List<Func<Task>>
        {
            () => client.GetCartAsync(new GetCartRequest { UserId = TestCartId }),
            () => client.AddItemAsync(new AddItemRequest { UserId = TestCartId, Item = new Oteldemo.CartItem { ProductId = "prod1", Quantity = 1 } }),
            () => client.EmptyCartAsync(new EmptyCartRequest { UserId = TestCartId }),
            () => client.UpdateItemAsync(new UpdateItemRequest { UserId = TestCartId, Item = new Oteldemo.CartItem { ProductId = "prod1", Quantity = 2 } }),
            () => client.DeleteItemAsync(new DeleteItemRequest { UserId = TestCartId, ProductId = "prod1" })
        };

        foreach (var testCase in testCases)
        {
            var exception = await Assert.ThrowsAsync<RpcException>(testCase);
            Assert.Equal(StatusCode.Unavailable, exception.StatusCode);
            Assert.Contains("Redis circuit breaker is open", exception.Status.Detail);
        }
    }

    // Helper classes for test logging
    private class TestLoggerProvider : ILoggerProvider
    {
        private readonly List<LogEntry> _logs;
        public TestLoggerProvider(List<LogEntry> logs) => _logs = logs;
        public ILogger CreateLogger(string categoryName) => new TestLogger(_logs);
        public void Dispose() { }
    }

    private class TestLogger : ILogger
    {
        private readonly List<LogEntry> _logs;
        public TestLogger(List<LogEntry> logs) => _logs = logs;
        public IDisposable BeginScope<TState>(TState state) => null;
        public bool IsEnabled(LogLevel logLevel) => true;
        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception exception, Func<TState, Exception, string> formatter)
        {
            _logs.Add(new LogEntry
            {
                Timestamp = DateTime.UtcNow,
                LogLevel = logLevel,
                Message = formatter(state, exception),
                Exception = exception
            });
        }
    }

    private class LogEntry
    {
        public DateTime Timestamp { get; set; }
        public LogLevel LogLevel { get; set; }
        public string Message { get; set; } = string.Empty;
        public Exception? Exception { get; set; }
    }
}
