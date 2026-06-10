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

namespace cart.services;

public class CartService : Oteldemo.CartService.CartServiceBase
{
    private static readonly Empty Empty = new();
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
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.SetTag("demo.product.id", request.ProductId);
        activity?.SetTag("demo.product.quantity", request.Quantity);

        // Validate inputs
        if (string.IsNullOrWhiteSpace(request.UserId))
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID cannot be empty"));
        }
        if (string.IsNullOrWhiteSpace(request.ProductId))
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "Product ID cannot be empty"));
        }
        if (request.Quantity < 1)
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "Quantity must be greater than 0"));
        }
        if (request.Quantity > 100)
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "Quantity must not exceed maximum allowed value of 100"));
        }

        try
        {
            var cart = await _cartStore.AddItemAsync(request.UserId, request.ProductId, request.Quantity);
            return cart;
        }
        catch (RpcException ex)
        {
            activity?.AddException(ex);
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    [EnableRateLimiting("GetCartPolicy")]
    public override async Task<Cart> GetCart(GetCartRequest request, ServerCallContext context)
    {
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.AddEvent(new("Fetch cart"));

        // Validate inputs
        if (string.IsNullOrWhiteSpace(request.UserId))
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID cannot be empty"));
        }
        try
        {
            var cart = await _cartStore.GetCartAsync(request.UserId);
            var totalCart = 0;
            foreach (var item in cart.Items)
            {
                totalCart += item.Quantity;
            }
            activity?.SetTag("demo.cart.items.count", totalCart);

            return cart;
        }
        catch (RpcException ex)
        {
            activity?.AddException(ex);
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    [EnableRateLimiting("RemoveItemPolicy")]
    public override async Task<Empty> RemoveItem(RemoveItemRequest request, ServerCallContext context)
    {
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.SetTag("demo.product.id", request.ProductId);

        // Validate inputs
        if (string.IsNullOrWhiteSpace(request.UserId))
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID cannot be empty"));
        }
        if (string.IsNullOrWhiteSpace(request.ProductId))
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "Product ID cannot be empty"));
        }
        try
        {
            await _cartStore.RemoveItemAsync(request.UserId, request.ProductId);
            return Empty;
        }
        catch (RpcException ex)
        {
            activity?.AddException(ex);
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    public override async Task<Empty> EmptyCart(EmptyCartRequest request, ServerCallContext context)
    {
        var activity = Activity.Current;
        activity?.SetTag("user.id", request.UserId);
        activity?.AddEvent(new("Empty cart"));

        // Validate inputs
        if (string.IsNullOrWhiteSpace(request.UserId))
        {
            throw new RpcException(new Status(StatusCode.InvalidArgument, "User ID cannot be empty"));
        }
        try
        {
            if (await _featureFlagHelper.GetBooleanValueAsync("cartFailure", false))
            {
                await _badCartStore.EmptyCartAsync(request.UserId);
            }
            else
            {
                await _cartStore.EmptyCartAsync(request.UserId);
            }
        }
        catch (RpcException ex)
        {
            Activity.Current?.AddException(ex);
            Activity.Current?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }

        return Empty;
    }
}
