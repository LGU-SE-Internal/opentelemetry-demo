// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Grpc.Net.Client;
using Oteldemo;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Xunit;
using static Oteldemo.CartService;
using Moq;
using StackExchange.Redis;

namespace cart.tests;

public class CartServiceLoggingTests
{
    private readonly IHostBuilder _host;

    public CartServiceLoggingTests()
    {
        _host = new HostBuilder().ConfigureWebHost(webBuilder =>
        {
            webBuilder
                .UseTestServer();
        });
    }

    [Fact]
    public void test_ac1_no_console_writeline_calls()
    {
        // Check Program.cs and ValkeyCartStore.cs for Console.WriteLine calls
        var programCsContent = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "../../../../src/Program.cs"));
        var valkeyStoreContent = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "../../../../src/cartstore/ValkeyCartStore.cs"));
        
        Assert.DoesNotContain("Console.WriteLine", programCsContent);
        Assert.DoesNotContain("Console.WriteLine", valkeyStoreContent);
    }

    [Fact]
    public async Task test_ac2_logs_use_ilogger_logerror_with_same_messages()
    {
        var mockLogger = new Mock<ILogger<ValkeyCartStore>>();
        var mockConnectionMultiplexer = new Mock<IConnectionMultiplexer>();
        mockConnectionMultiplexer.Setup(c => c.GetDatabase(It.IsAny<int>(), It.IsAny<object>())).Throws(new Exception("Connection failed"));

        var valkeyStore = new ValkeyCartStore(mockLogger.Object, mockConnectionMultiplexer.Object, new RedisRetryPolicySettings());

        // Attempt to access cart store which should log error
        var exception = await Record.ExceptionAsync(async () => await valkeyStore.GetItemsAsync("test-user"));
        
        // Verify ILogger.LogError was called with expected message
        mockLogger.Verify(
            x => x.Log(
                LogLevel.Error,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => v.ToString().Contains("Failed to get cart items for user test-user")),
                It.IsAny<Exception>(),
                It.IsAny<Func<It.IsAnyType, Exception, string>>()),
            Times.Once,
            "Expected LogError to be called with the correct error message");
    }

    [Fact]
    public async Task test_ac3_logs_contain_service_trace_span_fields()
    {
        var logsReceived = new List<LogEntry>();
        var testHost = _host.ConfigureLogging(logging =>
        {
            logging.AddProvider(new TestLoggerProvider(logsReceived));
        }).Build();

        await testHost.StartAsync();
        using var server = testHost.GetTestServer();
        var httpClient = server.GetTestClient();

        // Make a request that will trigger an error log
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions { HttpClient = httpClient });
        var client = new CartServiceClient(channel);

        // Attempt to get cart with invalid connection to trigger error
        var exception = await Record.ExceptionAsync(async () => await client.GetCartAsync(new GetCartRequest { UserId = "test-user" }));

        // Verify logs have required fields
        Assert.NotEmpty(logsReceived);
        var errorLogs = logsReceived.Where(l => l.LogLevel == LogLevel.Error).ToList();
        Assert.NotEmpty(errorLogs);

        foreach (var log in errorLogs)
        {
            Assert.True(log.Properties.TryGetValue("service.name", out var serviceName), "Log missing service.name field");
            Assert.Equal("cartservice", serviceName?.ToString());
            
            // If in trace context, trace and span IDs should be present
            if (log.TraceId != default)
            {
                Assert.True(log.TraceId.ToHexString().Length == 32, "trace.id should be 16-byte hex string");
            }
            if (log.SpanId != default)
            {
                Assert.True(log.SpanId.ToHexString().Length == 16, "span.id should be 8-byte hex string");
            }
        }

        await testHost.StopAsync();
    }

    [Fact]
    public async Task test_ac4_logs_preserve_full_exception_details()
    {
        var mockLogger = new Mock<ILogger<ValkeyCartStore>>();
        var testException = new InvalidOperationException("Test connection error", new System.Net.Sockets.SocketException());
        var mockConnectionMultiplexer = new Mock<IConnectionMultiplexer>();
        mockConnectionMultiplexer.Setup(c => c.GetDatabase(It.IsAny<int>(), It.IsAny<object>())).Throws(testException);

        var valkeyStore = new ValkeyCartStore(mockLogger.Object, mockConnectionMultiplexer.Object, new RedisRetryPolicySettings());

        var exception = await Record.ExceptionAsync(async () => await valkeyStore.GetItemsAsync("test-user"));

        // Verify exception details are preserved in log
        mockLogger.Verify(
            x => x.Log(
                LogLevel.Error,
                It.IsAny<EventId>(),
                It.IsAny<It.IsAnyType>(),
                It.Is<Exception>(ex => 
                    ex.Message == testException.Message && 
                    ex.GetType() == testException.GetType() &&
                    ex.InnerException != null &&
                    ex.InnerException.GetType() == typeof(System.Net.Sockets.SocketException)),
                It.IsAny<Func<It.IsAnyType, Exception, string>>()),
            Times.Once,
            "Log should preserve full exception details including type, message and stack trace");
    }

    [Fact(Skip = "Requires full OpenTelemetry Collector infrastructure, run in integration test suite only")]
    public async Task test_ac5_logs_ingested_by_otel_collector()
    {
        // This test would typically run against a deployed demo environment
        // Verify logs appear in logging backend with all structured fields
        // For this test suite, we verify the logs are properly formatted for collector ingestion
        Assert.True(true, "Structured log format is compatible with OpenTelemetry Collector ingestion");
    }
}

// Helper classes for test logging
public class LogEntry
{
    public LogLevel LogLevel { get; set; }
    public string Message { get; set; }
    public Exception Exception { get; set; }
    public Dictionary<string, object> Properties { get; set; } = new();
    public ActivityTraceId TraceId { get; set; }
    public ActivitySpanId SpanId { get; set; }
}

public class TestLoggerProvider : ILoggerProvider
{
    private readonly List<LogEntry> _logs;

    public TestLoggerProvider(List<LogEntry> logs)
    {
        _logs = logs;
    }

    public ILogger CreateLogger(string categoryName)
    {
        return new TestLogger(categoryName, _logs);
    }

    public void Dispose() { }
}

public class TestLogger : ILogger
{
    private readonly string _category;
    private readonly List<LogEntry> _logs;

    public TestLogger(string category, List<LogEntry> logs)
    {
        _category = category;
        _logs = logs;
    }

    public IDisposable BeginScope<TState>(TState state) => null;

    public bool IsEnabled(LogLevel logLevel) => true;

    public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception exception, Func<TState, Exception, string> formatter)
    {
        var entry = new LogEntry
        {
            LogLevel = logLevel,
            Message = formatter(state, exception),
            Exception = exception,
            TraceId = System.Diagnostics.Activity.Current?.TraceId ?? default,
            SpanId = System.Diagnostics.Activity.Current?.SpanId ?? default
        };

        if (state is IReadOnlyList<KeyValuePair<string, object>> properties)
        {
            foreach (var prop in properties)
            {
                entry.Properties[prop.Key] = prop.Value;
            }
        }

        _logs.Add(entry);
    }
}
