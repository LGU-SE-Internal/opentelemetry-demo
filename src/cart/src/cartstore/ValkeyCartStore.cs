// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Linq;
using System.Threading.Tasks;
using Grpc.Core;
using StackExchange.Redis;
using Google.Protobuf;
using Microsoft.Extensions.Logging;
using System.Diagnostics.Metrics;
using System.Diagnostics;
using Polly;
using Polly.CircuitBreaker;
using Microsoft.Extensions.Configuration;
using System.Configuration;
using System.IO;
using System.Security.Cryptography.X509Certificates;

namespace cart.cartstore;

public class ValkeyCartStore : ICartStore
{
    private readonly ILogger _logger;
    private const string CartFieldName = "cart";
    private const int RedisRetryNumber = 30;
    
    private const string TlsEnabledEnvVar = "CART_SERVICE_VALKEY_TLS_ENABLED";
    private const string CaCertPathEnvVar = "CART_SERVICE_VALKEY_CA_CERT_PATH";
    private const string ClientCertPathEnvVar = "CART_SERVICE_VALKEY_CLIENT_CERT_PATH";
    private const string ClientKeyPathEnvVar = "CART_SERVICE_VALKEY_CLIENT_KEY_PATH";

    private volatile ConnectionMultiplexer _redis;
    private volatile bool _isRedisConnectionOpened;

    private readonly object _locker = new();
    private readonly byte[] _emptyCartBytes;
    private readonly string _connectionString;

    private static readonly ActivitySource CartActivitySource = new("OpenTelemetry.Demo.Cart");
    private static readonly Meter CartMeter = new Meter("OpenTelemetry.Demo.Cart");
    private static readonly Meter ResilienceMeter = new Meter("Cartservice.Resilience");
    private static readonly Histogram<double> addItemHistogram = CartMeter.CreateHistogram(
        "demo.cart.add_item.latency",
        unit: "s",
        advice: new InstrumentAdvice<double>
        {
            HistogramBucketBoundaries = [ 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 ]
        });
    private static readonly Histogram<double> getCartHistogram = CartMeter.CreateHistogram(
        "demo.cart.get_cart.latency",
        unit: "s",
        advice: new InstrumentAdvice<double>
        {
            HistogramBucketBoundaries = [ 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 ]
        });
    private static readonly UpDownGauge<int> CircuitBreakerStateGauge = ResilienceMeter.CreateUpDownGauge<int>(
        "resilience.circuit_breaker.state",
        unit: "1",
        description: "Current state of the circuit breaker: 0 = CLOSED, 1 = OPEN, 2 = HALF_OPEN");
    private static readonly Counter<int> CircuitBreakerTransitionsCounter = ResilienceMeter.CreateCounter<int>(
        "resilience.circuit_breaker.transitions",
        unit: "{transition}",
        description: "Total number of circuit breaker state transitions");
    private static readonly Counter<int> CircuitBreakerRequestsRejectedCounter = ResilienceMeter.CreateCounter<int>(
        "resilience.circuit_breaker.requests_rejected",
        unit: "{request}",
        description: "Total number of requests rejected while the circuit breaker is open");
    private readonly ConfigurationOptions _redisConnectionOptions;
    private readonly AsyncCircuitBreakerPolicy _circuitBreakerPolicy;
    public AsyncCircuitBreakerPolicy CircuitBreaker => _circuitBreakerPolicy;

    public ValkeyCartStore(ILogger<ValkeyCartStore> logger, IConfiguration configuration)
    {
        _logger = logger;
        // Serialize empty cart into byte array.
        var cart = new Oteldemo.Cart();
        _emptyCartBytes = cart.ToByteArray();
        
        var valkeyAddress = configuration["ValkeyAddress"] ?? "valkey:6379";
        bool tlsEnabled = configuration.GetValue<bool>(TlsEnabledEnvVar, false);
        string caCertPath = configuration.GetValue<string>(CaCertPathEnvVar, string.Empty);
        string clientCertPath = configuration.GetValue<string>(ClientCertPathEnvVar, string.Empty);
        string clientKeyPath = configuration.GetValue<string>(ClientKeyPathEnvVar, string.Empty);

        // Validate configuration
        if (tlsEnabled)
        {
            // Validate client cert/key pair
            if (!string.IsNullOrEmpty(clientCertPath) && string.IsNullOrEmpty(clientKeyPath))
            {
                throw new ConfigurationException($"Client certificate path provided but no corresponding client key path. Please set {ClientKeyPathEnvVar}");
            }
            
            if (!string.IsNullOrEmpty(clientKeyPath) && string.IsNullOrEmpty(clientCertPath))
            {
                throw new ConfigurationException($"Client key path provided but no corresponding client certificate path. Please set {ClientCertPathEnvVar}");
            }
            
            // Validate CA cert path exists if provided
            if (!string.IsNullOrEmpty(caCertPath) && !File.Exists(caCertPath))
            {
                throw new ConfigurationException($"CA certificate file not found at path: {caCertPath}");
            }
            
            // Validate client cert and key paths exist if provided
            if (!string.IsNullOrEmpty(clientCertPath) && !File.Exists(clientCertPath))
            {
                throw new ConfigurationException($"Client certificate file not found at path: {clientCertPath}");
            }
            
            if (!string.IsNullOrEmpty(clientKeyPath) && !File.Exists(clientKeyPath))
            {
                throw new ConfigurationException($"Client key file not found at path: {clientKeyPath}");
            }
        }

        // Build connection string base
        _connectionString = $"{valkeyAddress},ssl={tlsEnabled.ToString().ToLower()},allowAdmin=true,abortConnect=false";
        
        _redisConnectionOptions = ConfigurationOptions.Parse(_connectionString);
        
        if (tlsEnabled)
        {
            // Enforce minimum TLS version 1.2
            _redisConnectionOptions.SslProtocols = System.Security.Authentication.SslProtocols.Tls12 | System.Security.Authentication.SslProtocols.Tls13;
            
            // Add custom CA certificate if provided
            if (!string.IsNullOrEmpty(caCertPath))
            {
                var caCert = new X509Certificate2(caCertPath);
                _redisConnectionOptions.CertificateValidation += (sender, cert, chain, sslPolicyErrors) =>
                {
                    if (sslPolicyErrors == System.Net.Security.SslPolicyErrors.None)
                        return true;
                    
                    chain.ChainPolicy.ExtraStore.Add(caCert);
                    chain.ChainPolicy.VerificationFlags = X509VerificationFlags.AllowUnknownCertificateAuthority;
                    return chain.Build((X509Certificate2)cert);
                };
            }
            
            // Add client certificate for mTLS if provided
            if (!string.IsNullOrEmpty(clientCertPath) && !string.IsNullOrEmpty(clientKeyPath))
            {
                var clientCert = X509Certificate2.CreateFromPemFile(clientCertPath, clientKeyPath);
                _redisConnectionOptions.Certificates.Add(clientCert);
            }
        }

        // Try to reconnect multiple times if the first retry fails.
        _redisConnectionOptions.ConnectRetry = RedisRetryNumber;
        _redisConnectionOptions.ReconnectRetryPolicy = new ExponentialRetry(1000);

        _redisConnectionOptions.KeepAlive = 180;

        // Configure circuit breaker policy
        _circuitBreakerPolicy = Policy
            .Handle<Exception>()
            .CircuitBreakerAsync(
                exceptionsAllowedBeforeBreaking: 5,
                durationOfBreak: TimeSpan.FromSeconds(30),
                onBreak: (ex, state, duration, context) =>
                {
                    _logger.LogInformation("Circuit breaker transitioning from {PreviousState} to OPEN state for {Duration} seconds. Reason: {ExceptionMessage}",
                        state, duration.TotalSeconds, ex.Message);
                    // Update state gauge to OPEN (1)
                    CircuitBreakerStateGauge.Record(1, new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"));
                    // Emit transition counter
                    CircuitBreakerTransitionsCounter.Add(1,
                        new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"),
                        new KeyValuePair<string, object>("resilience.circuit_breaker.state.from", state.ToString().ToUpperInvariant()),
                        new KeyValuePair<string, object>("resilience.circuit_breaker.state.to", "OPEN"));
                },
                onReset: context =>
                {
                    _logger.LogInformation("Circuit breaker transitioning from HALF-OPEN to CLOSED state. Test call succeeded.");
                    // Update state gauge to CLOSED (0)
                    CircuitBreakerStateGauge.Record(0, new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"));
                    // Emit transition counter
                    CircuitBreakerTransitionsCounter.Add(1,
                        new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"),
                        new KeyValuePair<string, object>("resilience.circuit_breaker.state.from", "HALF_OPEN"),
                        new KeyValuePair<string, object>("resilience.circuit_breaker.state.to", "CLOSED"));
                },
                onHalfOpen: () =>
                {
                    _logger.LogInformation("Circuit breaker transitioning from OPEN to HALF-OPEN state. Allowing 1 test call.");
                    // Update state gauge to HALF_OPEN (2)
                    CircuitBreakerStateGauge.Record(2, new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"));
                    // Emit transition counter
                    CircuitBreakerTransitionsCounter.Add(1,
                        new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"),
                        new KeyValuePair<string, object>("resilience.circuit_breaker.state.from", "OPEN"),
                        new KeyValuePair<string, object>("resilience.circuit_breaker.state.to", "HALF_OPEN"));
                });
    }

    public ValkeyCartStore(ILogger<ValkeyCartStore> logger, string valkeyAddress)
        : this(logger, new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string> { { "ValkeyAddress", valkeyAddress } }).Build())
    {
    }

    public ConnectionMultiplexer GetConnection()
    {
        EnsureRedisConnected();
        return _redis;
    }

    public void Initialize()
    {
        EnsureRedisConnected();
    }

    private void EnsureRedisConnected()
    {
        if (_isRedisConnectionOpened)
        {
            return;
        }

        // Connection is closed or failed - open a new one but only at the first thread
        lock (_locker)
        {
            if (_isRedisConnectionOpened)
            {
                return;
            }

            if (_logger.IsEnabled(LogLevel.Debug))
            {
                _logger.LogDebug("Connecting to Redis: {connectionString}", _connectionString);
            }

            _redis = ConnectionMultiplexer.Connect(_redisConnectionOptions);

            if (_redis == null || !_redis.IsConnected)
            {
                _logger.LogError("Wasn't able to connect to redis");

                // We weren't able to connect to Redis despite some retries with exponential backoff.
                throw new ApplicationException("Wasn't able to connect to redis");
            }

            _logger.LogInformation("Successfully connected to Redis");
            var cache = _redis.GetDatabase();

            _logger.LogDebug("Performing small test");
            cache.StringSet("cart", "OK" );
            object res = cache.StringGet("cart");

            if (_logger.IsEnabled(LogLevel.Debug))
            {
                _logger.LogDebug("Small test result: {result}", res);
            }

            _redis.InternalError += (_, e) => { _logger.LogError(e.Exception, "Redis internal error occurred"); };
            _redis.ConnectionRestored += (_, _) =>
            {
                _isRedisConnectionOpened = true;
                _logger.LogInformation("Connection to redis was restored successfully.");
            };
            _redis.ConnectionFailed += (_, _) =>
            {
                _logger.LogInformation("Connection failed. Disposing the object");
                _isRedisConnectionOpened = false;
            };

            _isRedisConnectionOpened = true;
        }
    }

    public async Task AddItemAsync(string userId, string productId, int quantity)
    {
        var stopwatch = Stopwatch.StartNew();

        if (_logger.IsEnabled(LogLevel.Information))
        {
            _logger.LogInformation("AddItemAsync called with userId={userId}, productId={productId}, quantity={quantity}", userId, productId, quantity);
        }

        try
        {
            await _circuitBreakerPolicy.ExecuteAsync(async () =>
            {
                EnsureRedisConnected();

                var db = _redis.GetDatabase();

                // Access the cart from the cache
                var value = await db.HashGetAsync(userId, CartFieldName);

                Oteldemo.Cart cart;
                if (value.IsNull)
                {
                    cart = new Oteldemo.Cart
                    {
                        UserId = userId
                    };
                    cart.Items.Add(new Oteldemo.CartItem { ProductId = productId, Quantity = quantity });
                }
                else
                {
                    cart = Oteldemo.Cart.Parser.ParseFrom(value);
                    var existingItem = cart.Items.SingleOrDefault(i => i.ProductId == productId);
                    if (existingItem == null)
                    {
                        cart.Items.Add(new Oteldemo.CartItem { ProductId = productId, Quantity = quantity });
                    }
                    else
                    {
                        existingItem.Quantity += quantity;
                    }
                }

                await db.HashSetAsync(userId, new[]{ new HashEntry(CartFieldName, cart.ToByteArray()) });
                await db.KeyExpireAsync(userId, TimeSpan.FromMinutes(60));
            });
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open for Valkey/Redis operations");
            // Record rejected request metric
            CircuitBreakerRequestsRejectedCounter.Add(1,
                new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"),
                new KeyValuePair<string, object>("error.type", "circuit_breaker_open"));
            throw new RpcException(new Status(StatusCode.Unavailable, "Redis circuit breaker is open; please try again later"));
        }
        catch (Exception ex)
        {
            throw new RpcException(new Status(StatusCode.FailedPrecondition, $"Can't access cart storage. {ex}"));
        }
        finally
        {
            addItemHistogram.Record(stopwatch.Elapsed.TotalSeconds);
        }
    }

    public async Task EmptyCartAsync(string userId)
    {
        if (_logger.IsEnabled(LogLevel.Information))
        {
            _logger.LogInformation("EmptyCartAsync called with userId={userId}", userId);
        }
        try
        {
            await _circuitBreakerPolicy.ExecuteAsync(async () =>
            {
                EnsureRedisConnected();
                var db = _redis.GetDatabase();

                // Update the cache with empty cart for given user
                await db.HashSetAsync(userId, new[] { new HashEntry(CartFieldName, _emptyCartBytes) });
                await db.KeyExpireAsync(userId, TimeSpan.FromMinutes(60));
            });
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open for Valkey/Redis operations");
            // Record rejected request metric
            CircuitBreakerRequestsRejectedCounter.Add(1,
                new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"),
                new KeyValuePair<string, object>("error.type", "circuit_breaker_open"));
            throw new RpcException(new Status(StatusCode.Unavailable, "Redis circuit breaker is open; please try again later"));
        }
        catch (Exception ex)
        {
            throw new RpcException(new Status(StatusCode.FailedPrecondition, $"Can't access cart storage. {ex}"));
        }
    }

    public async Task<Oteldemo.Cart> GetCartAsync(string userId)
    {
        var stopwatch = Stopwatch.StartNew();

        if (_logger.IsEnabled(LogLevel.Information))
        {
            _logger.LogInformation("GetCartAsync called with userId={userId}", userId);
        }

        try
        {
            return await _circuitBreakerPolicy.ExecuteAsync(async () =>
            {
                EnsureRedisConnected();

                var db = _redis.GetDatabase();

                // Access the cart from the cache
                var value = await db.HashGetAsync(userId, CartFieldName);

                if (!value.IsNull)
                {
                    return Oteldemo.Cart.Parser.ParseFrom(value);
                }

                // We decided to return empty cart in cases when user wasn't in the cache before
                return new Oteldemo.Cart();
            });
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open for Valkey/Redis operations");
            // Record rejected request metric
            CircuitBreakerRequestsRejectedCounter.Add(1,
                new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"),
                new KeyValuePair<string, object>("error.type", "circuit_breaker_open"));
            throw new RpcException(new Status(StatusCode.Unavailable, "Redis circuit breaker is open; please try again later"));
        }
        catch (Exception ex)
        {
            throw new RpcException(new Status(StatusCode.FailedPrecondition, $"Can't access cart storage. {ex}"));
        }
        finally
        {
            getCartHistogram.Record(stopwatch.Elapsed.TotalSeconds);
        }
    }

    public bool Ping()
    {
        try
        {
            var cache = _redis.GetDatabase();
            var res = cache.Ping();
            return res != TimeSpan.Zero;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public async Task FlushAsync(CancellationToken cancellationToken = default)
    {
        if (_logger.IsEnabled(LogLevel.Debug))
        {
            _logger.LogDebug("Flushing pending cart store operations");
        }
        try
        {
            await _circuitBreakerPolicy.ExecuteAsync(async () =>
            {
                // For Redis, operations are immediately persisted, so no explicit flush needed
                // Just ensure connection is active and any pending commands are processed
                if (_isRedisConnectionOpened && _redis != null)
                {
                    await _redis.WaitAllAsync(_isShuttingDown: false, cancellationToken);
                }
                await Task.CompletedTask;
            });
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open for Valkey/Redis operations");
            // Record rejected request metric
            CircuitBreakerRequestsRejectedCounter.Add(1,
                new KeyValuePair<string, object>("resilience.circuit_breaker.name", "redis"),
                new KeyValuePair<string, object>("error.type", "circuit_breaker_open"));
            throw new RpcException(new Status(StatusCode.Unavailable, "Redis circuit breaker is open; please try again later"));
        }
        catch (Exception ex)
        {
            throw new RpcException(new Status(StatusCode.FailedPrecondition, $"Can't access cart storage. {ex}"));
        }
    }
}
