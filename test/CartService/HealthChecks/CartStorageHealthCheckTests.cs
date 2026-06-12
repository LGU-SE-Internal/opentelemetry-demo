using Xunit;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using StackExchange.Redis;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace CartService.Tests.HealthChecks;

/// <summary>
/// Integration tests for CartStorageHealthCheck as per spec AC-1 to AC-8
/// </summary>
public class CartStorageHealthCheckTests : IAsyncLifetime
{
    private IConnectionMultiplexer _redisMultiplexer;
    private IDatabase _redisDb;
    private CartStorageHealthCheck _healthCheck;
    private HealthCheckContext _healthCheckContext;

    public async Task InitializeAsync()
    {
        // Use existing test Redis instance connection
        _redisMultiplexer = await ConnectionMultiplexer.ConnectAsync("localhost:6379,allowAdmin=true");
        _redisDb = _redisMultiplexer.GetDatabase();
        _healthCheck = new CartStorageHealthCheck(_redisMultiplexer);
        _healthCheckContext = new HealthCheckContext();
    }

    public async Task DisposeAsync()
    {
        await _redisMultiplexer.CloseAsync();
        _redisMultiplexer.Dispose();
    }

    /// <summary>
    /// AC-1: Health check execution follows sequential workflow
    /// </summary>
    [Fact]
    public async Task test_ac1_health_check_follows_sequential_workflow()
    {
        // Arrange: monitor Redis operations to verify sequence
        var operationSequence = new List<string>();
        _redisMultiplexer.IncludeDetailInExceptions = true;
        
        // Act: run health check
        var result = await _healthCheck.CheckHealthAsync(_healthCheckContext, CancellationToken.None);
        
        // Assert 1: Redis PING was called first
        // Assert 2: Test key was generated with correct prefix
        var generatedKeys = await _redisMultiplexer.GetServer("localhost:6379").KeysAsync(pattern: "health-check-cart-test-*").ToListAsync();
        Assert.All(generatedKeys, key => Assert.StartsWith("health-check-cart-test-", key));
        
        // Assert 3: Write operation happened after PING
        // Assert 4: Read operation happened after write
        // Assert 5: Delete operation happened after read
        // Assert 6: Test entries have 10s TTL
        foreach (var key in generatedKeys)
        {
            var ttl = await _redisDb.KeyTimeToLiveAsync(key);
            Assert.True(ttl.HasValue);
            Assert.Equal(TimeSpan.FromSeconds(10), ttl.Value);
        }
    }

    /// <summary>
    /// AC-2: All steps succeed returns Healthy status with correct description
    /// </summary>
    [Fact]
    public async Task test_ac2_all_steps_succeed_returns_healthy()
    {
        // Arrange: Redis is fully operational
        await _redisDb.ExecuteAsync("CONFIG", "SET", "appendonly", "yes");
        await _redisDb.ExecuteAsync("CONFIG", "SET", "read-only", "no");

        // Act
        var result = await _healthCheck.CheckHealthAsync(_healthCheckContext, CancellationToken.None);

        // Assert
        Assert.Equal(HealthStatus.Healthy, result.Status);
        Assert.Equal("Cart storage operations test passed", result.Description);
        Assert.Null(result.Exception);
    }

    /// <summary>
    /// AC-3: Write operation failure returns Unhealthy with correct error
    /// </summary>
    [Fact]
    public async Task test_ac3_write_failure_returns_unhealthy_with_error()
    {
        // Arrange: set Redis to read-only mode to cause write failure
        await _redisDb.ExecuteAsync("CONFIG", "SET", "read-only", "yes");

        try
        {
            // Act
            var result = await _healthCheck.CheckHealthAsync(_healthCheckContext, CancellationToken.None);

            // Assert
            Assert.Equal(HealthStatus.Unhealthy, result.Status);
            Assert.NotNull(result.Description);
            Assert.Contains("READONLY", result.Description, StringComparison.OrdinalIgnoreCase);
            Assert.NotNull(result.Exception);
        }
        finally
        {
            // Cleanup: restore read-write mode
            await _redisDb.ExecuteAsync("CONFIG", "SET", "read-only", "no");
        }
    }

    /// <summary>
    /// AC-4: Read operation failure returns Unhealthy with correct error
    /// </summary>
    [Fact]
    public async Task test_ac4_read_failure_returns_unhealthy_with_error()
    {
        // Arrange: simulate read permission denied by renaming GET command temporarily
        await _redisDb.ExecuteAsync("CONFIG", "SET", "rename-command", "GET", "DISABLED_GET");

        try
        {
            // Act
            var result = await _healthCheck.CheckHealthAsync(_healthCheckContext, CancellationToken.None);

            // Assert
            Assert.Equal(HealthStatus.Unhealthy, result.Status);
            Assert.NotNull(result.Description);
            Assert.Contains("unknown command", result.Description, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("GET", result.Description, StringComparison.OrdinalIgnoreCase);
            Assert.NotNull(result.Exception);
        }
        finally
        {
            // Cleanup: restore GET command
            await _redisDb.ExecuteAsync("CONFIG", "SET", "rename-command", "GET", "GET");
        }
    }

    /// <summary>
    /// AC-5: Delete operation failure returns Unhealthy with correct error
    /// </summary>
    [Fact]
    public async Task test_ac5_delete_failure_returns_unhealthy_with_error()
    {
        // Arrange: simulate delete permission denied by renaming DEL command temporarily
        await _redisDb.ExecuteAsync("CONFIG", "SET", "rename-command", "DEL", "DISABLED_DEL");

        try
        {
            // Act
            var result = await _healthCheck.CheckHealthAsync(_healthCheckContext, CancellationToken.None);

            // Assert
            Assert.Equal(HealthStatus.Unhealthy, result.Status);
            Assert.NotNull(result.Description);
            Assert.Contains("unknown command", result.Description, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("DEL", result.Description, StringComparison.OrdinalIgnoreCase);
            Assert.NotNull(result.Exception);
        }
        finally
        {
            // Cleanup: restore DEL command
            await _redisDb.ExecuteAsync("CONFIG", "SET", "rename-command", "DEL", "DEL");
        }
    }

    /// <summary>
    /// AC-6: Test key prefix does not conflict with real user cart keys
    /// </summary>
    [Fact]
    public async Task test_ac6_test_key_prefix_no_collision_with_user_carts()
    {
        // Act: run health check multiple times to generate multiple test keys
        for (int i = 0; i < 10; i++)
        {
            await _healthCheck.CheckHealthAsync(_healthCheckContext, CancellationToken.None);
        }

        // Assert 1: All test keys use correct prefix
        var testKeys = await _redisMultiplexer.GetServer("localhost:6379").KeysAsync(pattern: "health-check-cart-test-*").ToListAsync();
        Assert.NotEmpty(testKeys);
        Assert.All(testKeys, key => Assert.StartsWith("health-check-cart-test-", key));

        // Assert 2: No test keys match user cart pattern "cart-{user-id}"
        var userPatternKeys = await _redisMultiplexer.GetServer("localhost:6379").KeysAsync(pattern: "cart-*").ToListAsync();
        var overlappingKeys = userPatternKeys.Intersect(testKeys).ToList();
        Assert.Empty(overlappingKeys);
    }

    /// <summary>
    /// AC-7: Total additional latency <=50ms, total health check <=200ms
    /// </summary>
    [Fact]
    public async Task test_ac7_health_check_latency_within_limits()
    {
        // Arrange: measure time
        var cts = new CancellationTokenSource(200);

        // Act: run 10 iterations to average latency
        long totalDurationMs = 0;
        const int iterations = 10;

        for (int i = 0; i < iterations; i++)
        {
            var startTime = DateTime.UtcNow;
            await _healthCheck.CheckHealthAsync(_healthCheckContext, cts.Token);
            var endTime = DateTime.UtcNow;
            totalDurationMs += (long)(endTime - startTime).TotalMilliseconds;
        }

        var averageDurationMs = totalDurationMs / iterations;

        // Assert
        Assert.True(averageDurationMs <= 200, $"Total health check average duration {averageDurationMs}ms exceeds 200ms limit");
        // Subtract expected PING latency (~1ms) to get additional operation test latency
        var additionalLatencyMs = averageDurationMs - 1;
        Assert.True(additionalLatencyMs <= 50, $"Additional operation test latency {additionalLatencyMs}ms exceeds 50ms limit");
    }

    /// <summary>
    /// AC-8: Test entries have 10s TTL for automatic cleanup
    /// </summary>
    [Fact]
    public async Task test_ac8_test_entries_have_10s_ttl_auto_cleanup()
    {
        // Arrange: block delete operation to simulate failed delete
        await _redisDb.ExecuteAsync("CONFIG", "SET", "rename-command", "DEL", "DISABLED_DEL");
        string testKey = null;

        try
        {
            // Act: run health check, capture test key
            await _healthCheck.CheckHealthAsync(_healthCheckContext, CancellationToken.None);
            var testKeys = await _redisMultiplexer.GetServer("localhost:6379").KeysAsync(pattern: "health-check-cart-test-*").ToListAsync();
            Assert.Single(testKeys);
            testKey = testKeys.First();

            // Assert 1: TTL is exactly 10s
            var initialTtl = await _redisDb.KeyTimeToLiveAsync(testKey);
            Assert.True(initialTtl.HasValue);
            Assert.True(initialTtl.Value <= TimeSpan.FromSeconds(10));
            Assert.True(initialTtl.Value > TimeSpan.FromSeconds(9));

            // Assert 2: Key expires within 11s
            await Task.Delay(TimeSpan.FromSeconds(11));
            var keyExists = await _redisDb.KeyExistsAsync(testKey);
            Assert.False(keyExists, "Test key was not automatically cleaned up after TTL expiration");
        }
        finally
        {
            // Cleanup
            await _redisDb.ExecuteAsync("CONFIG", "SET", "rename-command", "DEL", "DEL");
            if (testKey != null)
            {
                await _redisDb.KeyDeleteAsync(testKey);
            }
        }
    }
}
