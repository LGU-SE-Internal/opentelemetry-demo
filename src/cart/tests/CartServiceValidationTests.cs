// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Threading.Tasks;
using Grpc.Core;
using Grpc.Net.Client;
using Oteldemo;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Hosting;
using Xunit;
using static Oteldemo.CartService;
using System.Net;

namespace cart.tests;

public class CartServiceValidationTests
{
    private readonly IHostBuilder _host;

    public CartServiceValidationTests()
    {
        _host = new HostBuilder().ConfigureWebHost(webBuilder =>
        {
            webBuilder
                .UseTestServer();
        });
    }

    [Fact]
    public async Task test_ac1_add_item_empty_user_id_returns_invalid_argument()
    {
        // AC-1: AddItem with empty user_id returns INVALID_ARGUMENT, no data written
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        var request = new AddItemRequest
        {
            UserId = "",
            Item = new CartItem { ProductId = "test-prod-1", Quantity = 1 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("UserId is required", exception.Status.Detail, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task test_ac2_add_item_empty_product_id_returns_invalid_argument()
    {
        // AC-2: AddItem with empty product_id returns INVALID_ARGUMENT, no data written
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        var request = new AddItemRequest
        {
            UserId = "test-user-1",
            Item = new CartItem { ProductId = "", Quantity = 1 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("ProductId is required", exception.Status.Detail, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task test_ac3_add_item_negative_quantity_returns_invalid_argument()
    {
        // AC-3: AddItem with quantity <= 0 returns INVALID_ARGUMENT, no data written
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test negative quantity
        var request = new AddItemRequest
        {
            UserId = "test-user-1",
            Item = new CartItem { ProductId = "test-prod-1", Quantity = -1 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("Quantity must be a positive integer greater than 0", exception.Status.Detail, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task test_ac3_add_item_zero_quantity_returns_invalid_argument()
    {
        // AC-3: AddItem with quantity <= 0 returns INVALID_ARGUMENT, no data written (zero case)
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test zero quantity
        var request = new AddItemRequest
        {
            UserId = "test-user-1",
            Item = new CartItem { ProductId = "test-prod-1", Quantity = 0 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("Quantity must be a positive integer greater than 0", exception.Status.Detail, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task test_ac4_get_cart_empty_user_id_returns_invalid_argument()
    {
        // AC-4: GetCart with empty user_id returns INVALID_ARGUMENT, no datastore query
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        var request = new GetCartRequest { UserId = "" };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.GetCartAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("UserId is required", exception.Status.Detail, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task test_ac5_empty_cart_empty_user_id_returns_invalid_argument()
    {
        // AC-5: EmptyCart with empty user_id returns INVALID_ARGUMENT, no data deleted
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        var request = new EmptyCartRequest { UserId = "" };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.EmptyCartAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("UserId is required", exception.Status.Detail, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task test_ac6_valid_add_item_request_processed_successfully()
    {
        // AC-6: Valid AddItem request succeeds, item added to cart
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        string userId = Guid.NewGuid().ToString();
        string productId = "test-prod-valid-ac6";
        int quantity = 2;

        var addRequest = new AddItemRequest
        {
            UserId = userId,
            Item = new CartItem { ProductId = productId, Quantity = quantity }
        };
        var addResponse = await cartClient.AddItemAsync(addRequest);
        Assert.NotNull(addResponse);

        // Verify item was added
        var getRequest = new GetCartRequest { UserId = userId };
        var cart = await cartClient.GetCartAsync(getRequest);
        Assert.NotNull(cart);
        Assert.Equal(userId, cart.UserId);
        Assert.Single(cart.Items);
        Assert.Equal(productId, cart.Items[0].ProductId);
        Assert.Equal(quantity, cart.Items[0].Quantity);
    }

    [Fact]
    public async Task test_ac7_valid_get_cart_request_processed_successfully()
    {
        // AC-7: Valid GetCart request succeeds, returns cart data
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        string userId = Guid.NewGuid().ToString();
        string productId = "test-prod-valid-ac7";
        int quantity = 3;

        // Pre-populate cart
        await cartClient.AddItemAsync(new AddItemRequest
        {
            UserId = userId,
            Item = new CartItem { ProductId = productId, Quantity = quantity }
        });

        // Test GetCart
        var getRequest = new GetCartRequest { UserId = userId };
        var cart = await cartClient.GetCartAsync(getRequest);
        Assert.NotNull(cart);
        Assert.Equal(userId, cart.UserId);
        Assert.Single(cart.Items);
        Assert.Equal(productId, cart.Items[0].ProductId);
        Assert.Equal(quantity, cart.Items[0].Quantity);
    }

    [Fact]
    public async Task test_ac8_valid_empty_cart_request_processed_successfully()
    {
        // AC-8: Valid EmptyCart request succeeds, cart is deleted
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        string userId = Guid.NewGuid().ToString();
        string productId = "test-prod-valid-ac8";
        int quantity = 2;

        // Pre-populate cart
        await cartClient.AddItemAsync(new AddItemRequest
        {
            UserId = userId,
            Item = new CartItem { ProductId = productId, Quantity = quantity }
        });

        // Verify cart has items
        var getRequest = new GetCartRequest { UserId = userId };
        var preEmptyCart = await cartClient.GetCartAsync(getRequest);
        Assert.NotEmpty(preEmptyCart.Items);

        // Test EmptyCart
        var emptyRequest = new EmptyCartRequest { UserId = userId };
        var emptyResponse = await cartClient.EmptyCartAsync(emptyRequest);
        Assert.NotNull(emptyResponse);

        // Verify cart is empty
        var postEmptyCart = await cartClient.GetCartAsync(getRequest);
        Assert.Empty(postEmptyCart.Items);
    }
}
