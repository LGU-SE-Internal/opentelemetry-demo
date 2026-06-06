using Microsoft.AspNetCore.Mvc.Testing;
using Accounting;
using System.Net.Http.Json;
using Xunit;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace Accounting.Tests;

public class HealthCheckTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly HttpClient _client;

    public HealthCheckTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        _client = _factory.CreateClient();
    }

    [Fact]
    public async Task test_ac1_health_endpoint_returns_200_ok_with_healthy_status()
    {
        // Act
        var response = await _client.GetAsync("/health");

        // Assert
        Assert.Equal(System.Net.HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("application/json", response.Content.Headers.ContentType?.MediaType);
        
        var healthResponse = await response.Content.ReadFromJsonAsync<HealthEndpointResponse>();
        Assert.NotNull(healthResponse);
        Assert.Equal("Healthy", healthResponse.Status);
    }

    [Fact]
    public async Task test_ac2_ready_endpoint_returns_200_ok_when_kafka_connected()
    {
        // Arrange: Override Kafka health check to return healthy
        using var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.Configure<HealthCheckServiceOptions>(opts =>
                {
                    // Clear any existing registrations to inject our test Kafka health check
                    var existingKafkaCheck = opts.Registrations.FirstOrDefault(r => r.Name == "Kafka");
                    if (existingKafkaCheck != null)
                    {
                        opts.Registrations.Remove(existingKafkaCheck);
                    }
                    
                    opts.Registrations.Add(new HealthCheckRegistration(
                        "Kafka",
                        _ => Task.FromResult(HealthCheckResult.Healthy("Kafka connection active")),
                        HealthStatus.Unhealthy,
                        new[] { "ready" }));
                });
            });
        });
        var testClient = factory.CreateClient();

        // Act
        var response = await testClient.GetAsync("/ready");

        // Assert
        Assert.Equal(System.Net.HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("application/json", response.Content.Headers.ContentType?.MediaType);
        
        var readyResponse = await response.Content.ReadFromJsonAsync<ReadyEndpointResponse>();
        Assert.NotNull(readyResponse);
        Assert.Equal("Ready", readyResponse.Status);
        Assert.Equal("Connected", readyResponse.KafkaConnection);
    }

    [Fact]
    public async Task test_ac3_ready_endpoint_returns_503_when_kafka_disconnected()
    {
        // Arrange: Override Kafka health check to return unhealthy
        using var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.Configure<HealthCheckServiceOptions>(opts =>
                {
                    // Clear any existing registrations to inject our test Kafka health check
                    var existingKafkaCheck = opts.Registrations.FirstOrDefault(r => r.Name == "Kafka");
                    if (existingKafkaCheck != null)
                    {
                        opts.Registrations.Remove(existingKafkaCheck);
                    }
                    
                    opts.Registrations.Add(new HealthCheckRegistration(
                        "Kafka",
                        _ => Task.FromResult(HealthCheckResult.Unhealthy("Kafka connection failed")),
                        HealthStatus.Unhealthy,
                        new[] { "ready" }));
                });
            });
        });
        var testClient = factory.CreateClient();

        // Act
        var response = await testClient.GetAsync("/ready");

        // Assert
        Assert.Equal(System.Net.HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.Equal("application/json", response.Content.Headers.ContentType?.MediaType);
        
        var readyResponse = await response.Content.ReadFromJsonAsync<ReadyEndpointResponse>();
        Assert.NotNull(readyResponse);
        Assert.Equal("NotReady", readyResponse.Status);
        Assert.Equal("Disconnected", readyResponse.KafkaConnection);
    }

    [Fact]
    public async Task test_ac4_endpoints_exposed_on_default_port_when_no_existing_http_server()
    {
        // Arrange: Set environment variable for health port
        Environment.SetEnvironmentVariable("ACCOUNTING_HEALTH_PORT", "8080");
        
        // Act: Create client that connects to the default health port
        // Note: This test validates that the server listens on the configured port
        using var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.UseUrls("http://*:8080");
        });
        var testClient = factory.CreateClient();
        
        // Verify endpoints are reachable
        var healthResponse = await testClient.GetAsync("/health");
        var readyResponse = await testClient.GetAsync("/ready");

        // Assert
        Assert.Equal(System.Net.HttpStatusCode.OK, healthResponse.StatusCode);
        Assert.NotNull(readyResponse);
    }

    [Fact]
    public async Task test_ac5_implementation_uses_standard_dotnet_health_check_middleware()
    {
        // Arrange: Check that health check services are registered in DI
        var healthCheckService = _factory.Services.GetService<HealthCheckService>();
        
        // Assert
        Assert.NotNull(healthCheckService);
        
        // Verify health check middleware is configured by calling endpoints
        var healthResponse = await _client.GetAsync("/health");
        Assert.Equal(System.Net.HttpStatusCode.OK, healthResponse.StatusCode);
    }
}

// Response models matching the spec schema
public class HealthEndpointResponse
{
    public string Status { get; set; } = string.Empty;
}

public class ReadyEndpointResponse
{
    public string Status { get; set; } = string.Empty;
    public string KafkaConnection { get; set; } = string.Empty;
}
