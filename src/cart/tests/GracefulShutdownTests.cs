using Xunit;
using Microsoft.AspNetCore.Mvc.Testing;
using System.Net;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Moq;
using Cart;
using Cart.Services;
using Cart.Repositories;

namespace Cart.Tests;

public class GracefulShutdownTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public GracefulShutdownTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
    }

    [Fact]
    public async Task test_ac1_new_requests_return_503_with_retry_after_after_signal()
    {
        // Arrange
        var client = _factory.CreateClient();
        var lifetime = _factory.Services.GetRequiredService<IHostApplicationLifetime>();
        var cts = new CancellationTokenSource();

        // Act
        lifetime.StopApplication(); // Triggers application shutdown (simulates SIGINT/SIGTERM)
        var response = await client.GetAsync("/cart/testuser", cts.Token);

        // Assert
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.True(response.Headers.TryGetValues("Retry-After", out var retryAfterValues));
        Assert.Equal("10", retryAfterValues.FirstOrDefault());
    }

    [Fact]
    public async Task test_ac2_in_flight_requests_complete_within_grace_period()
    {
        // Arrange
        var mockRepo = new Mock<ICartRepository>();
        mockRepo.Setup(r => r.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>(), It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask)
            .Callback(() => Thread.Sleep(2000)); // Simulate 2s processing (well within 10s grace)

        var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton(mockRepo.Object);
            });
        });
        var client = factory.CreateClient();
        var lifetime = factory.Services.GetRequiredService<IHostApplicationLifetime>();

        // Act: Start request first, then trigger shutdown
        var requestTask = client.PostAsJsonAsync("/cart/testuser", new CartItem { ProductId = "test", Quantity = 1 });
        await Task.Delay(500); // Let request start processing
        lifetime.StopApplication();
        var response = await requestTask;

        // Assert
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        mockRepo.Verify(r => r.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>(), It.IsAny<CancellationToken>()), Times.Once);
        mockRepo.Verify(r => r.FlushAsync(It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task test_ac3_in_flight_requests_cancelled_after_10s_timeout()
    {
        // Arrange
        var requestCancelled = false;
        var mockRepo = new Mock<ICartRepository>();
        mockRepo.Setup(r => r.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>(), It.IsAny<CancellationToken>()))
            .Returns(async (string userId, CartItem item, CancellationToken ct) =>
            {
                try
                {
                    await Task.Delay(15000, ct); // Simulate 15s processing (longer than 10s grace)
                }
                catch (OperationCanceledException)
                {
                    requestCancelled = true;
                    throw;
                }
            });

        var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton(mockRepo.Object);
            });
        });
        var client = factory.CreateClient();
        var lifetime = factory.Services.GetRequiredService<IHostApplicationLifetime>();

        // Act: Start long running request, trigger shutdown
        var requestTask = client.PostAsJsonAsync("/cart/testuser", new CartItem { ProductId = "test", Quantity = 1 });
        await Task.Delay(500);
        lifetime.StopApplication();

        // Wait for timeout
        var timeoutTask = Task.Delay(12000);
        var completedTask = await Task.WhenAny(requestTask, timeoutTask);

        // Assert
        Assert.Equal(timeoutTask, completedTask); // Request not completed within 12s
        Assert.True(requestCancelled); // Request was cancelled
    }

    [Fact]
    public async Task test_ac4_pending_writes_flushed_before_exit()
    {
        // Arrange
        var flushCalled = false;
        var mockRepo = new Mock<ICartRepository>();
        mockRepo.Setup(r => r.FlushAsync(It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask)
            .Callback(() => flushCalled = true);

        var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton(mockRepo.Object);
            });
        });
        var lifetime = factory.Services.GetRequiredService<IHostApplicationLifetime>();

        // Act
        lifetime.StopApplication();
        await Task.Delay(2000); // Wait for shutdown sequence to run

        // Assert
        Assert.True(flushCalled);
    }

    [Fact]
    public void test_ac5_log_emitted_when_termination_signal_received()
    {
        // Arrange
        var mockLogger = new Mock<ILogger<CartService>>();
        var mockRepo = new Mock<ICartRepository>();
        var lifetime = new Mock<IHostApplicationLifetime>();

        // Act: Call registration method
        Program.RegisterShutdownHandlers(lifetime.Object, mockRepo.Object, mockLogger.Object);
        lifetime.Raise(l => l.ApplicationStopping += null, EventArgs.Empty); // Simulate stopping event

        // Assert
        mockLogger.Verify(
            l => l.Log(
                LogLevel.Information,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => v.ToString().Contains("Received termination signal")),
                It.IsAny<Exception>(),
                It.IsAny<Func<It.IsAnyType, Exception, string>>()),
            Times.Once
        );
    }

    [Fact]
    public async Task test_ac6_log_emitted_when_shutdown_times_out()
    {
        // Arrange
        var mockLogger = new Mock<ILogger<CartService>>();
        var mockRepo = new Mock<ICartRepository>();
        mockRepo.Setup(r => r.FlushAsync(It.IsAny<CancellationToken>()))
            .Returns(async (CancellationToken ct) =>
            {
                await Task.Delay(15000, ct); // Simulate flush taking longer than grace period
            });

        var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton(mockRepo.Object);
                services.AddSingleton(mockLogger.Object);
            });
        });
        var lifetime = factory.Services.GetRequiredService<IHostApplicationLifetime>();

        // Act
        lifetime.StopApplication();
        await Task.Delay(12000); // Wait past grace period

        // Assert
        mockLogger.Verify(
            l => l.Log(
                LogLevel.Warning,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => v.ToString().Contains("Graceful shutdown timed out after")),
                It.IsAny<Exception>(),
                It.IsAny<Func<It.IsAnyType, Exception, string>>()),
            Times.Once
        );
    }

    [Fact]
    public async Task test_ac7_log_emitted_when_shutdown_completes_successfully()
    {
        // Arrange
        var mockLogger = new Mock<ILogger<CartService>>();
        var mockRepo = new Mock<ICartRepository>();
        mockRepo.Setup(r => r.FlushAsync(It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask);

        var factory = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton(mockRepo.Object);
                services.AddSingleton(mockLogger.Object);
            });
        });
        var lifetime = factory.Services.GetRequiredService<IHostApplicationLifetime>();

        // Act
        lifetime.StopApplication();
        await Task.Delay(3000); // Wait for shutdown to complete

        // Assert
        mockLogger.Verify(
            l => l.Log(
                LogLevel.Information,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => v.ToString().Contains("Shutdown completed successfully in")),
                It.IsAny<Exception>(),
                It.IsAny<Func<It.IsAnyType, Exception, string>>()),
            Times.Once
        );
    }
}
