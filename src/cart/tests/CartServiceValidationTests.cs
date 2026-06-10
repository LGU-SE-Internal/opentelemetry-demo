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
using System.Net.Http.Json;

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
    public async Task test_ac1_get_cart_empty_user_id_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with empty user ID
        var request = new GetCartRequest { UserId = "" };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.GetCartAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("User ID must not be empty", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac2_empty_cart_empty_user_id_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with empty user ID
        var request = new EmptyCartRequest { UserId = "" };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.EmptyCartAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("User ID must not be empty", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac3_add_item_empty_user_id_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with empty user ID
        var request = new AddItemRequest
        {
            UserId = "",
            Item = new CartItem { ProductId = "test-prod-1", Quantity = 1 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("User ID must not be empty", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac4_add_item_empty_product_id_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with empty product ID
        var request = new AddItemRequest
        {
            UserId = "test-user-1",
            Item = new CartItem { ProductId = "", Quantity = 1 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("Product ID must not be empty", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac5_add_item_zero_quantity_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with zero quantity
        var request = new AddItemRequest
        {
            UserId = "test-user-1",
            Item = new CartItem { ProductId = "test-prod-1", Quantity = 0 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("Quantity must be a positive integer", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac6_add_item_negative_quantity_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with negative quantity
        var request = new AddItemRequest
        {
            UserId = "test-user-1",
            Item = new CartItem { ProductId = "test-prod-1", Quantity = -1 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("Quantity must be a positive integer", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac6_remove_item_empty_user_id_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with empty user ID
        var request = new RemoveItemRequest
        {
            UserId = "",
            ProductId = "test-prod-1"
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.RemoveItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("User ID must not be empty", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac7_remove_item_empty_product_id_returns_invalid_argument()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with empty product ID
        var request = new RemoveItemRequest
        {
            UserId = "test-user-1",
            ProductId = ""
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.RemoveItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("Product ID must not be empty", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac8_multiple_invalid_fields_returns_all_errors()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        // Test with multiple invalid fields
        var request = new AddItemRequest
        {
            UserId = "",
            Item = new CartItem { ProductId = "", Quantity = 0 }
        };

        var exception = await Assert.ThrowsAsync<RpcException>(() => cartClient.AddItemAsync(request));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Contains("User ID must not be empty", exception.Status.Detail);
        Assert.Contains("Product ID must not be empty", exception.Status.Detail);
        Assert.Contains("Quantity must be a positive integer", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac9_valid_requests_process_normally()
    {
        // Setup test server and client
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Create GRPC client
        var channel = GrpcChannel.ForAddress(httpClient.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = httpClient
        });
        var cartClient = new CartServiceClient(channel);

        string userId = Guid.NewGuid().ToString();
        string productId = "test-prod-1";
        int quantity = 2;

        // Valid AddItem request
        var addRequest = new AddItemRequest
        {
            UserId = userId,
            Item = new CartItem { ProductId = productId, Quantity = quantity }
        };
        await cartClient.AddItemAsync(addRequest);

        // Valid GetCart request
        var getRequest = new GetCartRequest { UserId = userId };
        var cart = await cartClient.GetCartAsync(getRequest);
        Assert.NotNull(cart);
        Assert.Equal(userId, cart.UserId);
        Assert.Single(cart.Items);
        Assert.Equal(productId, cart.Items[0].ProductId);
        Assert.Equal(quantity, cart.Items[0].Quantity);

        // Valid RemoveItem request
        var removeRequest = new RemoveItemRequest { UserId = userId, ProductId = productId };
        await cartClient.RemoveItemAsync(removeRequest);

        cart = await cartClient.GetCartAsync(getRequest);
        Assert.Empty(cart.Items);

        // Valid EmptyCart request
        await cartClient.AddItemAsync(addRequest);
        var emptyRequest = new EmptyCartRequest { UserId = userId };
        await cartClient.EmptyCartAsync(emptyRequest);

        cart = await cartClient.GetCartAsync(getRequest);
        Assert.Empty(cart.Items);
    }
}
