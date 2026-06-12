using Microsoft.Extensions.Diagnostics.HealthChecks;
using StackExchange.Redis;
using System;
using System.Threading;
using System.Threading.Tasks;

namespace CartService.HealthChecks;

public class CartStorageHealthCheck : IHealthCheck
{
    private readonly IConnectionMultiplexer _redisMultiplexer;
    private const string TestKeyPrefix = "health-check-cart-test-";
    private const string TestValue = "health-check-test-value";
    private static readonly TimeSpan TtlDuration = TimeSpan.FromSeconds(10);

    public CartStorageHealthCheck(IConnectionMultiplexer redisMultiplexer)
    {
        _redisMultiplexer = redisMultiplexer ?? throw new ArgumentNullException(nameof(redisMultiplexer));
    }

    public async Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
    {
        var db = _redisMultiplexer.GetDatabase();
        string testKey = null;

        try
        {
            // Step 1: Run Redis PING connectivity check first
            await db.PingAsync(cancellationToken);

            // Step 2: Generate unique test key
            testKey = $"{TestKeyPrefix}{Guid.NewGuid():N}";

            // Step 3: Write test entry with TTL
            var writeSuccess = await db.StringSetAsync(testKey, TestValue, TtlDuration, When.Always, CommandFlags.None, cancellationToken);
            if (!writeSuccess)
            {
                return HealthCheckResult.Unhealthy("Failed to write test cart entry to Redis");
            }

            // Step 4: Read back and validate
            var readValue = await db.StringGetAsync(testKey, CommandFlags.None, cancellationToken);
            if (!readValue.HasValue || readValue != TestValue)
            {
                return HealthCheckResult.Unhealthy($"Test cart entry read failed: expected value '{TestValue}', got '{readValue}'");
            }

            // Step 5: Delete test entry
            await db.KeyDeleteAsync(testKey, CommandFlags.None, cancellationToken);

            // All steps succeeded
            return HealthCheckResult.Healthy("Cart storage operations test passed");
        }
        catch (Exception ex)
        {
            // Clean up test key if it exists and we have an error
            if (testKey != null)
            {
                try
                {
                    await db.KeyDeleteAsync(testKey, CommandFlags.FireAndForget, cancellationToken);
                }
                catch
                {
                    // Ignore cleanup errors, TTL will handle it
                }
            }

            return HealthCheckResult.Unhealthy(ex.Message, ex);
        }
    }
}
