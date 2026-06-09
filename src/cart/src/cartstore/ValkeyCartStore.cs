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

namespace cart.cartstore;

public class ValkeyCartStore : ICartStore
{
    private readonly ILogger _logger;
    private const string CartFieldName = "cart";
    private const int RedisRetryNumber = 30;

    private volatile ConnectionMultiplexer _redis;
    private volatile bool _isRedisConnectionOpened;

    private readonly object _locker = new();
    private readonly byte[] _emptyCartBytes;
    private readonly string _connectionString;

    private static readonly ActivitySource CartActivitySource = new("OpenTelemetry.Demo.Cart");
    private static readonly Meter CartMeter = new Meter("OpenTelemetry.Demo.Cart");
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
    private static readonly UpDownGauge<int> CircuitBreakerStateGauge = CartMeter.CreateUpDownGauge<int>(
        "resilience.circuit_breaker.state",
        unit: "1",
        description: "Current state of the circuit breaker: 0 = CLOSED, 1 = OPEN, 2 = HALF_OPEN");
    private static readonly Counter<int> CircuitBreakerTransitionsCounter = CartMeter.CreateCounter<int>(
        "resilience.circuit_breaker.transitions",
        unit: "{transition}",
        description: "Total number of circuit breaker state transitions");
    private static readonly Counter<int> CircuitBreakerRequestsRejectedCounter = CartMeter.CreateCounter<int>(
        "resilience.circuit_breaker.requests_rejected",
        unit: "{request}",
        description: "Total number of requests rejected while the circuit breaker is open");
    private readonly ConfigurationOptions _redisConnectionOptions;
    private readonly AsyncCircuitBreakerPolicy _circuitBreakerPolicy;

    public ValkeyCartStore(ILogger<ValkeyCartStore> logger, string valkeyAddress)
    {
        _logger = logger;
        // Serialize empty cart into byte array.
        var cart = new Oteldemo.Cart();
        _emptyCartBytes = cart.ToByteArray();
        _connectionString = $"{valkeyAddress},ssl=false,allowAdmin=true,abortConnect=false";

        _redisConnectionOptions = ConfigurationOptions.Parse(_connectionString);

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

            _redis.InternalError += (_, e) => { Console.WriteLine(e.Exception); };
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
            throw new RpcException(new Status(StatusCode.Unavailable, "Redis circuit breaker is open; please try again later"));
        }
        catch (Exception ex)
        {
            throw new RpcException(new Status(StatusCode.FailedPrecondition, $"Can't access cart storage. {ex}"));
        }
    }
}
