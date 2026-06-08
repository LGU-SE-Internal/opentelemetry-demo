// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Threading;
using System.Threading.Tasks;
using Grpc.Core;
using Microsoft.Extensions.Logging;
using Oteldemo;
using Polly;
using Polly.CircuitBreaker;
using StackExchange.Redis;

namespace cart.cartstore;

public class CircuitBreakerCartStore : ICartStore
{
    private readonly ICartStore _innerStore;
    private readonly ILogger<CircuitBreakerCartStore> _logger;
    private readonly ResiliencePipeline<RedisResult> _resiliencePipeline;
    private readonly Meter _meter;
    private readonly Counter<long> _trippedCounter;
    private readonly Counter<long> _resetCounter;
    private readonly ObservableGauge<long> _stateGauge;
    private CircuitBreakerState _currentState = CircuitBreakerState.Closed;

    private const string UnavailableMessage = "Service temporarily unavailable: cart storage is down. Please retry later.";

    public ResiliencePipeline<RedisResult> GetRedisResiliencePipeline() => _resiliencePipeline;

    public CircuitBreakerCartStore(
        ICartStore innerStore,
        ILogger<CircuitBreakerCartStore> logger,
        CircuitBreakerSettings settings,
        IMeterFactory meterFactory)
    {
        _innerStore = innerStore;
        _logger = logger;

        // Setup metrics
        _meter = meterFactory.Create("CartService");
        _trippedCounter = _meter.CreateCounter<long>("cart_service_redis_circuit_breaker_tripped_total");
        _resetCounter = _meter.CreateCounter<long>("cart_service_redis_circuit_breaker_reset_total");
        _stateGauge = _meter.CreateObservableGauge<long>(
            "cart_service_redis_circuit_breaker_state",
            () => (long)_currentState,
            description: "Current circuit breaker state: 0=closed, 1=open, 2=half-open");

        // Build resilience pipeline
        _resiliencePipeline = new ResiliencePipelineBuilder<RedisResult>()
            .AddCircuitBreaker(new CircuitBreakerStrategyOptions<RedisResult>
            {
                FailureThreshold = settings.FailureThreshold,
                BreakDuration = settings.ResetTimeout,
                SamplingDuration = settings.SamplingDuration,
                ShouldHandle = args => args.Outcome switch
                {
                    { Exception: RedisException } => PredicateResult.True(),
                    { Result: { IsNull: true, HasError: true } } => PredicateResult.True(),
                    _ => PredicateResult.False()
                },
                OnOpened = args =>
                {
                    _logger.LogWarning("Redis circuit breaker opened after {FailureCount} failures, will remain open for {BreakDuration}",
                        args.FailureCount, args.BreakDuration);
                    _currentState = CircuitBreakerState.Open;
                    _trippedCounter.Add(1);
                    return default;
                },
                OnHalfOpened = args =>
                {
                    _logger.LogInformation("Redis circuit breaker entering half-open state, testing Redis connectivity");
                    _currentState = CircuitBreakerState.HalfOpen;
                    return default;
                },
                OnClosed = args =>
                {
                    _logger.LogInformation("Redis circuit breaker closed, normal operation resumed");
                    _currentState = CircuitBreakerState.Closed;
                    _resetCounter.Add(1);
                    return default;
                }
            })
            .Build();
    }

    public void Initialize() => _innerStore.Initialize();

    public async Task AddItemAsync(string userId, string productId, int quantity)
    {
        try
        {
            await _resiliencePipeline.ExecuteAsync(async _ =>
            {
                await _innerStore.AddItemAsync(userId, productId, quantity);
                return RedisResult.Create(RedisValue.Null);
            }, CancellationToken.None);
        }
        catch (BrokenCircuitException)
        {
            _logger.LogWarning("Redis circuit breaker is open, rejecting AddItem request");
            throw new RpcException(new Status(StatusCode.Unavailable, UnavailableMessage));
        }
    }

    public async Task EmptyCartAsync(string userId)
    {
        try
        {
            await _resiliencePipeline.ExecuteAsync(async _ =>
            {
                await _innerStore.EmptyCartAsync(userId);
                return RedisResult.Create(RedisValue.Null);
            }, CancellationToken.None);
        }
        catch (BrokenCircuitException)
        {
            _logger.LogWarning("Redis circuit breaker is open, rejecting EmptyCart request");
            throw new RpcException(new Status(StatusCode.Unavailable, UnavailableMessage));
        }
    }

    public async Task<Cart> GetCartAsync(string userId)
    {
        try
        {
            return await _resiliencePipeline.ExecuteAsync(async _ =>
            {
                return await _innerStore.GetCartAsync(userId);
            }, CancellationToken.None);
        }
        catch (BrokenCircuitException)
        {
            _logger.LogWarning("Redis circuit breaker is open, rejecting GetCart request");
            throw new RpcException(new Status(StatusCode.Unavailable, UnavailableMessage));
        }
    }

    public bool Ping()
    {
        try
        {
            return _resiliencePipeline.Execute(_ =>
            {
                return _innerStore.Ping();
            }, CancellationToken.None);
        }
        catch (BrokenCircuitException)
        {
            _logger.LogWarning("Redis circuit breaker is open, rejecting Ping request");
            return false;
        }
    }

    public async Task FlushAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            await _resiliencePipeline.ExecuteAsync(async ct =>
            {
                await _innerStore.FlushAsync(ct);
                return RedisResult.Create(RedisValue.Null);
            }, cancellationToken);
        }
        catch (BrokenCircuitException)
        {
            _logger.LogWarning("Redis circuit breaker is open, rejecting Flush request");
            throw new RpcException(new Status(StatusCode.Unavailable, UnavailableMessage));
        }
    }
}
