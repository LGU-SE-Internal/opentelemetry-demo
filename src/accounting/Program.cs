// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Accounting;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Logging;
using Confluent.Kafka;
using Microsoft.AspNetCore.Http;
using System.Text.Json;

Console.WriteLine("Accounting service started");

Environment.GetEnvironmentVariables()
    .FilterRelevant()
    .OutputInOrder();

var builder = WebApplication.CreateBuilder(args);

// Configure health port
var healthPort = Environment.GetEnvironmentVariable("ACCOUNTING_HEALTH_PORT") ?? "8080";
builder.WebHost.UseUrls($"http://*:{healthPort}");

// Add health checks
builder.Services.AddHealthChecks()
    // Liveness check: always returns healthy when process is running
    .AddCheck("liveness", () => HealthCheckResult.Healthy(), new[] { "health" })
    // Readiness check: check Kafka connection
    .Add(new HealthCheckRegistration("kafka", sp =>
    {
        var kafkaAddr = Environment.GetEnvironmentVariable("KAFKA_ADDR")
            ?? throw new InvalidOperationException("KAFKA_ADDR environment variable is not set");
        return new KafkaHealthCheck(kafkaAddr);
    }, HealthStatus.Unhealthy, new[] { "ready" }))
    // Readiness check: check PostgreSQL connection
    .AddNpgsql(
        connectionString: Environment.GetEnvironmentVariable("POSTGRESQL_CONNECTION_STRING") 
            ?? throw new InvalidOperationException("POSTGRESQL_CONNECTION_STRING environment variable is not set"),
        name: "postgresql",
        failureStatus: HealthStatus.Unhealthy,
        tags: new[] { "ready" },
        timeout: TimeSpan.FromSeconds(5));

// Add our Kafka consumer service
builder.Services.AddSingleton<Consumer>(sp => new Consumer(sp.GetRequiredService<ILogger<Consumer>>(), sp.GetRequiredService<IConfiguration>()));
// Add graceful shutdown service
builder.Services.AddSingleton<IGracefulShutdownService, GracefulShutdownService>();

var app = builder.Build();

// Get graceful shutdown service and register handlers
var shutdownService = app.Services.GetRequiredService<IGracefulShutdownService>();
shutdownService.RegisterSignalHandlers();

// Map /health endpoint
app.MapGet("/health", async context =>
{
    context.Response.ContentType = "application/json";
    // Return 503 if shutdown has been initiated
    if (shutdownService.ShutdownInitiatedToken.IsCancellationRequested)
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        await context.Response.WriteAsync(JsonSerializer.Serialize(new { status = "ShuttingDown" }));
        return;
    }
    context.Response.StatusCode = StatusCodes.Status200OK;
    await context.Response.WriteAsync(JsonSerializer.Serialize(new { status = "Healthy" }));
});

// Map /ready endpoint
app.MapGet("/ready", async context =>
{
    var healthCheckService = context.RequestServices.GetRequiredService<HealthCheckService>();
    var result = await healthCheckService.CheckHealthAsync(check => check.Tags.Contains("ready"));
    
    context.Response.ContentType = "application/json";
    
    var kafkaStatus = result.Entries.TryGetValue("kafka", out var kafkaEntry) 
        ? kafkaEntry.Status == HealthStatus.Healthy ? "Healthy" : "Unhealthy" 
        : "Unhealthy";
    
    var postgresqlStatus = result.Entries.TryGetValue("postgresql", out var pgEntry) 
        ? pgEntry.Status == HealthStatus.Healthy ? "Healthy" : "Unhealthy" 
        : "Unhealthy";
    
    var overallStatus = result.Status == HealthStatus.Healthy ? "Healthy" : "Unhealthy";
    
    if (result.Status == HealthStatus.Healthy)
    {
        context.Response.StatusCode = StatusCodes.Status200OK;
        await context.Response.WriteAsync(JsonSerializer.Serialize(new 
        { 
            status = overallStatus, 
            kafkaConnection = "Connected",
            checks = new
            {
                kafka = kafkaStatus,
                postgresql = postgresqlStatus
            }
        }));
    }
    else
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        await context.Response.WriteAsync(JsonSerializer.Serialize(new 
        { 
            status = overallStatus, 
            kafkaConnection = kafkaStatus == "Healthy" ? "Connected" : "Disconnected",
            checks = new
            {
                kafka = kafkaStatus,
                postgresql = postgresqlStatus
            }
        }));
    }
});

// Start Kafka consumer in background
var consumer = app.Services.GetRequiredService<Consumer>();
var listeningTask = Task.Run(() => consumer.StartListening(shutdownService.ShutdownInitiatedToken), shutdownService.ShutdownInitiatedToken);

// Register consumer shutdown operations with graceful shutdown service
shutdownService.RegisterShutdownOperation(async ct =>
{
    await consumer.StopAsync(ct);
    await consumer.DisposeAsync();
});

await app.RunAsync();

// Kafka health check implementation
internal class KafkaHealthCheck : IHealthCheck
{
    private readonly string _bootstrapServers;

    public KafkaHealthCheck(string bootstrapServers)
    {
        _bootstrapServers = bootstrapServers;
    }

    public async Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
    {
        try
        {
            var config = new AdminClientConfig { BootstrapServers = _bootstrapServers };
            
            var (kafkaTlsEnabled, caCertPath, clientCertPath, clientKeyPath) = TlsConfiguration.GetKafkaTlsConfig();
            
            if (kafkaTlsEnabled)
            {
                config.SecurityProtocol = SecurityProtocol.Ssl;
                config.SslCaLocation = caCertPath;
                config.SslEndpointIdentificationAlgorithm = SslEndpointIdentificationAlgorithm.Https;

                if (!string.IsNullOrWhiteSpace(clientCertPath) && !string.IsNullOrWhiteSpace(clientKeyPath))
                {
                    config.SslCertificateLocation = clientCertPath;
                    config.SslKeyLocation = clientKeyPath;
                }
            }
            
            using var adminClient = new AdminClientBuilder(config).Build();
            
            // Simple metadata query to verify connection
            var metadata = await Task.Run(() => adminClient.GetMetadata(TimeSpan.FromSeconds(5)), cancellationToken);
            
            if (metadata?.Brokers != null && metadata.Brokers.Any())
            {
                return HealthCheckResult.Healthy("Kafka connection is active");
            }
            
            return HealthCheckResult.Unhealthy("No Kafka brokers found");
        }
        catch (Exception ex)
        {
            return HealthCheckResult.Unhealthy("Kafka connection failed", ex);
        }
    }
}
