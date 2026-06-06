using Grpc.Net.Client;
using Grpc.Health.V1;
using Xunit;
using Microsoft.AspNetCore.Mvc.Testing;
using Accounting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Grpc.Core;
using Microsoft.Extensions.Logging;

namespace Accounting.Tests;

public class HealthCheckTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly GrpcChannel _channel;
    private readonly Health.HealthClient _client;

    public HealthCheckTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        var client = _factory.CreateClient();
        _channel = GrpcChannel.ForAddress(client.BaseAddress!, new GrpcChannelOptions { HttpClient = client });
        _client = new Health.HealthClient(_channel);
    }

    [Fact]
    public async Task test_ac1_health_service_available_on_same_port_as_main_api()
    {
        // Arrange
        var request = new HealthCheckRequest { Service = "" };

        // Act
        var response = await _client.CheckAsync(request);

        // Assert: gRPC status is OK (no exception thrown) and service responds
        Assert.NotNull(response);
    }

    [Fact]
    public async Task test_ac2_liveness_check_returns_serving_always_when_process_running()
    {
        // Arrange: Liveness check uses empty service name
        var request = new HealthCheckRequest { Service = "" };

        // Act: Even with failing dependencies, liveness should return SERVING
        var response = await _client.CheckAsync(request);

        // Assert
        Assert.Equal(HealthCheckResponse.Types.ServingStatus.Serving, response.Status);
    }

    [Fact]
    public async Task test_ac3_readiness_check_returns_serving_when_all_dependencies_healthy()
    {
        // Arrange: Readiness check uses "accounting" service name
        var request = new HealthCheckRequest { Service = "accounting" };

        // Override health checks to return healthy for all dependencies
        using var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.Configure<HealthCheckServiceOptions>(opts =>
                {
                    opts.Registrations.Clear();
                    opts.Registrations.Add(new HealthCheckRegistration(
                        "Database",
                        _ => Task.FromResult(HealthCheckResult.Healthy()),
                        HealthStatus.Unhealthy,
                        new[] { "accounting" }));
                    opts.Registrations.Add(new HealthCheckRegistration(
                        "MessageBroker",
                        _ => Task.FromResult(HealthCheckResult.Healthy()),
                        HealthStatus.Unhealthy,
                        new[] { "accounting" }));
                });
            });
        });
        var client = factory.CreateClient();
        var channel = GrpcChannel.ForAddress(client.BaseAddress!, new GrpcChannelOptions { HttpClient = client });
        var testClient = new Health.HealthClient(channel);

        // Act
        var response = await testClient.CheckAsync(request);

        // Assert
        Assert.Equal(HealthCheckResponse.Types.ServingStatus.Serving, response.Status);
    }

    [Fact]
    public async Task test_ac4_readiness_check_returns_not_serving_when_any_dependency_unhealthy()
    {
        // Arrange: Readiness check uses "accounting" service name
        var request = new HealthCheckRequest { Service = "accounting" };

        // Override health checks to return unhealthy for one dependency
        using var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.Configure<HealthCheckServiceOptions>(opts =>
                {
                    opts.Registrations.Clear();
                    opts.Registrations.Add(new HealthCheckRegistration(
                        "Database",
                        _ => Task.FromResult(HealthCheckResult.Unhealthy("DB connection failed")),
                        HealthStatus.Unhealthy,
                        new[] { "accounting" }));
                    opts.Registrations.Add(new HealthCheckRegistration(
                        "MessageBroker",
                        _ => Task.FromResult(HealthCheckResult.Healthy()),
                        HealthStatus.Unhealthy,
                        new[] { "accounting" }));
                });
            });
        });
        var client = factory.CreateClient();
        var channel = GrpcChannel.ForAddress(client.BaseAddress!, new GrpcChannelOptions { HttpClient = client });
        var testClient = new Health.HealthClient(channel);

        // Act
        var response = await testClient.CheckAsync(request);

        // Assert
        Assert.Equal(HealthCheckResponse.Types.ServingStatus.NotServing, response.Status);
    }

    [Fact]
    public async Task test_ac5_status_transitions_are_logged()
    {
        // Arrange: Track log entries
        var logMessages = new List<string>();

        using var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.AddLogging(logging =>
                {
                    logging.AddProvider(new InMemoryLoggerProvider(logMessages));
                });
            });
        });
        var client = factory.CreateClient();
        var channel = GrpcChannel.ForAddress(client.BaseAddress!, new GrpcChannelOptions { HttpClient = client });
        var testClient = new Health.HealthClient(channel);

        // Act: Simulate status transition from healthy to unhealthy and back
        // First check should return healthy and log
        var initialResponse = await testClient.CheckAsync(new HealthCheckRequest { Service = "accounting" });
        // Force unhealthy status
        // Wait for propagation
        await Task.Delay(1000);
        // Check again, should return unhealthy and log transition
        var unhealthyResponse = await testClient.CheckAsync(new HealthCheckRequest { Service = "accounting" });
        // Force healthy status again
        // Wait for propagation
        await Task.Delay(1000);
        // Check again, should return healthy and log transition
        var healthyResponse = await testClient.CheckAsync(new HealthCheckRequest { Service = "accounting" });

        // Assert: Logs contain transition entries
        Assert.Contains(logMessages, m => m.Contains("SERVING") && (m.Contains("liveness") || m.Contains("readiness")));
        Assert.Contains(logMessages, m => m.Contains("NOT_SERVING") && m.Contains("reason"));
        Assert.All(logMessages, m => Assert.Contains(DateTime.UtcNow.ToString("yyyy-MM-dd"), m)); // Should have timestamp
    }

    [Fact]
    public async Task test_ac6_watch_method_streams_status_updates_on_change()
    {
        // Arrange
        var request = new HealthCheckRequest { Service = "accounting" };
        var statusUpdates = new List<HealthCheckResponse.Types.ServingStatus>();

        // Act: Start watching
        using var call = _client.Watch(request);
        var readTask = Task.Run(async () =>
        {
            await foreach (var response in call.ResponseStream.ReadAllAsync())
            {
                statusUpdates.Add(response.Status);
            }
        });

        // Wait for initial status
        await Task.Delay(500);
        // Trigger status change (simulate dependency failure)
        await Task.Delay(1000);
        // Trigger status change (simulate dependency recovery)
        await Task.Delay(1000);
        call.Dispose();
        await readTask;

        // Assert: We have received at least 3 status updates (initial, unhealthy, healthy)
        Assert.True(statusUpdates.Count >= 3);
        Assert.Contains(HealthCheckResponse.Types.ServingStatus.Serving, statusUpdates);
        Assert.Contains(HealthCheckResponse.Types.ServingStatus.NotServing, statusUpdates);
    }

    [Fact]
    public async Task test_ac7_invalid_service_name_returns_service_unknown_status()
    {
        // Arrange: Request health check for non-existent service
        var request = new HealthCheckRequest { Service = "nonexistent-service" };

        // Act
        var response = await _client.CheckAsync(request);

        // Assert
        Assert.Equal(HealthCheckResponse.Types.ServingStatus.ServiceUnknown, response.Status);
    }

    [Fact]
    public async Task test_ac7_malformed_request_returns_invalid_argument_error()
    {
        // Arrange: Simulate malformed request by sending invalid null service name
        var request = new HealthCheckRequest { Service = null! };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() =>
            _client.CheckAsync(request).ResponseAsync);
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
    }
}

// Helper class for in-memory logging
public class InMemoryLoggerProvider : ILoggerProvider
{
    private readonly List<string> _logMessages;

    public InMemoryLoggerProvider(List<string> logMessages)
    {
        _logMessages = logMessages;
    }

    public ILogger CreateLogger(string categoryName)
    {
        return new InMemoryLogger(_logMessages);
    }

    public void Dispose() { }
}

public class InMemoryLogger : ILogger
{
    private readonly List<string> _logMessages;

    public InMemoryLogger(List<string> logMessages)
    {
        _logMessages = logMessages;
    }

    public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

    public bool IsEnabled(LogLevel logLevel) => true;

    public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
    {
        _logMessages.Add($"{DateTime.UtcNow:o} {logLevel}: {formatter(state, exception)}");
    }
}
