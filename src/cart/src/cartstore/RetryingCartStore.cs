// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using System;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Polly;
using Polly.Retry;
using StackExchange.Redis;
using Oteldemo;

namespace cart.cartstore;

public class RetryingCartStore : ICartStore
{
    private readonly ICartStore _innerStore;
    private readonly ILogger<RetryingCartStore> _logger;
    private readonly ResiliencePipeline _retryPipeline;
    private readonly Random _random = new Random();

    public RetryingCartStore(ICartStore innerStore, ILogger<RetryingCartStore> logger, RedisRetryPolicySettings settings)
    {
        _innerStore = innerStore ?? throw new ArgumentNullException(nameof(innerStore));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        
        _retryPipeline = new ResiliencePipelineBuilder()
            .AddRetry(new RetryStrategyOptions
            {
                MaxRetryAttempts = settings.MaxRetryAttempts,
                Delay = TimeSpan.FromMilliseconds(settings.InitialBackoffMs),
                MaxDelay = TimeSpan.FromMilliseconds(settings.MaxBackoffMs),
                BackoffType = DelayBackoffType.Exponential,
                UseJitter = true,
                ShouldHandle = args => new ValueTask<bool>(IsTransientRedisError(args.Outcome.Exception))
            })
            .Build();
    }

    private bool IsTransientRedisError(Exception ex)
    {
        if (ex == null) return false;
        
        // Handle wrapped exceptions
        var baseException = ex.GetBaseException();
        
        return baseException switch
        {
            RedisConnectionException connEx => connEx.FailureType is RedisConnectionFailureType.UnableToConnect
                or RedisConnectionFailureType.ConnectionDisposed
                or RedisConnectionFailureType.Timeout
                or RedisConnectionFailureType.SocketFailure
                or RedisConnectionFailureType.UnableToResolvePhysicalConnection,
            RedisServerException serverEx => serverEx.Message.Contains("BUSY", StringComparison.OrdinalIgnoreCase),
            System.Net.Sockets.SocketException => true,
            TimeoutException => true,
            _ => false
        };
    }

    public void Initialize()
    {
        _innerStore.Initialize();
    }

    public Task AddItemAsync(string userId, string productId, int quantity)
    {
        return _retryPipeline.ExecuteAsync(async (state, ct) =>
        {
            try
            {
                await state._innerStore.AddItemAsync(state.userId, state.productId, state.quantity);
            }
            catch (Exception ex)
            {
                var context = RetryContext.GetCurrent(ct);
                if (context != null && context.AttemptNumber > 0)
                {
                    _logger.LogWarning(ex, 
                        "Redis operation {Operation} failed on attempt {Attempt}/{MaxAttempts} for cart {CartId}, retrying in {RetryDelayMs}ms",
                        nameof(AddItemAsync), context.AttemptNumber, context.MaxRetryAttempts, state.userId, context.RetryDelay.TotalMilliseconds);
                }
                throw;
            }
            return Task.CompletedTask;
        }, ( _innerStore, userId, productId, quantity ), CancellationToken.None);
    }

    public Task EmptyCartAsync(string userId)
    {
        return _retryPipeline.ExecuteAsync(async (state, ct) =>
        {
            try
            {
                await state._innerStore.EmptyCartAsync(state.userId);
            }
            catch (Exception ex)
            {
                var context = RetryContext.GetCurrent(ct);
                if (context != null && context.AttemptNumber > 0)
                {
                    _logger.LogWarning(ex, 
                        "Redis operation {Operation} failed on attempt {Attempt}/{MaxAttempts} for cart {CartId}, retrying in {RetryDelayMs}ms",
                        nameof(EmptyCartAsync), context.AttemptNumber, context.MaxRetryAttempts, state.userId, context.RetryDelay.TotalMilliseconds);
                }
                throw;
            }
            return Task.CompletedTask;
        }, ( _innerStore, userId ), CancellationToken.None);
    }

    public Task<Cart> GetCartAsync(string userId)
    {
        return _retryPipeline.ExecuteAsync(async (state, ct) =>
        {
            try
            {
                return await state._innerStore.GetCartAsync(state.userId);
            }
            catch (Exception ex)
            {
                var context = RetryContext.GetCurrent(ct);
                if (context != null && context.AttemptNumber > 0)
                {
                    _logger.LogWarning(ex, 
                        "Redis operation {Operation} failed on attempt {Attempt}/{MaxAttempts} for cart {CartId}, retrying in {RetryDelayMs}ms",
                        nameof(GetCartAsync), context.AttemptNumber, context.MaxRetryAttempts, state.userId, context.RetryDelay.TotalMilliseconds);
                }
                throw;
            }
        }, ( _innerStore, userId ), CancellationToken.None);
    }

    public bool Ping()
    {
        return _retryPipeline.Execute((state, ct) =>
        {
            try
            {
                return state._innerStore.Ping();
            }
            catch (Exception ex)
            {
                var context = RetryContext.GetCurrent(ct);
                if (context != null && context.AttemptNumber > 0)
                {
                    _logger.LogWarning(ex, 
                        "Redis operation {Operation} failed on attempt {Attempt}/{MaxAttempts}, retrying in {RetryDelayMs}ms",
                        nameof(Ping), context.AttemptNumber, context.MaxRetryAttempts, context.RetryDelay.TotalMilliseconds);
                }
                throw;
            }
        }, ( _innerStore ), CancellationToken.None);
    }

    public Task FlushAsync(CancellationToken cancellationToken = default)
    {
        return _innerStore.FlushAsync(cancellationToken);
    }
}
