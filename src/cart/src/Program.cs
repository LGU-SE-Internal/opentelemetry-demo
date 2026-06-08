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
string mtlsMode = builder.Configuration["CART_SERVICE_MTLS_MODE"] ?? "disabled";
string mtlsCaCertPath = builder.Configuration["CART_SERVICE_TLS_CA_CERT_PATH"] ?? string.Empty;

// Validate mTLS mode value
if (mtlsMode is not "disabled" and not "optional" and not "required")
{
    throw new InvalidOperationException($"Invalid CART_SERVICE_MTLS_MODE value: {mtlsMode}. Must be one of 'disabled', 'optional', 'required'");
}

if (tlsEnabled)
{
    if (string.IsNullOrEmpty(tlsCertPath) || string.IsNullOrEmpty(tlsKeyPath))
    {
        throw new InvalidOperationException("Missing required TLS configuration: CART_SERVICE_TLS_CERT_PATH and CART_SERVICE_TLS_KEY_PATH must be set when TLS is enabled");
    }
    if (!File.Exists(tlsCertPath))
    {
        throw new InvalidOperationException($"Invalid TLS certificate or private key: File not found at {tlsCertPath}");
    }
    if (!File.Exists(tlsKeyPath))
    {
        throw new InvalidOperationException($"Invalid TLS certificate or private key: File not found at {tlsKeyPath}");
    }

    X509Certificate2 serverCert;
    try
    {
        serverCert = X509Certificate2.CreateFromPemFile(tlsCertPath, tlsKeyPath);
    }
    catch (System.Security.Cryptography.CryptographicException ex)
    {
        throw new InvalidOperationException($"Invalid TLS certificate or private key: {ex.Message}");
    }

    // Validate mTLS configuration if needed
    X509Certificate2? caCert = null;
    if (mtlsMode is "optional" or "required")
    {
        if (string.IsNullOrEmpty(mtlsCaCertPath))
        {
            throw new InvalidOperationException("Missing required CA certificate path: CART_SERVICE_TLS_CA_CERT_PATH must be set when mTLS mode is optional or required");
        }
        if (!File.Exists(mtlsCaCertPath))
        {
            throw new InvalidOperationException($"Invalid CA certificate file: File not found at {mtlsCaCertPath}");
        }
        try
        {
            caCert = new X509Certificate2(mtlsCaCertPath);
        }
        catch (System.Security.Cryptography.CryptographicException ex)
        {
            throw new InvalidOperationException($"Invalid CA certificate file: {ex.Message}");
        }
    }

    // Configure Kestrel TLS
    builder.WebHost.ConfigureKestrel(options =>
    {
        options.ConfigureHttpsDefaults(httpsOptions =>
        {
            httpsOptions.ServerCertificate = serverCert;
            httpsOptions.SslProtocols = System.Security.Authentication.SslProtocols.Tls12 | System.Security.Authentication.SslProtocols.Tls13;

            if (mtlsMode == "optional")
            {
                httpsOptions.ClientCertificateMode = Microsoft.AspNetCore.Server.Kestrel.Https.ClientCertificateMode.AllowCertificate;
            }
            else if (mtlsMode == "required")
            {
                httpsOptions.ClientCertificateMode = Microsoft.AspNetCore.Server.Kestrel.Https.ClientCertificateMode.RequireCertificate;
            }

            if (mtlsMode is "optional" or "required" && caCert != null)
            {
                httpsOptions.ClientCertificateValidation = (cert, chain, errors) =>
                {
                    if (mtlsMode == "optional" && cert == null)
                    {
                        // No client certificate provided, allowed in optional mode
                        return true;
                    }

                    if (errors != SslPolicyErrors.None && errors != SslPolicyErrors.RemoteCertificateChainErrors)
                    {
                        return false;
                    }

                    chain!.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                    chain.ChainPolicy.CustomTrustStore.Add(caCert);
                    chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                    return chain.Build(cert!);
                };
            }
        });
    });
}
else if (mtlsMode is "optional" or "required")
{
    throw new InvalidOperationException("mTLS mode cannot be set to optional or required when TLS is disabled (CART_SERVICE_TLS_ENABLED must be true)");
}

string valkeyAddress = builder.Configuration["VALKEY_ADDR"];
if (string.IsNullOrEmpty(valkeyAddress))
{
    Console.WriteLine("VALKEY_ADDR environment variable is required.");
    Environment.Exit(1);
}

// Load Redis retry policy settings from environment variables
var redisRetrySettings = new RedisRetryPolicySettings();
if (int.TryParse(builder.Configuration["CART_REDIS_MAX_RETRY_ATTEMPTS"], out int maxRetries))
{
    redisRetrySettings.MaxRetryAttempts = maxRetries;
}
if (int.TryParse(builder.Configuration["CART_REDIS_INITIAL_BACKOFF_MS"], out int initialBackoff))
{
    redisRetrySettings.InitialBackoffMs = initialBackoff;
}
if (int.TryParse(builder.Configuration["CART_REDIS_MAX_BACKOFF_MS"], out int maxBackoff))
{
    redisRetrySettings.MaxBackoffMs = maxBackoff;
}
builder.Services.AddSingleton(redisRetrySettings);

builder.Logging
    .AddOpenTelemetry(options => options.AddOtlpExporter())
    .AddConsole();

builder.Services.AddSingleton<ValkeyCartStore>(x =>
{
    var store = new ValkeyCartStore(x.GetRequiredService<ILogger<ValkeyCartStore>>(), valkeyAddress);
    store.Initialize();
    return store;
});

builder.Services.AddSingleton<ICartStore>(x =>
{
    var innerStore = x.GetRequiredService<ValkeyCartStore>();
    var logger = x.GetRequiredService<ILogger<RetryingCartStore>>();
    var settings = x.GetRequiredService<RedisRetryPolicySettings>();
    return new RetryingCartStore(innerStore, logger, settings);
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

var ValkeyCartStore = app.Services.GetRequiredService<ValkeyCartStore>();
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


