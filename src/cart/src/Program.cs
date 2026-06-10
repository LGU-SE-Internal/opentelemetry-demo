// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography.X509Certificates;
using System.Net.Security;
using System.Net;
using System.Threading.RateLimiting;

using Grpc.Health.V1;
using Grpc.Core;
using Grpc.Core.Interceptors;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using System.Threading.Tasks;
using System.Threading;

using cart.cartstore;
using cart.services;
using cart.healthcheck;
using cart.Interceptors;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.RateLimiting;
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
    var loggerFactory = LoggerFactory.Create(b => b.AddConsole().AddOpenTelemetry(options => options.AddOtlpExporter()));
    var logger = loggerFactory.CreateLogger<Program>();
    logger.LogError("VALKEY_ADDR environment variable is not configured");
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

// Rate Limiting Configuration
const string GetCartPolicy = "GetCartPolicy";
const string AddItemPolicy = "AddItemPolicy";
const string RemoveItemPolicy = "RemoveItemPolicy";

// Load rate limit configuration from environment variables
int getCartPermitLimit = int.TryParse(builder.Configuration["CART_SERVICE_RATELIMIT_GETCART_PERMITLIMIT"], out int gcl) ? gcl : 100;
int getCartWindowSeconds = int.TryParse(builder.Configuration["CART_SERVICE_RATELIMIT_GETCART_WINDOWSECONDS"], out int gcw) ? gcw : 10;

int addItemPermitLimit = int.TryParse(builder.Configuration["CART_SERVICE_RATELIMIT_ADDITEM_PERMITLIMIT"], out int ail) ? ail : 100;
int addItemWindowSeconds = int.TryParse(builder.Configuration["CART_SERVICE_RATELIMIT_ADDITEM_WINDOWSECONDS"], out int aiw) ? aiw : 10;

int removeItemPermitLimit = int.TryParse(builder.Configuration["CART_SERVICE_RATELIMIT_REMOVEITEM_PERMITLIMIT"], out int ril) ? ril : 100;
int removeItemWindowSeconds = int.TryParse(builder.Configuration["CART_SERVICE_RATELIMIT_REMOVEITEM_WINDOWSECONDS"], out int riw) ? riw : 10;

builder.Services.AddRateLimiter(rateLimiterOptions =>
{
    rateLimiterOptions.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    
    rateLimiterOptions.AddFixedWindowLimiter(policyName: GetCartPolicy, options =>
    {
        options.PermitLimit = getCartPermitLimit;
        options.Window = TimeSpan.FromSeconds(getCartWindowSeconds);
        options.QueueProcessingOrder = QueueProcessingOrder.OldestFirst;
        options.QueueLimit = 0;
    });
    
    rateLimiterOptions.AddFixedWindowLimiter(policyName: AddItemPolicy, options =>
    {
        options.PermitLimit = addItemPermitLimit;
        options.Window = TimeSpan.FromSeconds(addItemWindowSeconds);
        options.QueueProcessingOrder = QueueProcessingOrder.OldestFirst;
        options.QueueLimit = 0;
    });
    
    rateLimiterOptions.AddFixedWindowLimiter(policyName: RemoveItemPolicy, options =>
    {
        options.PermitLimit = removeItemPermitLimit;
        options.Window = TimeSpan.FromSeconds(removeItemWindowSeconds);
        options.QueueProcessingOrder = QueueProcessingOrder.OldestFirst;
        options.QueueLimit = 0;
    });
    
    // Create partition key based on client IP and endpoint
    rateLimiterOptions.AddPolicy<string, RateLimitPartitionKeyPolicy>(GetCartPolicy);
    rateLimiterOptions.AddPolicy<string, RateLimitPartitionKeyPolicy>(AddItemPolicy);
    rateLimiterOptions.AddPolicy<string, RateLimitPartitionKeyPolicy>(RemoveItemPolicy);
    
    // On rejected request, return appropriate gRPC status and log
    rateLimiterOptions.OnRejected = async (context, cancellationToken) =>
    {
        var httpContext = context.HttpContext;
        var endpoint = httpContext.GetEndpoint();
        var logger = httpContext.RequestServices.GetRequiredService<ILogger<Program>>();
        
        string endpointName = endpoint?.DisplayName ?? "Unknown";
        var clientIp = httpContext.Connection.RemoteIpAddress?.ToString() ?? "Unknown";
        var requestId = httpContext.TraceIdentifier;
        
        // Get applied limit and window based on policy
        var policyName = endpoint?.Metadata.GetMetadata<RateLimiterMetadata>()?.PolicyName;
        int appliedLimit = 100;
        int appliedWindow = 10;
        
        if (policyName == GetCartPolicy)
        {
            appliedLimit = getCartPermitLimit;
            appliedWindow = getCartWindowSeconds;
        }
        else if (policyName == AddItemPolicy)
        {
            appliedLimit = addItemPermitLimit;
            appliedWindow = addItemWindowSeconds;
        }
        else if (policyName == RemoveItemPolicy)
        {
            appliedLimit = removeItemPermitLimit;
            appliedWindow = removeItemWindowSeconds;
        }
        
        // Log structured rate limit event
        logger.LogInformation(
            "RateLimitExceeded: Endpoint={EndpointName}, ClientIp={ClientIpAddress}, RequestId={RequestId}, Limit={AppliedPermitLimit}, Window={AppliedWindowSeconds}s",
            endpointName, clientIp, requestId, appliedLimit, appliedWindow);
        
        // Return gRPC RESOURCE_EXHAUSTED status
        httpContext.Response.ContentType = "application/grpc";
        httpContext.Response.Headers["grpc-status"] = ((int)StatusCode.ResourceExhausted).ToString();
        httpContext.Response.Headers["grpc-message"] = $"Rate limit exceeded for endpoint {endpointName}. Please try again later.";
        
        await Task.CompletedTask;
    };
});

// Add HTTP context accessor for IP extraction
builder.Services.AddHttpContextAccessor();

// Custom partition key policy that combines client IP and endpoint
public class RateLimitPartitionKeyPolicy : IRateLimiterPolicy<string>
{
    public Func<OnRejectedContext, CancellationToken, ValueTask>? OnRejected { get; }
    
    public RateLimitPartition<string> GetPartition(HttpContext httpContext)
    {
        var clientIp = httpContext.Connection.RemoteIpAddress?.ToString() ?? string.Empty;
        var endpoint = httpContext.GetEndpoint()?.DisplayName ?? string.Empty;
        var partitionKey = $"{clientIp}_{endpoint}";
        
        var policyName = httpContext.GetEndpoint()?.Metadata.GetMetadata<RateLimiterMetadata>()?.PolicyName;
        
        // Get the policy options based on policy name
        return policyName switch
        {
            GetCartPolicy => RateLimitPartition.GetFixedWindowLimiter(partitionKey, _ => new FixedWindowRateLimiterOptions
            {
                PermitLimit = getCartPermitLimit,
                Window = TimeSpan.FromSeconds(getCartWindowSeconds),
                QueueLimit = 0
            }),
            AddItemPolicy => RateLimitPartition.GetFixedWindowLimiter(partitionKey, _ => new FixedWindowRateLimiterOptions
            {
                PermitLimit = addItemPermitLimit,
                Window = TimeSpan.FromSeconds(addItemWindowSeconds),
                QueueLimit = 0
            }),
            RemoveItemPolicy => RateLimitPartition.GetFixedWindowLimiter(partitionKey, _ => new FixedWindowRateLimiterOptions
            {
                PermitLimit = removeItemPermitLimit,
                Window = TimeSpan.FromSeconds(removeItemWindowSeconds),
                QueueLimit = 0
            }),
            _ => RateLimitPartition.GetNoLimiter(partitionKey)
        };
    }
}

builder.Services.AddGrpc(options =>
{
    options.EnableDetailedErrors = true;
})
.AddFluentValidation();

var app = builder.Build();

// Use rate limiting middleware
app.UseRateLimiter();

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


