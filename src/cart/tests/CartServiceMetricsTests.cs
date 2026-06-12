using System.Net;
using System.Net.Http.Headers;
using System.Diagnostics;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;
using OpenTelemetry;
using OpenTelemetry.Metrics;
using cart;

namespace cart.Tests;

public class CartServiceMetricsTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly HttpClient _client;

    public CartServiceMetricsTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task test_ac1_metrics_endpoint_returns_valid_prometheus_format()
    {
        // Arrange
        var request = new HttpRequestMessage(HttpMethod.Get, "/metrics");
        
        // Act
        var response = await _client.SendAsync(request);
        
        // Assert
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("text/plain; version=0.0.4; charset=utf-8", response.Content.Headers.ContentType?.ToString());
        var content = await response.Content.ReadAsStringAsync();
        Assert.Contains("# HELP", content);
        Assert.Contains("# TYPE", content);
    }

    [Fact]
    public async Task test_ac2_additem_request_increments_total_counter()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_total", "endpoint", "AddItem");

        // Act: Send valid AddItem request
        var addItemRequest = new HttpRequestMessage(HttpMethod.Post, "/cart/123/items")
        {
            Content = JsonContent.Create(new { product_id = "product1", quantity = 1 })
        };
        var addItemResponse = await _client.SendAsync(addItemRequest);
        addItemResponse.EnsureSuccessStatusCode();

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_total", "endpoint", "AddItem");
        Assert.Equal(initialValue + 1, finalValue);
    }

    [Fact]
    public async Task test_ac3_getcart_request_increments_total_counter()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_total", "endpoint", "GetCart");

        // Act: Send valid GetCart request
        var getCartResponse = await _client.GetAsync("/cart/123");
        getCartResponse.EnsureSuccessStatusCode();

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_total", "endpoint", "GetCart");
        Assert.Equal(initialValue + 1, finalValue);
    }

    [Fact]
    public async Task test_ac4_removeitem_request_increments_total_counter()
    {
        // First add an item to remove
        var addItemRequest = new HttpRequestMessage(HttpMethod.Post, "/cart/123/items")
        {
            Content = JsonContent.Create(new { product_id = "product1", quantity = 1 })
        };
        await _client.SendAsync(addItemRequest);

        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_total", "endpoint", "RemoveItem");

        // Act: Send valid RemoveItem request
        var removeItemResponse = await _client.DeleteAsync("/cart/123/items/product1");
        removeItemResponse.EnsureSuccessStatusCode();

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_total", "endpoint", "RemoveItem");
        Assert.Equal(initialValue + 1, finalValue);
    }

    [Fact]
    public async Task test_ac5_emptycart_request_increments_total_counter()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_total", "endpoint", "EmptyCart");

        // Act: Send valid EmptyCart request
        var emptyCartResponse = await _client.DeleteAsync("/cart/123");
        emptyCartResponse.EnsureSuccessStatusCode();

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_total", "endpoint", "EmptyCart");
        Assert.Equal(initialValue + 1, finalValue);
    }

    [Fact]
    public async Task test_ac6_additem_failure_increments_failed_counter()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_failed_total", "endpoint", "AddItem");

        // Act: Send invalid AddItem request (missing product_id, will trigger validation error)
        var addItemRequest = new HttpRequestMessage(HttpMethod.Post, "/cart/123/items")
        {
            Content = JsonContent.Create(new { quantity = 1 })
        };
        var addItemResponse = await _client.SendAsync(addItemRequest);
        Assert.Equal(HttpStatusCode.BadRequest, addItemResponse.StatusCode);

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_failed_total", "endpoint", "AddItem");
        var errorValue = GetCounterValueWithTwoLabels(finalContent, "app_cart_requests_failed_total", "endpoint", "AddItem", "error_type", "validation_error");
        Assert.Equal(initialValue + 1, finalValue);
        Assert.Equal(initialValue + 1, errorValue);
    }

    [Fact]
    public async Task test_ac7_getcart_failure_increments_failed_counter()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_failed_total", "endpoint", "GetCart");

        // Act: Send invalid GetCart request with invalid user ID format
        var getCartResponse = await _client.GetAsync("/cart/invalid-user-id-format-!@#$%");
        Assert.Equal(HttpStatusCode.BadRequest, getCartResponse.StatusCode);

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_failed_total", "endpoint", "GetCart");
        var errorValue = GetCounterValueWithTwoLabels(finalContent, "app_cart_requests_failed_total", "endpoint", "GetCart", "error_type", "validation_error");
        Assert.Equal(initialValue + 1, finalValue);
        Assert.Equal(initialValue + 1, errorValue);
    }

    [Fact]
    public async Task test_ac8_removeitem_failure_increments_failed_counter()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_failed_total", "endpoint", "RemoveItem");

        // Act: Try to remove non-existent item
        var removeItemResponse = await _client.DeleteAsync("/cart/123/items/nonexistent-product-999");
        Assert.Equal(HttpStatusCode.NotFound, removeItemResponse.StatusCode);

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_failed_total", "endpoint", "RemoveItem");
        var errorValue = GetCounterValueWithTwoLabels(finalContent, "app_cart_requests_failed_total", "endpoint", "RemoveItem", "error_type", "storage_error");
        Assert.Equal(initialValue + 1, finalValue);
        Assert.Equal(initialValue + 1, errorValue);
    }

    [Fact]
    public async Task test_ac9_emptycart_failure_increments_failed_counter()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialValue = GetCounterValue(initialContent, "app_cart_requests_failed_total", "endpoint", "EmptyCart");

        // Act: Send invalid EmptyCart request with empty user ID
        var emptyCartResponse = await _client.DeleteAsync("/cart/ ");
        Assert.Equal(HttpStatusCode.BadRequest, emptyCartResponse.StatusCode);

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalValue = GetCounterValue(finalContent, "app_cart_requests_failed_total", "endpoint", "EmptyCart");
        var errorValue = GetCounterValueWithTwoLabels(finalContent, "app_cart_requests_failed_total", "endpoint", "EmptyCart", "error_type", "validation_error");
        Assert.Equal(initialValue + 1, finalValue);
        Assert.Equal(initialValue + 1, errorValue);
    }

    [Fact]
    public async Task test_ac10_request_duration_histogram_records_timings()
    {
        // Arrange
        var initialContent = await _client.GetStringAsync("/metrics");
        var initialCount = GetHistogramCount(initialContent, "app_cart_request_duration_seconds", "endpoint", "GetCart");

        // Act: Send multiple requests to ensure histogram is populated
        var stopwatch = Stopwatch.StartNew();
        for (int i = 0; i < 10; i++)
        {
            var response = await _client.GetAsync("/cart/123");
            response.EnsureSuccessStatusCode();
        }
        stopwatch.Stop();
        var averageDuration = stopwatch.Elapsed.TotalSeconds / 10;

        // Assert
        var finalContent = await _client.GetStringAsync("/metrics");
        var finalCount = GetHistogramCount(finalContent, "app_cart_request_duration_seconds", "endpoint", "GetCart");
        Assert.Equal(initialCount + 10, finalCount);
        
        // Check that at least one bucket has been incremented
        var bucketValues = GetHistogramBucketValues(finalContent, "app_cart_request_duration_seconds", "endpoint", "GetCart");
        Assert.True(bucketValues.Sum() >= 10);
        
        // Check that average duration falls into one of the expected buckets
        var expectedBuckets = new[] { 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 };
        var matchingBucket = expectedBuckets.First(b => b >= averageDuration);
        Assert.True(bucketValues[Array.IndexOf(expectedBuckets, matchingBucket)] > 0);
    }

    [Fact]
    public async Task test_ac11_health_endpoints_remain_functional()
    {
        // Test /health endpoint
        var healthResponse = await _client.GetAsync("/health");
        Assert.Equal(HttpStatusCode.OK, healthResponse.StatusCode);
        var healthContent = await healthResponse.Content.ReadAsStringAsync();
        Assert.False(string.IsNullOrEmpty(healthContent));

        // Test /healthz endpoint
        var healthzResponse = await _client.GetAsync("/healthz");
        Assert.Equal(HttpStatusCode.OK, healthzResponse.StatusCode);
        var healthzContent = await healthzResponse.Content.ReadAsStringAsync();
        Assert.False(string.IsNullOrEmpty(healthzContent));
    }

    [Fact]
    public async Task test_ac12_metrics_endpoint_same_port_as_service()
    {
        // Check that all endpoints are on the same port (the factory client uses the same base address)
        var serviceBaseAddress = _client.BaseAddress!;
        var servicePort = serviceBaseAddress.Port;

        // Create request to metrics endpoint
        var metricsUri = new Uri(serviceBaseAddress, "/metrics");
        Assert.Equal(servicePort, metricsUri.Port);

        var healthUri = new Uri(serviceBaseAddress, "/health");
        Assert.Equal(servicePort, healthUri.Port);

        var apiUri = new Uri(serviceBaseAddress, "/cart/123");
        Assert.Equal(servicePort, apiUri.Port);

        // Verify metrics responds on same port
        var response = await _client.GetAsync("/metrics");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    #region Helper Methods
    private static long GetCounterValue(string metricsContent, string metricName, string labelKey, string labelValue)
    {
        var lines = metricsContent.Split('\n');
        foreach (var line in lines)
        {
            if (line.StartsWith($"{metricName}{{{labelKey}=\"{labelValue}\"}}"))
            {
                var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 2 && long.TryParse(parts[1], out long value))
                {
                    return value;
                }
            }
        }
        return 0;
    }

    private static long GetCounterValueWithTwoLabels(string metricsContent, string metricName, string label1Key, string label1Value, string label2Key, string label2Value)
    {
        var lines = metricsContent.Split('\n');
        foreach (var line in lines)
        {
            if (line.StartsWith($"{metricName}{{{label1Key}=\"{label1Value}\",{label2Key}=\"{label2Value}\"}}") ||
                line.StartsWith($"{metricName}{{{label2Key}=\"{label2Value}\",{label1Key}=\"{label1Value}\"}}"))
            {
                var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 2 && long.TryParse(parts[1], out long value))
                {
                    return value;
                }
            }
        }
        return 0;
    }

    private static long GetHistogramCount(string metricsContent, string metricName, string labelKey, string labelValue)
    {
        var lines = metricsContent.Split('\n');
        foreach (var line in lines)
        {
            if (line.StartsWith($"{metricName}_count{{{labelKey}=\"{labelValue}\"}}"))
            {
                var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 2 && long.TryParse(parts[1], out long value))
                {
                    return value;
                }
            }
        }
        return 0;
    }

    private static long[] GetHistogramBucketValues(string metricsContent, string metricName, string labelKey, string labelValue)
    {
        var bucketValues = new List<long>();
        var lines = metricsContent.Split('\n');
        var expectedBuckets = new[] { 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 };
        
        foreach (var bucket in expectedBuckets)
        {
            foreach (var line in lines)
            {
                if (line.StartsWith($"{metricName}_bucket{{{labelKey}=\"{labelValue}\",le=\"{bucket.ToString(System.Globalization.CultureInfo.InvariantCulture)}\"}}"))
                {
                    var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length >= 2 && long.TryParse(parts[1], out long value))
                    {
                        bucketValues.Add(value);
                    }
                    else
                    {
                        bucketValues.Add(0);
                    }
                    break;
                }
            }
        }
        
        return bucketValues.ToArray();
    }
    #endregion
}
