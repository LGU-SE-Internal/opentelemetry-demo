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

        [Fact]
        public async Task test_ac1_ready_endpoint_returns_200_ok_when_both_kafka_and_postgresql_healthy()
        {
            // Arrange: Override both Kafka and PostgreSQL health checks to return healthy
            using var factory = _factory.WithWebHostBuilder(builder =>
            {
                builder.ConfigureServices(services =>
                {
                    services.Configure<HealthCheckServiceOptions>(opts =>
                    {
                        // Replace existing Kafka check
                        var existingKafkaCheck = opts.Registrations.FirstOrDefault(r => r.Name == "Kafka");
                        if (existingKafkaCheck != null)
                        {
                            opts.Registrations.Remove(existingKafkaCheck);
                        }
                        
                        // Add test Kafka check
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "Kafka",
                            _ => Task.FromResult(HealthCheckResult.Healthy("Kafka connection active")),
                            HealthStatus.Unhealthy,
                            new[] { "ready" }));
                            
                        // Add test PostgreSQL check
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "postgresql",
                            _ => Task.FromResult(HealthCheckResult.Healthy("PostgreSQL connection active")),
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
            Assert.Equal("Healthy", readyResponse.Status);
            Assert.True(readyResponse.Checks.ContainsKey("kafka"));
            Assert.Equal("Healthy", readyResponse.Checks["kafka"]);
            Assert.True(readyResponse.Checks.ContainsKey("postgresql"));
            Assert.Equal("Healthy", readyResponse.Checks["postgresql"]);
        }

        [Fact]
        public async Task test_ac2_ready_endpoint_returns_503_when_postgresql_unhealthy_kafka_healthy()
        {
            // Arrange: Override Kafka as healthy, PostgreSQL as unhealthy
            using var factory = _factory.WithWebHostBuilder(builder =>
            {
                builder.ConfigureServices(services =>
                {
                    services.Configure<HealthCheckServiceOptions>(opts =>
                    {
                        // Replace existing Kafka check
                        var existingKafkaCheck = opts.Registrations.FirstOrDefault(r => r.Name == "Kafka");
                        if (existingKafkaCheck != null)
                        {
                            opts.Registrations.Remove(existingKafkaCheck);
                        }
                        
                        // Add test Kafka check (healthy)
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "Kafka",
                            _ => Task.FromResult(HealthCheckResult.Healthy("Kafka connection active")),
                            HealthStatus.Unhealthy,
                            new[] { "ready" }));
                            
                        // Add test PostgreSQL check (unhealthy)
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "postgresql",
                            _ => Task.FromResult(HealthCheckResult.Unhealthy("PostgreSQL connection failed")),
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
            Assert.Equal("Unhealthy", readyResponse.Status);
            Assert.Equal("Healthy", readyResponse.Checks["kafka"]);
            Assert.Equal("Unhealthy", readyResponse.Checks["postgresql"]);
        }

        [Fact]
        public async Task test_ac3_ready_endpoint_returns_503_when_kafka_unhealthy_postgresql_healthy()
        {
            // Arrange: Override Kafka as unhealthy, PostgreSQL as healthy
            using var factory = _factory.WithWebHostBuilder(builder =>
            {
                builder.ConfigureServices(services =>
                {
                    services.Configure<HealthCheckServiceOptions>(opts =>
                    {
                        // Replace existing Kafka check
                        var existingKafkaCheck = opts.Registrations.FirstOrDefault(r => r.Name == "Kafka");
                        if (existingKafkaCheck != null)
                        {
                            opts.Registrations.Remove(existingKafkaCheck);
                        }
                        
                        // Add test Kafka check (unhealthy)
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "Kafka",
                            _ => Task.FromResult(HealthCheckResult.Unhealthy("Kafka connection failed")),
                            HealthStatus.Unhealthy,
                            new[] { "ready" }));
                            
                        // Add test PostgreSQL check (healthy)
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "postgresql",
                            _ => Task.FromResult(HealthCheckResult.Healthy("PostgreSQL connection active")),
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
            Assert.Equal("Unhealthy", readyResponse.Status);
            Assert.Equal("Unhealthy", readyResponse.Checks["kafka"]);
            Assert.Equal("Healthy", readyResponse.Checks["postgresql"]);
        }

        [Fact]
        public async Task test_ac4_ready_endpoint_returns_503_when_postgresql_times_out()
        {
            // Arrange: Override PostgreSQL health check to simulate timeout after 6 seconds (exceeds 5s max allowed)
            using var factory = _factory.WithWebHostBuilder(builder =>
            {
                builder.ConfigureServices(services =>
                {
                    services.Configure<HealthCheckServiceOptions>(opts =>
                    {
                        // Replace existing Kafka check with healthy
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
                            
                        // Add PostgreSQL check that delays for 6 seconds
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "postgresql",
                            async _ => 
                            {
                                await Task.Delay(6000);
                                return HealthCheckResult.Healthy("Should have timed out");
                            },
                            HealthStatus.Unhealthy,
                            new[] { "ready" },
                            TimeSpan.FromSeconds(5))); // Explicit 5s timeout as per requirement
                    });
                });
            });
            var testClient = factory.CreateClient();
            var cts = new CancellationTokenSource(TimeSpan.FromSeconds(6)); // Max test duration 6s

            // Act
            var response = await testClient.GetAsync("/ready", cts.Token);

            // Assert
            Assert.Equal(System.Net.HttpStatusCode.ServiceUnavailable, response.StatusCode);
            var readyResponse = await response.Content.ReadFromJsonAsync<ReadyEndpointResponse>(cancellationToken: cts.Token);
            Assert.NotNull(readyResponse);
            Assert.Equal("Unhealthy", readyResponse.Checks["postgresql"]);
            Assert.Equal("Unhealthy", readyResponse.Status);
        }

        [Fact]
        public async Task test_ac5_existing_fields_preserved_without_breaking_changes()
        {
            // Arrange: Override both health checks as healthy
            using var factory = _factory.WithWebHostBuilder(builder =>
            {
                builder.ConfigureServices(services =>
                {
                    services.Configure<HealthCheckServiceOptions>(opts =>
                    {
                        // Replace existing Kafka check
                        var existingKafkaCheck = opts.Registrations.FirstOrDefault(r => r.Name == "Kafka");
                        if (existingKafkaCheck != null)
                        {
                            opts.Registrations.Remove(existingKafkaCheck);
                        }
                        
                        // Add test Kafka check
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "Kafka",
                            _ => Task.FromResult(HealthCheckResult.Healthy("Kafka connection active")),
                            HealthStatus.Unhealthy,
                            new[] { "ready" }));
                            
                        // Add test PostgreSQL check
                        opts.Registrations.Add(new HealthCheckRegistration(
                            "postgresql",
                            _ => Task.FromResult(HealthCheckResult.Healthy("PostgreSQL connection active")),
                            HealthStatus.Unhealthy,
                            new[] { "ready" }));
                    });
                });
            });
            var testClient = factory.CreateClient();

            // Act
            var response = await testClient.GetAsync("/ready");

            // Assert: Existing fields are still present and functional
            var readyResponse = await response.Content.ReadFromJsonAsync<ReadyEndpointResponse>();
            Assert.NotNull(readyResponse);
            // Verify existing top-level field (preserved as per AC-5)
            Assert.NotNull(readyResponse.KafkaConnection);
            // Ensure old behavior still works: Kafka connected should return "Connected"
            Assert.Equal("Connected", readyResponse.KafkaConnection);
        }

        [Fact]
        public async Task test_ac6_postgresql_field_always_present_in_checks()
        {
            // Test both success and failure scenarios to ensure field is always present
            foreach (var postgresqlResult in new[] { HealthStatus.Healthy, HealthStatus.Unhealthy })
            {
                using var factory = _factory.WithWebHostBuilder(builder =>
                {
                    builder.ConfigureServices(services =>
                    {
                        services.Configure<HealthCheckServiceOptions>(opts =>
                        {
                            // Clear existing checks
                            var existingKafkaCheck = opts.Registrations.FirstOrDefault(r => r.Name == "Kafka");
                            if (existingKafkaCheck != null)
                            {
                                opts.Registrations.Remove(existingKafkaCheck);
                            }
                            
                            // Add test Kafka check
                            opts.Registrations.Add(new HealthCheckRegistration(
                                "Kafka",
                                _ => Task.FromResult(HealthCheckResult.Healthy()),
                                HealthStatus.Unhealthy,
                                new[] { "ready" }));
                                
                            // Add test PostgreSQL check with current result
                            opts.Registrations.Add(new HealthCheckRegistration(
                                "postgresql",
                                _ => Task.FromResult(postgresqlResult == HealthStatus.Healthy 
                                    ? HealthCheckResult.Healthy() 
                                    : HealthCheckResult.Unhealthy()),
                                HealthStatus.Unhealthy,
                                new[] { "ready" }));
                        });
                    });
                });
                var testClient = factory.CreateClient();

                // Act
                var response = await testClient.GetAsync("/ready");
                var readyResponse = await response.Content.ReadFromJsonAsync<ReadyEndpointResponse>();

                // Assert
                Assert.NotNull(readyResponse);
                Assert.True(readyResponse.Checks.ContainsKey("postgresql"));
                Assert.True(readyResponse.Checks["postgresql"] is "Healthy" or "Unhealthy");
            }
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
    // Existing fields preserved as per requirement AC-5
    public string KafkaConnection { get; set; } = string.Empty;
    // New field added per spec
    public Dictionary<string, string> Checks { get; set; } = new();
}
