// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System.Diagnostics;
using System.Threading.Tasks;
using System;
using Grpc.Core;
using cart.cartstore;
using OpenFeature;
using Oteldemo;
using Microsoft.AspNetCore.RateLimiting;
using OpenTelemetry.Metrics;

namespace cart.services;

public class CartService : Oteldemo.CartService.CartServiceBase
{
    private static readonly Empty Empty = new();
    private static readonly Meter CartMeter = new("OpenTelemetry.Demo.Cart", "1.0.0");
    private static readonly Counter<long> _cartRequestsTotal = CartMeter.CreateCounter<long>(
        "app_cart_requests_total",
        description: "Total number of requests received for each cart service endpoint",
        unit: "{count}");
    private static readonly Counter<long> _cartRequestsFailedTotal = CartMeter.CreateCounter<long>(
        "app_cart_requests_failed_total",
        description: "Total number of failed requests for each cart service endpoint, categorized by error type",
        unit: "{count}");
    private static readonly Histogram<double> _cartRequestDuration = CartMeter.CreateHistogram<double>(
        "app_cart_request_duration_seconds",
        description: "Distribution of request processing durations for each cart service endpoint",
        unit: "s",
        advice: new InstrumentAdvice<double> { ExplicitBucketBoundaries = new double[] { 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 } });
    
    private readonly Random random = new Random();
    private readonly ICartStore _badCartStore;
    private readonly ICartStore _cartStore;
    private readonly IFeatureClient _featureFlagHelper;

    public CartService(ICartStore cartStore, ICartStore badCartStore, IFeatureClient featureFlagService)
    {
        _badCartStore = badCartStore;
        _cartStore = cartStore;
        _featureFlagHelper = featureFlagService;
    }

    [EnableRateLimiting("AddItemPolicy")]
    public override async Task<Cart> AddItem(AddItemRequest request, ServerCallContext context)
    {
        const string endpointName = "AddItem";
        var stopwatch = Stopwatch.StartNew();
        _cartRequestsTotal.Add(1, new KeyValuePair<string, object?>("endpoint", endpointName));
        
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.SetTag("demo.product.id", request.ProductId);
        activity?.SetTag("demo.product.quantity", request.Quantity);

        try
        {
            // Validate inputs
            if (string.IsNullOrWhiteSpace(request.UserId))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID must not be empty or contain only whitespace"));
            }
            if (string.IsNullOrWhiteSpace(request.ProductId))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "Product ID must not be empty or contain only whitespace"));
            }
            if (request.Quantity < 1)
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "Quantity must be a positive integer greater than 0"));
            }
            if (request.Quantity > 100)
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "Quantity must not exceed maximum allowed value of 100"));
            }

            var cart = await _cartStore.AddItemAsync(request.UserId, request.ProductId, request.Quantity);
            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            return cart;
        }
        catch (RpcException ex)
        {
            var errorType = ex.StatusCode switch
            {
                StatusCode.InvalidArgument => "validation_error",
                StatusCode.NotFound => "storage_error",
                _ => "internal_error"
            };
            _cartRequestsFailedTotal.Add(1, 
                new KeyValuePair<string, object?>("endpoint", endpointName),
                new KeyValuePair<string, object?>("error_type", errorType));
            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            
            activity?.AddException(ex);
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    [EnableRateLimiting("GetCartPolicy")]
    public override async Task<Cart> GetCart(GetCartRequest request, ServerCallContext context)
    {
        const string endpointName = "GetCart";
        var stopwatch = Stopwatch.StartNew();
        _cartRequestsTotal.Add(1, new KeyValuePair<string, object?>("endpoint", endpointName));
        
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.AddEvent(new("Fetch cart"));

        try
        {
            // Validate inputs
            if (string.IsNullOrWhiteSpace(request.UserId))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID must not be empty or contain only whitespace"));
            }

            var cart = await _cartStore.GetCartAsync(request.UserId);
            var totalCart = 0;
            foreach (var item in cart.Items)
            {
                totalCart += item.Quantity;
            }
            activity?.SetTag("demo.cart.items.count", totalCart);

            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            return cart;
        }
        catch (RpcException ex)
        {
            var errorType = ex.StatusCode switch
            {
                StatusCode.InvalidArgument => "validation_error",
                StatusCode.NotFound => "storage_error",
                _ => "internal_error"
            };
            _cartRequestsFailedTotal.Add(1, 
                new KeyValuePair<string, object?>("endpoint", endpointName),
                new KeyValuePair<string, object?>("error_type", errorType));
            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            
            activity?.AddException(ex);
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    [EnableRateLimiting("RemoveItemPolicy")]
    public override async Task<Empty> RemoveItem(RemoveItemRequest request, ServerCallContext context)
    {
        const string endpointName = "RemoveItem";
        var stopwatch = Stopwatch.StartNew();
        _cartRequestsTotal.Add(1, new KeyValuePair<string, object?>("endpoint", endpointName));
        
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.SetTag("demo.product.id", request.ProductId);

        try
        {
            // Validate inputs
            if (string.IsNullOrWhiteSpace(request.UserId))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID must not be empty or contain only whitespace"));
            }
            if (string.IsNullOrWhiteSpace(request.ProductId))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "Product ID must not be empty or contain only whitespace"));
            }

            await _cartStore.RemoveItemAsync(request.UserId, request.ProductId);
            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            return Empty;
        }
        catch (RpcException ex)
        {
            var errorType = ex.StatusCode switch
            {
                StatusCode.InvalidArgument => "validation_error",
                StatusCode.NotFound => "storage_error",
                _ => "internal_error"
            };
            _cartRequestsFailedTotal.Add(1, 
                new KeyValuePair<string, object?>("endpoint", endpointName),
                new KeyValuePair<string, object?>("error_type", errorType));
            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            
            activity?.AddException(ex);
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    public override async Task<Empty> EmptyCart(EmptyCartRequest request, ServerCallContext context)
    {
        const string endpointName = "EmptyCart";
        var stopwatch = Stopwatch.StartNew();
        _cartRequestsTotal.Add(1, new KeyValuePair<string, object?>("endpoint", endpointName));
        
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.AddEvent(new("Empty cart"));

        try
        {
            // Validate inputs
            if (string.IsNullOrWhiteSpace(request.UserId))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID must not be empty or contain only whitespace"));
            }

            if (await _featureFlagHelper.GetBooleanValueAsync("cartFailure", false))
            {
                await _badCartStore.EmptyCartAsync(request.UserId);
            }
            else
            {
                await _cartStore.EmptyCartAsync(request.UserId);
            }
            
            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            return Empty;
        }
        catch (RpcException ex)
        {
            var errorType = ex.StatusCode switch
            {
                StatusCode.InvalidArgument => "validation_error",
                StatusCode.NotFound => "storage_error",
                _ => "internal_error"
            };
            _cartRequestsFailedTotal.Add(1, 
                new KeyValuePair<string, object?>("endpoint", endpointName),
                new KeyValuePair<string, object?>("error_type", errorType));
            _cartRequestDuration.Record(stopwatch.Elapsed.TotalSeconds, new KeyValuePair<string, object?>("endpoint", endpointName));
            
            Activity.Current?.AddException(ex);
            Activity.Current?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }
}
