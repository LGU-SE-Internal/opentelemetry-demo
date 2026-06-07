using System.Net;
using System.Net.Http.Headers;
using Grpc.Core;
using Grpc.Net.Client;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;
using cart.v1;
using System.Collections.Generic;
using System.Threading.Tasks;
using System;
using System.Linq;
using System.Text.Json;

namespace CartService.Tests;

public class RateLimitTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private const string DefaultRateLimitEnvVar = "CART_SERVICE_RATE_LIMIT_REQUESTS_PER_MINUTE";
    private const int DefaultRateLimit = 100;

    public RateLimitTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
    }

    [Fact]
    public async Task test_ac1_rate_limit_blocks_excess_requests_per_client_ip()
    {
        // Arrange: Use same client IP for all requests
        var client = _factory.CreateClient();
        client.DefaultRequestHeaders.Add("X-Forwarded-For", "192.168.1.100");

        // Act: Send DefaultRateLimit + 1 requests to any cart endpoint
        var responses = new List<HttpResponseMessage>();
        for (int i = 0; i < DefaultRateLimit + 1; i++)
        {
            responses.Add(await client.GetAsync("/api/cart"));
        }

        // Assert: First DefaultRateLimit requests are allowed, last is blocked
        var allowedResponses = responses.Take(DefaultRateLimit).All(r => r.StatusCode != HttpStatusCode.TooManyRequests);
        var blockedResponse = responses.Last().StatusCode == HttpStatusCode.TooManyRequests;

        Assert.True(allowedResponses, "First 100 requests should be allowed");
        Assert.True(blockedResponse, "101st request should be rate limited");
    }

    [Fact]
    public async Task test_ac2_custom_rate_limit_from_environment_variable()
    {
        // Arrange: Set custom rate limit in environment variables
        const int customRateLimit = 200;
        Environment.SetEnvironmentVariable(DefaultRateLimitEnvVar, customRateLimit.ToString());
        var customFactory = _factory.WithWebHostBuilder(builder =>
        {
            // Reconfigure services with new environment variable
        });
        var client = customFactory.CreateClient();
        client.DefaultRequestHeaders.Add("X-Forwarded-For", "192.168.1.101");

        // Act: Send customRateLimit + 1 requests
        var responses = new List<HttpResponseMessage>();
        for (int i = 0; i < customRateLimit + 1; i++)
        {
            responses.Add(await client.GetAsync("/api/cart"));
        }

        // Assert: First customRateLimit requests are allowed, last is blocked
        var allowedResponses = responses.Take(customRateLimit).All(r => r.StatusCode != HttpStatusCode.TooManyRequests);
        var blockedResponse = responses.Last().StatusCode == HttpStatusCode.TooManyRequests;

        Assert.True(allowedResponses, $"First {customRateLimit} requests should be allowed with custom limit");
        Assert.True(blockedResponse, $"{customRateLimit + 1}th request should be rate limited with custom limit");
        
        // Cleanup
        Environment.SetEnvironmentVariable(DefaultRateLimitEnvVar, null);
    }

    [Fact]
    public async Task test_ac3_http_rate_limit_response_returns_429_with_retry_after()
    {
        // Arrange: Exceed rate limit for a client IP
        var client = _factory.CreateClient();
        client.DefaultRequestHeaders.Add("X-Forwarded-For", "192.168.1.102");
        for (int i = 0; i < DefaultRateLimit; i++)
        {
            await client.GetAsync("/api/cart");
        }

        // Act: Send the rate limited request
        var response = await client.GetAsync("/api/cart");

        // Assert: Correct status code, header, and body
        Assert.Equal(HttpStatusCode.TooManyRequests, response.StatusCode);
        Assert.True(response.Headers.TryGetValues("Retry-After", out var retryAfterValues));
        Assert.True(int.TryParse(retryAfterValues.First(), out var retryAfterSeconds));
        Assert.InRange(retryAfterSeconds, 1, 60);

        var responseBody = await JsonSerializer.DeserializeAsync<RateLimitErrorResponse>(
            await response.Content.ReadAsStreamAsync(), 
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true }
        );
        Assert.NotNull(responseBody);
        Assert.Equal("Rate limit exceeded", responseBody.Error);
        Assert.Equal(retryAfterSeconds, responseBody.RetryAfter);
    }

    [Fact]
    public async Task test_ac4_grpc_rate_limit_response_returns_resource_exhausted_with_retry_after()
    {
        // Arrange: Exceed rate limit for a client IP
        var client = _factory.CreateClient();
        client.DefaultRequestHeaders.Add("X-Forwarded-For", "192.168.1.103");
        for (int i = 0; i < DefaultRateLimit; i++)
        {
            await client.GetAsync("/api/cart");
        }

        // Act: Send gRPC request to get cart
        var channel = GrpcChannel.ForAddress(_factory.Server.BaseAddress, new GrpcChannelOptions
        {
            HttpClient = client
        });
        var grpcClient = new CartService.CartServiceClient(channel);
        var exception = await Assert.ThrowsAsync<RpcException>(async () => 
            await grpcClient.GetCartAsync(new GetCartRequest { UserId = "test-user" }));

        // Assert: Correct status code, trailer, and message
        Assert.Equal(StatusCode.ResourceExhausted, exception.StatusCode);
        Assert.True(exception.Trailers.TryGetValue("retry-after", out var retryAfterValue));
        Assert.True(int.TryParse(retryAfterValue, out var retryAfterSeconds));
        Assert.InRange(retryAfterSeconds, 1, 60);
        Assert.Contains($"Rate limit exceeded, retry after {retryAfterSeconds} seconds", exception.Status.Detail);
    }

    [Fact]
    public async Task test_ac5_rate_limit_is_isolated_per_client_ip()
    {
        // Arrange: Exceed rate limit for first IP
        var client1 = _factory.CreateClient();
        client1.DefaultRequestHeaders.Add("X-Forwarded-For", "192.168.1.104");
        for (int i = 0; i < DefaultRateLimit; i++)
        {
            await client1.GetAsync("/api/cart");
        }

        // Act 1: Verify client1 is blocked
        var client1Response = await client1.GetAsync("/api/cart");
        Assert.Equal(HttpStatusCode.TooManyRequests, client1Response.StatusCode);

        // Act 2: Verify client2 (different IP) is not blocked
        var client2 = _factory.CreateClient();
        client2.DefaultRequestHeaders.Add("X-Forwarded-For", "192.168.1.105");
        var client2Response = await client2.GetAsync("/api/cart");
        Assert.NotEqual(HttpStatusCode.TooManyRequests, client2Response.StatusCode);
    }

    [Fact]
    public async Task test_ac6_rate_limit_resets_after_window_expires()
    {
        // Arrange: Exceed rate limit for a client IP
        var client = _factory.CreateClient();
        client.DefaultRequestHeaders.Add("X-Forwarded-For", "192.168.1.106");
        for (int i = 0; i < DefaultRateLimit; i++)
        {
            await client.GetAsync("/api/cart");
        }
        var blockedResponse = await client.GetAsync("/api/cart");
        Assert.Equal(HttpStatusCode.TooManyRequests, blockedResponse.StatusCode);
        blockedResponse.Headers.TryGetValues("Retry-After", out var retryAfterValues);
        var retryAfterSeconds = int.Parse(retryAfterValues.First());

        // Act: Wait for rate limit window to reset
        await Task.Delay(TimeSpan.FromSeconds(retryAfterSeconds + 1));

        // Assert: New request is allowed
        var newResponse = await client.GetAsync("/api/cart");
        Assert.NotEqual(HttpStatusCode.TooManyRequests, newResponse.StatusCode);
    }

    private class RateLimitErrorResponse
    {
        public string Error { get; set; } = string.Empty;
        public int RetryAfter { get; set; }
    }
}
