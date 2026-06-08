// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
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

public class RedisRetryPolicyTests : IDisposable
{
    private readonly IHost _testHost;
    private readonly Mock<IDatabase> _mockRedisDb;
    private readonly Mock<IConnectionMultiplexer> _mockRedisMultiplexer;
    private readonly List<LogEntry> _capturedLogs = new();
    private const string TestCartId = "test-cart-123";

    public RedisRetryPolicyTests()
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
    public async Task Test_AC1_RetryUpToMaxAttemptsOnTransientErrors()
    {
        // Arrange: Set environment variables for max retry attempts = 3
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_RETRY_ATTEMPTS", "3");
        Environment.SetEnvironmentVariable("CART_REDIS_INITIAL_BACKOFF_MS", "100");
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_BACKOFF_MS", "1000");

        // Setup Redis mock to fail first 3 times with transient error (connection timeout), then succeed
        var attempt = 0;
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ReturnsAsync(() =>
            {
                attempt++;
                if (attempt <= 3)
                    throw new RedisConnectionException(ConnectionFailureType.Timeout, "Transient connection timeout");
                return new HashEntry[] { new("test-item", "test-value") };
            });

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act
        var result = await client.GetCartAsync(new GetCartRequest { UserId = TestCartId });

        // Assert: Operation succeeds, 3 retry attempts logged
        Assert.NotNull(result);
        var retryLogs = _capturedLogs.Where(l => l.Message.Contains("retry attempt")).ToList();
        Assert.Equal(3, retryLogs.Count);
        Assert.All(retryLogs, l => Assert.Contains(TestCartId, l.Message));
    }

    [Fact]
    public async Task Test_AC2_ExponentialBackoffWithJitterApplied()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_RETRY_ATTEMPTS", "3");
        Environment.SetEnvironmentVariable("CART_REDIS_INITIAL_BACKOFF_MS", "100");
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_BACKOFF_MS", "1000");

        // Setup Redis to fail 3 times
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.SocketFailure, "Socket error"));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act
        var exception = await Assert.ThrowsAsync<Grpc.Core.RpcException>(() =>
            client.GetCartAsync(new GetCartRequest { UserId = TestCartId }));

        // Assert: Retry delays follow exponential pattern with +/-20% jitter
        var retryLogs = _capturedLogs.Where(l => l.Message.Contains("retry attempt") && l.Message.Contains("delay")).ToList();
        Assert.Equal(3, retryLogs.Count);

        // Extract delay values from logs
        var delays = retryLogs.Select(l => int.Parse(l.Message.Split("delay: ")[1].Split("ms")[0])).ToList();

        // First retry ~100ms (80-120ms)
        Assert.InRange(delays[0], 80, 120);
        // Second retry ~200ms (160-240ms)
        Assert.InRange(delays[1], 160, 240);
        // Third retry ~400ms (320-480ms)
        Assert.InRange(delays[2], 320, 480);
        // No delay exceeds max backoff 1000ms
        Assert.All(delays, d => Assert.True(d <= 1000));
    }

    [Fact]
    public async Task Test_AC3_NonTransientErrorsNotRetried()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_RETRY_ATTEMPTS", "3");
        // Setup Redis to throw non-transient error (invalid command)
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisCommandException("ERR unknown command 'INVALID_CMD'"));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act
        var exception = await Assert.ThrowsAsync<Grpc.Core.RpcException>(() =>
            client.GetCartAsync(new GetCartRequest { UserId = TestCartId }));

        // Assert: No retry attempts logged
        var retryLogs = _capturedLogs.Where(l => l.Message.Contains("retry attempt")).ToList();
        Assert.Empty(retryLogs);
    }

    [Fact]
    public async Task Test_AC4_FailedRetriesLogRequiredMetadata()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_RETRY_ATTEMPTS", "2");
        // Setup Redis to fail twice then succeed
        var attempt = 0;
        _mockRedisDb.Setup(db => db.HashSetAsync(It.IsAny<RedisKey>(), It.IsAny<HashEntry[]>(), It.IsAny<CommandFlags>()))
            .ReturnsAsync(() =>
            {
                attempt++;
                if (attempt <= 2)
                    throw new RedisConnectionException(ConnectionFailureType.UnableToConnect, "Connection refused");
                return true;
            });

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act
        await client.AddItemAsync(new AddItemRequest
        {
            UserId = TestCartId,
            Item = new Oteldemo.CartItem { ProductId = "prod-1", Quantity = 1 }
        });

        // Assert: 2 log entries with all required metadata
        var retryLogs = _capturedLogs.Where(l => l.Message.Contains("retry attempt")).ToList();
        Assert.Equal(2, retryLogs.Count);

        foreach (var log in retryLogs)
        {
            Assert.Contains("AddItem", log.Message); // operation name
            Assert.Contains("attempt", log.Message); // attempt number
            Assert.Contains("delay", log.Message); // next retry delay
            Assert.Contains("Connection refused", log.Message); // error message
            Assert.Contains(TestCartId, log.Message); // cart ID
            Assert.True(log.Timestamp > DateTime.MinValue); // timestamp
        }
    }

    [Fact]
    public async Task Test_AC5_RetryConfigurationLoadedFromEnvironmentVariables()
    {
        // Arrange: Set non-default config values
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_RETRY_ATTEMPTS", "5");
        Environment.SetEnvironmentVariable("CART_REDIS_INITIAL_BACKOFF_MS", "200");
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_BACKOFF_MS", "2000");

        // Setup Redis to fail all 5 attempts
        _mockRedisDb.Setup(db => db.HashDeleteAsync(It.IsAny<RedisKey>(), It.IsAny<RedisValue>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.SocketFailure, "Socket error"));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act
        var exception = await Assert.ThrowsAsync<Grpc.Core.RpcException>(() =>
            client.DeleteItemAsync(new DeleteItemRequest { UserId = TestCartId, ProductId = "prod-1" }));

        // Assert: 5 retry attempts logged, first delay between 160-240ms (200ms +/- 20%)
        var retryLogs = _capturedLogs.Where(l => l.Message.Contains("retry attempt")).ToList();
        Assert.Equal(5, retryLogs.Count);

        var firstDelay = int.Parse(retryLogs[0].Message.Split("delay: ")[1].Split("ms")[0]);
        Assert.InRange(firstDelay, 160, 240);
    }

    [Fact]
    public async Task Test_AC6_ThunderingHerdPreventionActive()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_RETRY_ATTEMPTS", "1");
        Environment.SetEnvironmentVariable("CART_REDIS_INITIAL_BACKOFF_MS", "100");
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_BACKOFF_MS", "1000");

        // Setup Redis to fail first attempt for all calls
        var callCount = 0;
        _mockRedisDb.Setup(db => db.HashGetAllAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ReturnsAsync(() =>
            {
                callCount++;
                if (callCount <= 100)
                    throw new RedisConnectionException(ConnectionFailureType.Timeout, "Transient timeout");
                return new HashEntry[] { };
            });

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act: Run 100 concurrent operations
        var tasks = Enumerable.Range(0, 100)
            .Select(i => client.GetCartAsync(new GetCartRequest { UserId = $"test-cart-{i}" }))
            .ToList();

        // We expect all to fail since we only have 1 retry, and first attempt fails
        var exceptions = await Assert.ThrowsAnyAsync<AggregateException>(() => Task.WhenAll(tasks));

        // Assert: Retry delays are spread between 80ms and 120ms
        var retryDelays = _capturedLogs
            .Where(l => l.Message.Contains("retry attempt 1"))
            .Select(l => int.Parse(l.Message.Split("delay: ")[1].Split("ms")[0]))
            .ToList();

        Assert.Equal(100, retryDelays.Count);
        Assert.All(retryDelays, d => Assert.InRange(d, 80, 120));

        // Check that no more than 10% of retries have exactly the same delay
        var delayGroups = retryDelays.GroupBy(d => d).Select(g => g.Count()).ToList();
        Assert.True(delayGroups.Max() <= 10, "No more than 10% of retries should have identical delay");
    }

    [Fact]
    public async Task Test_AC7_OriginalErrorPropagatedAfterRetryExhaustion()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_REDIS_MAX_RETRY_ATTEMPTS", "2");
        const string originalErrorMessage = "Permanent Redis failure: server down";
        // Setup Redis to always fail with connection refused
        _mockRedisDb.Setup(db => db.KeyDeleteAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.UnableToConnect, originalErrorMessage));

        var client = new CartServiceClient(_testHost.GetTestClient().CreateGrpcChannel());

        // Act
        var exception = await Assert.ThrowsAsync<Grpc.Core.RpcException>(() =>
            client.EmptyCartAsync(new EmptyCartRequest { UserId = TestCartId }));

        // Assert: Original error is propagated, exhaustion logged
        Assert.Contains(originalErrorMessage, exception.Message);
        var exhaustionLog = _capturedLogs.FirstOrDefault(l => l.Message.Contains("All retry attempts exhausted"));
        Assert.NotNull(exhaustionLog);
        Assert.Contains("2 attempts", exhaustionLog.Message);
        Assert.Contains(TestCartId, exhaustionLog.Message);
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
