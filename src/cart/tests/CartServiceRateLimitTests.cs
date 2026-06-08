// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Net;
using System.Threading.Tasks;
using Grpc.Core;
using Grpc.Net.Client;
using Oteldemo;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Xunit;
using static Oteldemo.CartService;

namespace cart.tests;

public class CartServiceRateLimitTests
{
    private readonly IHostBuilder _host;

    public CartServiceRateLimitTests()
    {
        _host = new HostBuilder().ConfigureWebHost(webBuilder =>
        {
            webBuilder
                .UseTestServer();
        });
    }

    [Fact]
    public async Task Test_AC1_GetCart_ExceedsLimit_ReturnsResourceExhausted()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_GETCART_MAX", "2");
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_GETCART_WINDOW_SECONDS", "60");
        var clientIp = "192.168.1.100";

        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        httpClient.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);
        var userId = Guid.NewGuid().ToString();

        // Act - make 2 requests that should succeed
        for (int i = 0; i < 2; i++)
        {
            await cartClient.GetCartAsync(new GetCartRequest { UserId = userId });
        }

        // Act + Assert - third request should fail with RESOURCE_EXHAUSTED
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            cartClient.GetCartAsync(new GetCartRequest { UserId = userId }));
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
        Assert.Contains("Rate limit exceeded for endpoint GetCart", exception.Status.Detail);
    }

    [Fact]
    public async Task Test_AC2_AddItem_ExceedsLimit_ReturnsResourceExhausted()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_ADDITEM_MAX", "2");
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_ADDITEM_WINDOW_SECONDS", "60");
        var clientIp = "192.168.1.101";

        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        httpClient.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);
        var userId = Guid.NewGuid().ToString();
        var addItemRequest = new AddItemRequest
        {
            UserId = userId,
            Item = new AddItemRequest.Types.CartItem
            {
                ProductId = "test-product",
                Quantity = 1
            }
        };

        // Act - make 2 requests that should succeed
        for (int i = 0; i < 2; i++)
        {
            await cartClient.AddItemAsync(addItemRequest);
        }

        // Act + Assert - third request should fail with RESOURCE_EXHAUSTED
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            cartClient.AddItemAsync(addItemRequest));
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
        Assert.Contains("Rate limit exceeded for endpoint AddItem", exception.Status.Detail);
    }

    [Fact]
    public async Task Test_AC3_RemoveItem_ExceedsLimit_ReturnsResourceExhausted()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_REMOVEITEM_MAX", "2");
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_REMOVEITEM_WINDOW_SECONDS", "60");
        var clientIp = "192.168.1.102";

        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        httpClient.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);
        var userId = Guid.NewGuid().ToString();
        var removeItemRequest = new RemoveItemRequest
        {
            UserId = userId,
            ProductId = "test-product"
        };

        // Act - make 2 requests that should succeed
        for (int i = 0; i < 2; i++)
        {
            await cartClient.RemoveItemAsync(removeItemRequest);
        }

        // Act + Assert - third request should fail with RESOURCE_EXHAUSTED
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            cartClient.RemoveItemAsync(removeItemRequest));
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
        Assert.Contains("Rate limit exceeded for endpoint RemoveItem", exception.Status.Detail);
    }

    [Fact]
    public async Task Test_AC4_EmptyCart_ExceedsLimit_ReturnsResourceExhausted()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_EMPTYCART_MAX", "2");
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_EMPTYCART_WINDOW_SECONDS", "60");
        var clientIp = "192.168.1.103";

        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        httpClient.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);
        var userId = Guid.NewGuid().ToString();
        var emptyCartRequest = new EmptyCartRequest { UserId = userId };

        // Act - make 2 requests that should succeed
        for (int i = 0; i < 2; i++)
        {
            await cartClient.EmptyCartAsync(emptyCartRequest);
        }

        // Act + Assert - third request should fail with RESOURCE_EXHAUSTED
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            cartClient.EmptyCartAsync(emptyCartRequest));
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
        Assert.Contains("Rate limit exceeded for endpoint EmptyCart", exception.Status.Detail);
    }

    [Fact]
    public async Task Test_AC5_Configuration_FromEnvironmentVariables()
    {
        // Arrange - set custom environment variables
        var customGetCartMax = 150;
        var customGetCartWindow = 120;
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_GETCART_MAX", customGetCartMax.ToString());
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_GETCART_WINDOW_SECONDS", customGetCartWindow.ToString());
        var clientIp = "192.168.1.104";

        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        httpClient.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);
        var userId = Guid.NewGuid().ToString();

        // Act - make 150 requests that should succeed
        for (int i = 0; i < customGetCartMax; i++)
        {
            await cartClient.GetCartAsync(new GetCartRequest { UserId = userId });
        }

        // Act + Assert - 151st request should fail (confirm custom limit applies)
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            cartClient.GetCartAsync(new GetCartRequest { UserId = userId }));
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
    }

    [Fact]
    public async Task Test_AC6_RateLimitedRequest_LogsRequiredFields()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_GETCART_MAX", "1");
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_GETCART_WINDOW_SECONDS", "60");
        var clientIp = "192.168.1.105";
        var logEntries = new List<string>();
        // Configure test host to capture logs
        var host = new HostBuilder().ConfigureWebHost(webBuilder =>
        {
            webBuilder
                .UseTestServer()
                .ConfigureLogging(logging =>
                {
                    logging.AddProvider(new InMemoryLogProvider(log => logEntries.Add(log)));
                });
        });

        using var server = await host.StartAsync();
        var httpClient = server.GetTestClient();
        httpClient.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);
        var userId = Guid.NewGuid().ToString();

        // Act - make 2 requests, second one is rate limited
        await cartClient.GetCartAsync(new GetCartRequest { UserId = userId });
        try { await cartClient.GetCartAsync(new GetCartRequest { UserId = userId }); } catch { }

        // Assert - log entry exists with all required fields
        var rateLimitLog = logEntries.Find(l => l.Contains("\"type\":\"rate_limit_exceeded\""));
        Assert.NotNull(rateLimitLog);
        Assert.Contains("\"endpoint\":\"GetCart\"", rateLimitLog);
        Assert.Contains($"\"client_ip\":\"{clientIp}\"", rateLimitLog);
        Assert.Contains("\"limit\":1", rateLimitLog);
        Assert.Contains("\"window_seconds\":60", rateLimitLog);
        Assert.Contains("\"retry_after\":", rateLimitLog);
    }

    [Fact]
    public async Task Test_AC7_DistributedRateLimit_AppliedAcrossInstances()
    {
        // Arrange
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_ADDITEM_MAX", "2");
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_ADDITEM_WINDOW_SECONDS", "60");
        Environment.SetEnvironmentVariable("CART_SERVICE_RATELIMIT_REDIS_ADDRESS", "localhost:6379");
        var clientIp = "192.168.1.106";
        var userId = Guid.NewGuid().ToString();
        var addItemRequest = new AddItemRequest
        {
            UserId = userId,
            Item = new AddItemRequest.Types.CartItem { ProductId = "test-product", Quantity = 1 }
        };

        // Start two separate service instances
        using var server1 = await _host.StartAsync();
        using var server2 = await _host.StartAsync();

        var client1 = server1.GetTestClient();
        client1.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);
        var channel1 = GrpcChannel.ForAddress(client1.BaseAddress, new GrpcChannelOptions { HttpClient = client1 });
        var cartClient1 = new CartServiceClient(channel1);

        var client2 = server2.GetTestClient();
        client2.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);
        var channel2 = GrpcChannel.ForAddress(client2.BaseAddress, new GrpcChannelOptions { HttpClient = client2 });
        var cartClient2 = new CartServiceClient(channel2);

        // Act - make 1 request to each instance
        await cartClient1.AddItemAsync(addItemRequest);
        await cartClient2.AddItemAsync(addItemRequest);

        // Assert - third request to either instance should be rate limited
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            cartClient1.AddItemAsync(addItemRequest));
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
    }

    [Fact]
    public async Task Test_AC8_NoEnvironmentVariables_UseDefaultValues()
    {
        // Arrange - remove all rate limit environment variables
        foreach (var envVar in Environment.GetEnvironmentVariables().Keys)
        {
            if (envVar.ToString().StartsWith("CART_SERVICE_RATELIMIT_"))
            {
                Environment.SetEnvironmentVariable(envVar.ToString(), null);
            }
        }
        var defaultGetCartMax = 100;
        var clientIp = "192.168.1.107";

        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        httpClient.DefaultRequestHeaders.Add("X-Forwarded-For", clientIp);

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);
        var userId = Guid.NewGuid().ToString();

        // Act - make 100 requests that should succeed (default limit is 100)
        for (int i = 0; i < defaultGetCartMax; i++)
        {
            await cartClient.GetCartAsync(new GetCartRequest { UserId = userId });
        }

        // Act + Assert - 101st request should fail (confirm default limit applies)
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            cartClient.GetCartAsync(new GetCartRequest { UserId = userId }));
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
    }

    // In-memory log provider for AC6 test
    private class InMemoryLogProvider : ILoggerProvider
    {
        private readonly Action<string> _logCallback;

        public InMemoryLogProvider(Action<string> logCallback)
        {
            _logCallback = logCallback;
        }

        public ILogger CreateLogger(string categoryName)
        {
            return new InMemoryLogger(_logCallback);
        }

        public void Dispose() { }

        private class InMemoryLogger : ILogger
        {
            private readonly Action<string> _logCallback;

            public InMemoryLogger(Action<string> logCallback)
            {
                _logCallback = logCallback;
            }

            public IDisposable BeginScope<TState>(TState state) => null;

            public bool IsEnabled(LogLevel logLevel) => logLevel == LogLevel.Warning;

            public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception exception, Func<TState, Exception, string> formatter)
            {
                if (logLevel == LogLevel.Warning)
                {
                    _logCallback(formatter(state, exception));
                }
            }
        }
    }
}
