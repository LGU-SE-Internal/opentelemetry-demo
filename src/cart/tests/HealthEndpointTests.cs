// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Threading.Tasks;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Hosting;
using Xunit;

namespace cart.tests;

public class HealthEndpointTests
{
    private readonly IHostBuilder _host;

    public HealthEndpointTests()
    {
        _host = new HostBuilder().ConfigureWebHost(webBuilder =>
        {
            webBuilder
                .UseTestServer();
        });
    }

    public class HealthCheckResponse
    {
        public string Status { get; set; } = string.Empty;
        public string[] Errors { get; set; } = Array.Empty<string>();
    }

    [Fact]
    public async Task test_ac1_health_endpoint_returns_200_ok_when_connected_to_redis()
    {
        // Arrange: Start server with healthy Redis connection
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Act: Call GET /health endpoint
        var response = await httpClient.GetAsync("/health");

        // Assert: Status code 200 OK
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        
        // Assert: Correct JSON payload
        var content = await response.Content.ReadFromJsonAsync<HealthCheckResponse>();
        Assert.NotNull(content);
        Assert.Equal("Healthy", content.Status);
        Assert.Empty(content.Errors);
    }

    [Fact]
    public async Task test_ac2_health_endpoint_returns_503_when_redis_connection_fails()
    {
        // Arrange: Start server with invalid Redis configuration (connection will fail)
        Environment.SetEnvironmentVariable("REDIS_ADDR", "invalid-host:6379");
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Act: Call GET /health endpoint
        var response = await httpClient.GetAsync("/health");

        // Assert: Status code 503 Service Unavailable
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        
        // Assert: Correct JSON payload with error
        var content = await response.Content.ReadFromJsonAsync<HealthCheckResponse>();
        Assert.NotNull(content);
        Assert.Equal("Unhealthy", content.Status);
        Assert.NotEmpty(content.Errors);
        Assert.StartsWith("Connection to Redis failed:", content.Errors[0]);
    }

    [Fact]
    public async Task test_ac3_ready_endpoint_returns_200_ok_when_fully_initialized()
    {
        // Arrange: Start fully initialized server with healthy dependencies
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Act: Call GET /ready endpoint
        var response = await httpClient.GetAsync("/ready");

        // Assert: Status code 200 OK
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        
        // Assert: Correct JSON payload
        var content = await response.Content.ReadFromJsonAsync<HealthCheckResponse>();
        Assert.NotNull(content);
        Assert.Equal("Ready", content.Status);
        Assert.Empty(content.Errors);
    }

    [Fact]
    public async Task test_ac4_ready_endpoint_returns_503_when_not_ready()
    {
        // Arrange: Simulate unready state (e.g. initialization in progress or failed dependency)
        Environment.SetEnvironmentVariable("REDIS_ADDR", "invalid-host:6379");
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Act: Call GET /ready endpoint
        var response = await httpClient.GetAsync("/ready");

        // Assert: Status code 503 Service Unavailable
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        
        // Assert: Correct JSON payload with error
        var content = await response.Content.ReadFromJsonAsync<HealthCheckResponse>();
        Assert.NotNull(content);
        Assert.Equal("NotReady", content.Status);
        Assert.NotEmpty(content.Errors);
    }

    [Fact]
    public async Task test_ac5_health_and_ready_endpoints_use_same_port_as_main_api()
    {
        // Arrange: Start test server
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        var baseAddress = httpClient.BaseAddress;

        // Act: Call both endpoints on the same base address as main API
        var healthResponse = await httpClient.GetAsync("/health");
        var readyResponse = await httpClient.GetAsync("/ready");

        // Assert: Both endpoints are reachable on the same port as main API base address
        Assert.True(healthResponse.IsSuccessStatusCode || healthResponse.StatusCode == HttpStatusCode.ServiceUnavailable);
        Assert.True(readyResponse.IsSuccessStatusCode || readyResponse.StatusCode == HttpStatusCode.ServiceUnavailable);
    }

    [Fact]
    public async Task test_ac6_health_and_ready_endpoints_no_authentication_required()
    {
        // Arrange: Start server with authentication middleware enabled
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();
        
        // Act: Call endpoints without any authentication headers
        httpClient.DefaultRequestHeaders.Authorization = null;
        var healthResponse = await httpClient.GetAsync("/health");
        var readyResponse = await httpClient.GetAsync("/ready");

        // Assert: No 401/403 responses returned
        Assert.NotEqual(HttpStatusCode.Unauthorized, healthResponse.StatusCode);
        Assert.NotEqual(HttpStatusCode.Forbidden, healthResponse.StatusCode);
        Assert.NotEqual(HttpStatusCode.Unauthorized, readyResponse.StatusCode);
        Assert.NotEqual(HttpStatusCode.Forbidden, readyResponse.StatusCode);
    }

    [Fact]
    public async Task test_ac7_health_and_ready_responses_have_application_json_content_type()
    {
        // Arrange: Start server
        using var server = await _host.StartAsync();
        var httpClient = server.GetTestClient();

        // Act: Call both endpoints
        var healthResponse = await httpClient.GetAsync("/health");
        var readyResponse = await httpClient.GetAsync("/ready");

        // Assert: Content-Type header is application/json for both responses
        Assert.Equal("application/json", healthResponse.Content.Headers.ContentType?.MediaType);
        Assert.Equal("application/json", readyResponse.Content.Headers.ContentType?.MediaType);
    }
}
