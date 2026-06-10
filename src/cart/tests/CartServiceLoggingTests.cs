// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Grpc.Net.Client;
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

public class CartServiceLoggingTests : IDisposable
{
    private readonly Mock<ILogger> _mockLogger;
    private readonly Mock<ILogger<ValkeyCartStore>> _mockValkeyLogger;
    private readonly IHostBuilder _hostBuilder;
    private IHost _host;

    public CartServiceLoggingTests()
    {
        _mockLogger = new Mock<ILogger>();
        _mockValkeyLogger = new Mock<ILogger<ValkeyCartStore>>();

        _hostBuilder = new HostBuilder().ConfigureWebHost(webBuilder =>
        {
            webBuilder
                .UseTestServer()
                .ConfigureServices(services =>
                {
                    services.AddSingleton(_mockLogger.Object);
                    services.AddSingleton(_mockValkeyLogger.Object);
                });
        });
    }

    public void Dispose()
    {
        _host?.Dispose();
    }

    [Fact]
    public async Task test_ac1_missing_valkey_addr_logs_error()
    {
        // AC-1: VALKEY_ADDR not set at startup logs Error level event with correct message and metadata
        Environment.SetEnvironmentVariable("VALKEY_ADDR", null);

        // Expect service to fail startup with correct log entry
        var exception = await Assert.ThrowsAsync<InvalidOperationException>(async () =>
        {
            _host = await _hostBuilder.StartAsync();
        });

        // Verify ILogger.LogError was called with correct parameters
        _mockLogger.Verify(
            x => x.Log(
                LogLevel.Error,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => v.ToString().Contains("VALKEY_ADDR environment variable is not configured")),
                It.IsAny<Exception>(),
                It.Is<Func<It.IsAnyType, Exception, string>>((v, t) => true)),
            Times.Once,
            "Expected ILogger.LogError to be called for missing VALKEY_ADDR");

        // Verify no Console.WriteLine/Console.Error.WriteLine calls were made (invariant checked via AC-3 test)
    }

    [Fact]
    public async Task test_ac2_valkey_operation_failure_logs_structured_error()
    {
        // AC-2: Valkey operation fails logs Error with all required metadata
        const string testValkeyAddr = "localhost:6379";
        Environment.SetEnvironmentVariable("VALKEY_ADDR", testValkeyAddr);

        // Mock ConnectionMultiplexer to throw exception on operation
        var testException = new RedisConnectionException(ConnectionFailureType.UnableToConnect, "Connection refused");
        var mockMultiplexer = new Mock<IConnectionMultiplexer>();
        mockMultiplexer.Setup(m => m.GetDatabase(It.IsAny<int>(), It.IsAny<object>()))
            .Throws(testException);

        _hostBuilder.ConfigureServices(services =>
        {
            services.AddSingleton(mockMultiplexer.Object);
        });

        _host = await _hostBuilder.StartAsync();
        var httpClient = _host.GetTestClient();

        // Create GRPC client
        using var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions { HttpClient = httpClient });
        var cartClient = new CartServiceClient(channel);

        // Attempt operation that will trigger Valkey failure
        var request = new GetCartRequest { UserId = Guid.NewGuid().ToString() };
        await Assert.ThrowsAsync<Grpc.Core.RpcException>(async () => await cartClient.GetCartAsync(request));

        // Verify Valkey logger logged error with all required properties
        _mockValkeyLogger.Verify(
            x => x.Log(
                LogLevel.Error,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) =>
                    v.ToString().Contains("Valkey operation failed") &&
                    v.ToString().Contains(testException.GetType().FullName) &&
                    v.ToString().Contains(testException.Message) &&
                    v.ToString().Contains(testValkeyAddr) &&
                    v.ToString().Contains("Get")
                ),
                testException,
                It.Is<Func<It.IsAnyType, Exception, string>>((v, t) => true)),
            Times.Once,
            "Expected ILogger.LogError to be called for Valkey operation failure with all metadata");
    }

    [Fact]
    public void test_ac3_no_unstructured_console_logging_calls()
    {
        // AC-3: Recursive search of all .cs files in cart service returns zero matches for Console.*Write*
        var cartSourceDir = "./src/cart/src/";
        var allowedExtensions = new[] { ".cs" };

        var consoleCalls = new List<string>();
        foreach (var file in Directory.EnumerateFiles(cartSourceDir, "*.*", SearchOption.AllDirectories)
                     .Where(f => allowedExtensions.Contains(Path.GetExtension(f).ToLower())))
        {
            var content = File.ReadAllText(file);
            if (content.IndexOf("Console.WriteLine", StringComparison.OrdinalIgnoreCase) >= 0 ||
                content.IndexOf("Console.Write(", StringComparison.OrdinalIgnoreCase) >= 0 ||
                content.IndexOf("Console.Error.WriteLine", StringComparison.OrdinalIgnoreCase) >= 0 ||
                content.IndexOf("Console.Error.Write(", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                consoleCalls.Add(file);
            }
        }

        Assert.Empty(consoleCalls, $"Found unstructured Console logging calls in: {string.Join(", ", consoleCalls)}");
    }
}
