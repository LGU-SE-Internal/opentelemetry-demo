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
    }, HealthStatus.Unhealthy, new[] { "ready" }));

// Add our Kafka consumer service
builder.Services.AddSingleton<Consumer>();

var app = builder.Build();

// Map /health endpoint
app.MapGet("/health", async context =>
{
    context.Response.ContentType = "application/json";
    context.Response.StatusCode = StatusCodes.Status200OK;
    await context.Response.WriteAsync(JsonSerializer.Serialize(new { status = "Healthy" }));
});

// Map /ready endpoint
app.MapGet("/ready", async context =>
{
    var healthCheckService = context.RequestServices.GetRequiredService<HealthCheckService>();
    var result = await healthCheckService.CheckHealthAsync(check => check.Tags.Contains("ready"));
    
    context.Response.ContentType = "application/json";
    
    if (result.Status == HealthStatus.Healthy)
    {
        context.Response.StatusCode = StatusCodes.Status200OK;
        await context.Response.WriteAsync(JsonSerializer.Serialize(new 
        { 
            status = "Ready", 
            kafkaConnection = "Connected" 
        }));
    }
    else
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        await context.Response.WriteAsync(JsonSerializer.Serialize(new 
        { 
            status = "NotReady", 
            kafkaConnection = "Disconnected" 
        }));
    }
});

// Start Kafka consumer in background
var consumer = app.Services.GetRequiredService<Consumer>();
_ = Task.Run(() => consumer.StartListening(), app.Lifetime.ApplicationStopping);

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
