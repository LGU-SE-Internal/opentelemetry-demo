// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography.X509Certificates;
using System.Net.Security;

using Grpc.Health.V1;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using System.Threading.Tasks;
using System.Threading;

using Grpc.Core;

using cart.cartstore;
using cart.services;
using cart.healthcheck;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Hosting;
using OpenTelemetry.Instrumentation.StackExchangeRedis;
using OpenTelemetry.Logs;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using OpenFeature;
using OpenFeature.Hooks;
using OpenFeature.Providers.Flagd;
public partial class Program
{
    internal const int ShutdownGracePeriodSeconds = 10;
    private static bool _isShuttingDown = false;
    private static int _inFlightRequests = 0;
    private static int _completedRequests = 0;

    internal static void RegisterShutdownHandlers(IHostApplicationLifetime lifetime, ICartStore cartStore, ILogger<Program> logger)
    {
        lifetime.ApplicationStopping.Register(() =>
        {
            _isShuttingDown = true;
            logger.LogInformation("Received termination signal {SignalType}, starting graceful shutdown", "SIGINT/SIGTERM");

            var stopwatch = Stopwatch.StartNew();
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(ShutdownGracePeriodSeconds));

            try
            {
                // Wait for in-flight requests to complete or timeout
                while (_inFlightRequests > 0 && !cts.IsCancellationRequested)
                {
                    Task.Delay(100, cts.Token).Wait(cts.Token);
                }

                // Flush pending writes
                cartStore.FlushAsync(cts.Token).Wait(cts.Token);

                stopwatch.Stop();
                logger.LogInformation("Shutdown completed successfully in {Duration}ms, processed {CompletedCount} in-flight requests", stopwatch.ElapsedMilliseconds, _completedRequests);
            }
            catch (OperationCanceledException)
            {
                stopwatch.Stop();
                logger.LogWarning("Graceful shutdown timed out after {Timeout}s, terminating {Count} in-flight requests", ShutdownGracePeriodSeconds, _inFlightRequests);
            }
            catch (AggregateException ex) when (ex.InnerException is OperationCanceledException)
            {
                stopwatch.Stop();
                logger.LogWarning("Graceful shutdown timed out after {Timeout}s, terminating {Count} in-flight requests", ShutdownGracePeriodSeconds, _inFlightRequests);
            }
        });
    }
}

var builder = WebApplication.CreateBuilder(args);

// TLS Configuration Validation
bool tlsEnabled = bool.TryParse(builder.Configuration["CART_SERVICE_TLS_ENABLED"], out bool te) && te;
string tlsCertPath = builder.Configuration["CART_SERVICE_TLS_CERT_PATH"] ?? string.Empty;
string tlsKeyPath = builder.Configuration["CART_SERVICE_TLS_KEY_PATH"] ?? string.Empty;
bool mtlsEnabled = bool.TryParse(builder.Configuration["CART_SERVICE_MTLS_ENABLED"], out bool me) && me;
string mtlsCaCertPath = builder.Configuration["CART_SERVICE_MTLS_CA_CERT_PATH"] ?? string.Empty;

if (tlsEnabled)
{
    if (string.IsNullOrEmpty(tlsCertPath))
    {
        throw new InvalidOperationException("CART_SERVICE_TLS_CERT_PATH is required when TLS is enabled");
    }
    if (!File.Exists(tlsCertPath))
    {
        throw new InvalidOperationException("CART_SERVICE_TLS_CERT_PATH points to non-existent file");
    }
    if (string.IsNullOrEmpty(tlsKeyPath))
    {
        throw new InvalidOperationException("CART_SERVICE_TLS_KEY_PATH is required when TLS is enabled");
    }
    if (!File.Exists(tlsKeyPath))
    {
        throw new InvalidOperationException("CART_SERVICE_TLS_KEY_PATH points to non-existent file");
    }

    // Configure Kestrel TLS
    builder.WebHost.ConfigureKestrel(options =>
    {
        options.ConfigureHttpsDefaults(httpsOptions =>
        {
            httpsOptions.ServerCertificate = System.Security.Cryptography.X509Certificates.X509Certificate2.CreateFromPemFile(tlsCertPath, tlsKeyPath);
            httpsOptions.SslProtocols = System.Security.Authentication.SslProtocols.Tls12 | System.Security.Authentication.SslProtocols.Tls13;

            if (mtlsEnabled)
            {
                if (string.IsNullOrEmpty(mtlsCaCertPath))
                {
                    throw new InvalidOperationException("CART_SERVICE_MTLS_CA_CERT_PATH is required when mTLS is enabled");
                }
                if (!File.Exists(mtlsCaCertPath))
                {
                    throw new InvalidOperationException("CART_SERVICE_MTLS_CA_CERT_PATH points to non-existent file");
                }
                try
                {
                    var caCert = new System.Security.Cryptography.X509Certificates.X509Certificate2(mtlsCaCertPath);
                    httpsOptions.ClientCertificateMode = Microsoft.AspNetCore.Server.Kestrel.Https.ClientCertificateMode.RequireCertificate;
                    httpsOptions.ClientCertificateValidation = (cert, chain, errors) =>
                    {
                        if (errors != System.Net.Security.SslPolicyErrors.None) return false;
                        chain.ChainPolicy.TrustMode = System.Security.Cryptography.X509Certificates.X509ChainTrustMode.CustomRootTrust;
                        chain.ChainPolicy.CustomTrustStore.Add(caCert);
                        return chain.Build(cert);
                    };
                }
                catch (System.Security.Cryptography.CryptographicException)
                {
                    throw new InvalidOperationException("CART_SERVICE_MTLS_CA_CERT_PATH contains invalid PEM format");
                }
            }
        });
    });
}
else if (mtlsEnabled)
{
    throw new InvalidOperationException("CART_SERVICE_MTLS_ENABLED requires CART_SERVICE_TLS_ENABLED to be true");
}

string valkeyAddress = builder.Configuration["VALKEY_ADDR"];
if (string.IsNullOrEmpty(valkeyAddress))
{
    Console.WriteLine("VALKEY_ADDR environment variable is required.");
    Environment.Exit(1);
}

builder.Logging
    .AddOpenTelemetry(options => options.AddOtlpExporter())
    .AddConsole();

builder.Services.AddSingleton<ICartStore>(x =>
{
    var store = new ValkeyCartStore(x.GetRequiredService<ILogger<ValkeyCartStore>>(), valkeyAddress);
    store.Initialize();
    return store;
});

builder.Services.AddOpenFeature(openFeatureBuilder =>
{
    openFeatureBuilder
        .AddProvider(_ => new FlagdProvider())
        .AddHook<MetricsHook>()
        .AddHook<TraceEnricherHook>();
});

builder.Services.AddValidatorsFromAssemblyContaining<GetCartRequestValidator>();
builder.Services.AddGrpc(options =>
{
    options.EnableDetailedErrors = true;
})
.AddFluentValidation();

builder.Services.AddSingleton(x =>
    new CartService(
        x.GetRequiredService<ICartStore>(),
        new ValkeyCartStore(x.GetRequiredService<ILogger<ValkeyCartStore>>(), "badhost:1234"),
        x.GetRequiredService<IFeatureClient>()
));


Action<ResourceBuilder> appResourceBuilder =
    resource => resource
        .AddService(builder.Environment.ApplicationName)
        .AddContainerDetector()
        .AddHostDetector();

builder.Services.AddOpenTelemetry()
    .ConfigureResource(appResourceBuilder)
    .WithTracing(tracerBuilder => tracerBuilder
        .AddSource("OpenTelemetry.Demo.Cart")
        .AddRedisInstrumentation(
            options => options.SetVerboseDatabaseStatements = true)
        .AddAspNetCoreInstrumentation()
        .AddGrpcClientInstrumentation()
        .AddHttpClientInstrumentation()
        .AddOtlpExporter())
    .WithMetrics(meterBuilder => meterBuilder
        .AddMeter("OpenTelemetry.Demo.Cart")
        .AddMeter("OpenFeature")
        .AddProcessInstrumentation()
        .AddRuntimeInstrumentation()
        .AddAspNetCoreInstrumentation()
        .SetExemplarFilter(ExemplarFilterType.TraceBased)
        .AddOtlpExporter());
builder.Services.AddGrpc();
builder.Services.AddSingleton<readinessCheck>();
builder.Services.AddGrpcHealthChecks()
    .AddCheck<readinessCheck>("oteldemo.CartService");

// Add HTTP health checks
builder.Services.AddHealthChecks()
    .AddCheck<readinessCheck>("readiness")
    .AddCheck("valkey", () =>
    {
        try
        {
            var store = builder.Services.BuildServiceProvider().GetRequiredService<ICartStore>();
            if (store.Ping())
            {
                return HealthCheckResult.Healthy();
            }
            return HealthCheckResult.Unhealthy("Connection to Redis failed: Ping returned false");
        }
        catch (Exception ex)
        {
            return HealthCheckResult.Unhealthy($"Connection to Redis failed: {ex.Message}");
        }
    }, tags: new[] { "health", "ready" });

builder.Services.AddSingleton<HealthServiceImpl>();

var app = builder.Build();

// Register shutdown handlers
var lifetime = app.Services.GetRequiredService<IHostApplicationLifetime>();
var cartStore = app.Services.GetRequiredService<ICartStore>();
var logger = app.Services.GetRequiredService<ILogger<Program>>();
RegisterShutdownHandlers(lifetime, cartStore, logger);

// Add shutdown middleware
app.Use(async (context, next) =>
{
    if (_isShuttingDown)
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        context.Response.Headers.RetryAfter = ShutdownGracePeriodSeconds.ToString();
        return;
    }

    Interlocked.Increment(ref _inFlightRequests);
    try
    {
        await next(context);
    }
    finally
    {
        Interlocked.Decrement(ref _inFlightRequests);
        Interlocked.Increment(ref _completedRequests);
    }
});

var ValkeyCartStore = (ValkeyCartStore)app.Services.GetRequiredService<ICartStore>();
app.Services.GetRequiredService<StackExchangeRedisInstrumentation>().AddConnection(ValkeyCartStore.GetConnection());

app.MapGrpcService<CartService>();
app.MapGrpcService<HealthServiceImpl>();

app.MapGet("/", async context =>
{
    await context.Response.WriteAsync("Communication with gRPC endpoints must be made through a gRPC client. To learn how to create a client, visit: https://go.microsoft.com/fwlink/?linkid=2086909");
});

// Map HTTP health endpoints
app.MapHealthChecks("/health", new HealthCheckOptions
{
    Predicate = check => check.Tags.Contains("health"),
    ResultStatusCodes =
    {
        [HealthStatus.Healthy] = StatusCodes.Status200OK,
        [HealthStatus.Unhealthy] = StatusCodes.Status503ServiceUnavailable,
        [HealthStatus.Degraded] = StatusCodes.Status503ServiceUnavailable
    },
    ResponseWriter = async (context, report) =>
    {
        context.Response.ContentType = "application/json";
        var response = new
        {
            status = report.Status == HealthStatus.Healthy ? "Healthy" : "Unhealthy",
            errors = report.Entries.SelectMany(e => e.Value.Errors.Select(err => err.Message)).ToList()
        };
        await context.Response.WriteAsJsonAsync(response);
    }
}).AllowAnonymous();

app.MapHealthChecks("/ready", new HealthCheckOptions
{
    Predicate = check => check.Tags.Contains("ready") || check.Name == "readiness",
    ResultStatusCodes =
    {
        [HealthStatus.Healthy] = StatusCodes.Status200OK,
        [HealthStatus.Unhealthy] = StatusCodes.Status503ServiceUnavailable,
        [HealthStatus.Degraded] = StatusCodes.Status503ServiceUnavailable
    },
    ResponseWriter = async (context, report) =>
    {
        context.Response.ContentType = "application/json";
        var response = new
        {
            status = report.Status == HealthStatus.Healthy ? "Ready" : "NotReady",
            errors = report.Entries.SelectMany(e => e.Value.Errors.Select(err => err.Message)).ToList()
        };
        await context.Response.WriteAsJsonAsync(response);
    }
}).AllowAnonymous();

app.Run();


