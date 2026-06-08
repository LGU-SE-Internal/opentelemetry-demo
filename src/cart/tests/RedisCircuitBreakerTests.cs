using Grpc.Core;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Moq;
using OpenTelemetry.Metrics;
using Polly;
using StackExchange.Redis;
using Xunit;
using Xunit.Abstractions;
using cart;

namespace Cart.Tests;

public class RedisCircuitBreakerTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly Mock<IConnectionMultiplexer> _redisMock;
    private readonly Mock<IDatabase> _redisDbMock;

    public RedisCircuitBreakerTests(WebApplicationFactory<Program> factory, ITestOutputHelper testOutputHelper)
    {
        _redisMock = new Mock<IConnectionMultiplexer>();
        _redisDbMock = new Mock<IDatabase>();
        _redisMock.Setup(m => m.GetDatabase(It.IsAny<int>(), It.IsAny<object>())).Returns(_redisDbMock.Object);
        
        _factory = factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton(_redisMock.Object);
            });
        });
    }

    [Fact]
    public async Task test_ac1_circuit_opens_after_5_consecutive_failures()
    {
        // Arrange
        _redisDbMock.Setup(m => m.StringGetAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.UnableToConnect, "Redis down"));
        
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();

        // Act - perform 5 failed GetCart operations
        for (int i = 0; i < 5; i++)
        {
            try
            {
                await client.GetCartAsync(new GetCartRequest { UserId = "test-user" });
            }
            catch (RpcException)
            {
                // Expected failure
            }
        }

        // Assert - circuit should be open
        var circuitBreakerState = resiliencePipeline.GetPipelineState().CircuitBreaker.State;
        Assert.Equal(CircuitBreakerState.Open, circuitBreakerState);
    }

    [Fact]
    public async Task test_ac2_open_circuit_returns_unavailable_error_no_redis_calls()
    {
        // Arrange - force circuit open
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();
        await resiliencePipeline.IsolateAsync();
        
        _redisDbMock.Invocations.Clear();
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());

        // Act
        var exception = await Assert.ThrowsAsync<RpcException>(() => 
            client.GetCartAsync(new GetCartRequest { UserId = "test-user" }));

        // Assert
        Assert.Equal(StatusCode.Unavailable, exception.StatusCode);
        Assert.Equal("Service temporarily unavailable: cart storage is down. Please retry later.", exception.Status.Detail);
        _redisDbMock.Verify(m => m.StringGetAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()), Times.Never);
    }

    [Fact]
    public async Task test_ac3_circuit_transitions_to_half_open_after_reset_timeout()
    {
        // Arrange
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();
        await resiliencePipeline.IsolateAsync();
        
        // Act - wait 30 seconds (we'll simulate time advance for test)
        await Task.Delay(30000);

        // Assert - circuit should be half open
        var circuitState = resiliencePipeline.GetPipelineState().CircuitBreaker.State;
        Assert.Equal(CircuitBreakerState.HalfOpen, circuitState);
    }

    [Fact]
    public async Task test_ac4_half_open_success_closes_circuit()
    {
        // Arrange - set circuit to half open
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();
        await resiliencePipeline.IsolateAsync();
        // Simulate time passing to get to half open
        await Task.Delay(30000);
        
        _redisDbMock.Setup(m => m.StringGetAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ReturnsAsync(RedisValue.Null);
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());

        // Act - make successful request
        await client.GetCartAsync(new GetCartRequest { UserId = "test-user" });

        // Assert - circuit is closed
        var circuitState = resiliencePipeline.GetPipelineState().CircuitBreaker.State;
        Assert.Equal(CircuitBreakerState.Closed, circuitState);
    }

    [Fact]
    public async Task test_ac5_half_open_failure_returns_to_open()
    {
        // Arrange - set circuit to half open
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();
        await resiliencePipeline.IsolateAsync();
        await Task.Delay(30000);
        
        _redisDbMock.Setup(m => m.StringGetAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.UnableToConnect, "Redis still down"));
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());

        // Act - make failed request
        try
        {
            await client.GetCartAsync(new GetCartRequest { UserId = "test-user" });
        }
        catch (RpcException)
        {
            // Expected
        }

        // Assert - circuit is open again
        var circuitState = resiliencePipeline.GetPipelineState().CircuitBreaker.State;
        Assert.Equal(CircuitBreakerState.Open, circuitState);
    }

    [Fact]
    public async Task test_ac6_circuit_tripped_counter_increments_on_open()
    {
        // Arrange
        var meterProvider = Sdk.CreateMeterProviderBuilder()
            .AddMeter("CartService")
            .AddInMemoryExporter(out var metrics)
            .Build();
        
        _redisDbMock.Setup(m => m.StringGetAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.UnableToConnect, "Redis down"));
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());

        // Act - trip circuit
        for (int i = 0; i < 5; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = "test-user" }); } catch { }
        }

        // Assert
        meterProvider.ForceFlush();
        var trippedMetric = metrics.First(m => m.Name == "cart_service_redis_circuit_breaker_tripped_total");
        Assert.Equal(1, trippedMetric.MetricPoints.First().GetLongValue());
    }

    [Fact]
    public async Task test_ac7_state_gauge_reflects_correct_state()
    {
        // Arrange
        var meterProvider = Sdk.CreateMeterProviderBuilder()
            .AddMeter("CartService")
            .AddInMemoryExporter(out var metrics)
            .Build();
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();

        // Test 1: Closed state
        meterProvider.ForceFlush();
        var stateMetric = metrics.First(m => m.Name == "cart_service_redis_circuit_breaker_state");
        Assert.Equal(0, stateMetric.MetricPoints.First().GetLongValue());

        // Test 2: Open state
        await resiliencePipeline.IsolateAsync();
        meterProvider.ForceFlush();
        stateMetric = metrics.First(m => m.Name == "cart_service_redis_circuit_breaker_state");
        Assert.Equal(1, stateMetric.MetricPoints.First().GetLongValue());

        // Test 3: Half open state
        await Task.Delay(30000);
        meterProvider.ForceFlush();
        stateMetric = metrics.First(m => m.Name == "cart_service_redis_circuit_breaker_state");
        Assert.Equal(2, stateMetric.MetricPoints.First().GetLongValue());
    }

    [Fact]
    public async Task test_ac8_retry_logic_counts_as_one_failure_for_circuit()
    {
        // Arrange - configure 3 retries per operation
        _redisDbMock.Setup(m => m.StringGetAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()))
            .ThrowsAsync(new RedisConnectionException(ConnectionFailureType.UnableToConnect, "Redis down"));
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();

        // Act - make 2 failed operations (each with 3 retries)
        for (int i = 0; i < 2; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = "test-user" }); } catch { }
        }

        // Assert - circuit should still be closed (only 2 failures against threshold of 5)
        var circuitState = resiliencePipeline.GetPipelineState().CircuitBreaker.State;
        Assert.Equal(CircuitBreakerState.Closed, circuitState);
        
        // 3 more failed operations should trip it
        for (int i = 0; i < 3; i++)
        {
            try { await client.GetCartAsync(new GetCartRequest { UserId = "test-user" }); } catch { }
        }
        
        circuitState = resiliencePipeline.GetPipelineState().CircuitBreaker.State;
        Assert.Equal(CircuitBreakerState.Open, circuitState);
    }

    [Fact]
    public async Task test_ac9_all_redis_operations_are_wrapped_in_circuit_breaker()
    {
        // Arrange - open circuit
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();
        await resiliencePipeline.IsolateAsync();
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());
        _redisDbMock.Invocations.Clear();

        // Act & Assert GetCart
        var ex = await Assert.ThrowsAsync<RpcException>(() => client.GetCartAsync(new GetCartRequest { UserId = "test" }));
        Assert.Equal(StatusCode.Unavailable, ex.StatusCode);

        // Act & Assert AddItem
        ex = await Assert.ThrowsAsync<RpcException>(() => client.AddItemAsync(new AddItemRequest { UserId = "test", Item = new CartItem { ProductId = "1", Quantity = 1 } }));
        Assert.Equal(StatusCode.Unavailable, ex.StatusCode);

        // Act & Assert EmptyCart
        ex = await Assert.ThrowsAsync<RpcException>(() => client.EmptyCartAsync(new EmptyCartRequest { UserId = "test" }));
        Assert.Equal(StatusCode.Unavailable, ex.StatusCode);

        // Act & Assert DeleteItem
        ex = await Assert.ThrowsAsync<RpcException>(() => client.DeleteItemAsync(new DeleteItemRequest { UserId = "test", ProductId = "1" }));
        Assert.Equal(StatusCode.Unavailable, ex.StatusCode);

        // Verify no Redis calls were made
        _redisDbMock.Verify(m => m.StringGetAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()), Times.Never);
        _redisDbMock.Verify(m => m.StringSetAsync(It.IsAny<RedisKey>(), It.IsAny<RedisValue>(), It.IsAny<TimeSpan?>(), It.IsAny<bool>(), It.IsAny<When>(), It.IsAny<CommandFlags>()), Times.Never);
        _redisDbMock.Verify(m => m.KeyDeleteAsync(It.IsAny<RedisKey>(), It.IsAny<CommandFlags>()), Times.Never);
    }

    [Fact]
    public async Task test_ac10_open_circuit_no_network_calls_to_redis()
    {
        // Arrange
        var resiliencePipeline = _factory.Services.GetRequiredService<ResiliencePipeline<RedisResult>>();
        await resiliencePipeline.IsolateAsync();
        var client = new CartService.CartServiceClient(_factory.CreateGrpcChannel());
        _redisMock.Invocations.Clear();

        // Act - make 10 cart operations
        for (int i = 0; i < 10; i++)
        {
            try
            {
                await client.GetCartAsync(new GetCartRequest { UserId = $"user-{i}" });
                await client.AddItemAsync(new AddItemRequest { UserId = $"user-{i}", Item = new CartItem { ProductId = "1", Quantity = 1 } });
                await client.EmptyCartAsync(new EmptyCartRequest { UserId = $"user-{i}" });
            }
            catch (RpcException)
            {
                // Expected
            }
        }

        // Assert - no calls to Redis connection multiplexer or database
        _redisMock.Verify(m => m.GetDatabase(It.IsAny<int>(), It.IsAny<object>()), Times.Never);
        _redisMock.Verify(m => m.GetServer(It.IsAny<EndPoint>(), It.IsAny<object>()), Times.Never);
    }
}
