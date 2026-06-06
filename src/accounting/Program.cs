// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Accounting;
using Grpc.AspNetCore.HealthChecks;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

Console.WriteLine("Accounting service started");

Environment.GetEnvironmentVariables()
    .FilterRelevant()
    .OutputInOrder();

var builder = WebApplication.CreateBuilder(args);

// Add gRPC services
builder.Services.AddGrpc();

// Add health checks
builder.Services.AddHealthChecks()
    // Liveness check: always returns healthy when process is running
    .AddCheck("liveness", () => HealthCheckResult.Healthy(), new[] { "liveness" })
    // Readiness check: check required dependencies
    .AddNpgSql(Environment.GetEnvironmentVariable("PG_CONNECTION_STRING") ?? string.Empty, name: "postgres", tags: new[] { "readiness" })
    .AddKafka(Environment.GetEnvironmentVariable("KAFKA_BROKER") ?? string.Empty, name: "kafka", tags: new[] { "readiness" });

// Map gRPC health checks to the standard service
builder.Services.AddGrpcHealthChecks()
    .AddServiceMapping("", "liveness")
    .AddServiceMapping("accounting", "readiness");

// Add our service
builder.Services.AddSingleton<Consumer>();

var app = builder.Build();

// Configure gRPC pipeline
app.MapGrpcService<GrpcAccountingService>(); // Add the main gRPC service if exists
app.MapGrpcHealthChecksService();

// Configure health check logging for status transitions
var healthCheckService = app.Services.GetRequiredService<HealthCheckService>();
var logger = app.Services.GetRequiredService<ILogger<Program>>();

_ = Task.Run(async () =>
{
    HealthStatus previousReadinessStatus = HealthStatus.Unhealthy;
    while (!app.Lifetime.ApplicationStopping.IsCancellationRequested)
    {
        var result = await healthCheckService.CheckHealthAsync(context => context.Tags.Contains("readiness"), app.Lifetime.ApplicationStopping);
        if (result.Status != previousReadinessStatus)
        {
            logger.LogInformation("Readiness status changed from {Previous} to {New} at {Time}: {Reason}",
                previousReadinessStatus, result.Status, DateTime.UtcNow,
                string.Join(", ", result.Entries.Select(e => $"{e.Key}: {e.Value.Status} - {e.Value.Description}")));
            previousReadinessStatus = result.Status;
        }
        await Task.Delay(5000, app.Lifetime.ApplicationStopping);
    }
}, app.Lifetime.ApplicationStopping);

var consumer = app.Services.GetRequiredService<Consumer>();
consumer.StartListening();

await app.RunAsync();
