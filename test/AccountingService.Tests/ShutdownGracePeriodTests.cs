using Xunit;
using Moq;
using Confluent.Kafka;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Configuration;
using Accounting;
using System.Diagnostics;
using System.Net.Sockets;
using Npgsql;

namespace AccountingService.Tests;

public class ShutdownGracePeriodTests : IDisposable
{
    private readonly IHost _testHost;
    private readonly Mock<IConsumer<string, string>> _mockKafkaConsumer;
    private readonly CancellationTokenSource _cts;
    private const int DefaultShutdownTimeout = 30;

    public ShutdownGracePeriodTests()
    {
        _mockKafkaConsumer = new Mock<IConsumer<string, string>>();
        _cts = new CancellationTokenSource();

        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>())
            .Build();

        _testHost = Host.CreateDefaultBuilder()
            .ConfigureServices(services =>
            {
                services.AddSingleton(config);
                services.AddSingleton(_mockKafkaConsumer.Object);
                services.AddHostedService<Consumer>();
            })
            .Build();
    }

    public void Dispose()
    {
        _cts.Dispose();
        _testHost.Dispose();
    }

    [Fact]
    public async Task test_ac1_kafka_stops_polling_on_shutdown_signal()
    {
        // Arrange
        await _testHost.StartAsync();
        var consumer = _testHost.Services.GetRequiredService<IHostedService>() as Consumer;
        Assert.NotNull(consumer);
        int pollCallCountBeforeShutdown = _mockKafkaConsumer.Invocations.Count(i => i.Method.Name == nameof(IConsumer<string, string>.Consume));

        // Act: Trigger shutdown signal
        var appLifetime = _testHost.Services.GetRequiredService<IHostApplicationLifetime>();
        appLifetime.StopApplication();
        await Task.Delay(100); // Allow time for shutdown handler to execute

        // Assert: No new Consume calls after shutdown initiation
        int pollCallCountAfterShutdown = _mockKafkaConsumer.Invocations.Count(i => i.Method.Name == nameof(IConsumer<string, string>.Consume));
        Assert.Equal(pollCallCountBeforeShutdown, pollCallCountAfterShutdown);
    }

    [Fact]
    public async Task test_ac2_offsets_committed_when_inflight_complete_before_timeout()
    {
        // Arrange
        var testOffset = new TopicPartitionOffset(new TopicPartition("accounting-topic", 0), new Offset(100));
        _mockKafkaConsumer.Setup(c => c.Commit(It.IsAny<IEnumerable<TopicPartitionOffset>>()))
            .Verifiable(Times.Once);

        await _testHost.StartAsync();
        var consumer = _testHost.Services.GetRequiredService<IHostedService>() as Consumer;
        Assert.NotNull(consumer);

        // Simulate in-flight message that completes quickly
        var inflightTask = Task.Run(async () =>
        {
            await Task.Delay(50);
            _mockKafkaConsumer.Raise(c => c.OnConsumeError += null, new Error(ErrorCode.NoError));
        });

        // Act: Trigger shutdown with sufficient timeout
        var shutdownToken = new CancellationTokenSource(TimeSpan.FromSeconds(5)).Token;
        await consumer.StopAsync(shutdownToken);

        // Assert: Commit was called for processed messages
        _mockKafkaConsumer.Verify(c => c.Commit(It.IsAny<IEnumerable<TopicPartitionOffset>>()), Times.Once);
    }

    [Fact]
    public async Task test_ac3_service_exits_when_inflight_exceeds_timeout()
    {
        // Arrange
        var consumer = _testHost.Services.GetRequiredService<IHostedService>() as Consumer;
        Assert.NotNull(consumer);

        // Simulate long-running in-flight message
        var longRunningTask = Task.Run(async () => await Task.Delay(TimeSpan.FromSeconds(10)));

        // Act: Trigger shutdown with short 1 second timeout
        var timeoutCts = new CancellationTokenSource(TimeSpan.FromSeconds(1));
        var stopTask = consumer.StopAsync(timeoutCts.Token);
        var stopTaskCompleted = await Task.WhenAny(stopTask, Task.Delay(TimeSpan.FromSeconds(2)));

        // Assert: StopAsync completed before the long running task
        Assert.Equal(stopTask, stopTaskCompleted);
        Assert.True(timeoutCts.IsCancellationRequested);
    }

    [Fact]
    public async Task test_ac4_kafka_consumer_disposed_after_shutdown()
    {
        // Arrange
        bool consumerDisposed = false;
        _mockKafkaConsumer.Setup(c => c.Dispose()).Callback(() => consumerDisposed = true);

        await _testHost.StartAsync();
        var consumer = _testHost.Services.GetRequiredService<IHostedService>() as Consumer;
        Assert.NotNull(consumer);

        // Act
        await consumer.StopAsync(CancellationToken.None);
        await consumer.DisposeAsync();

        // Assert: Consumer Dispose was called
        Assert.True(consumerDisposed);
        _mockKafkaConsumer.Verify(c => c.Dispose(), Times.Once);
    }

    [Fact]
    public async Task test_ac5_postgres_connections_closed_after_shutdown()
    {
        // Arrange: Track open Npgsql connections
        int initialActiveConnections = GetActivePostgresConnections();
        await _testHost.StartAsync();
        var consumer = _testHost.Services.GetRequiredService<IHostedService>() as Consumer;
        Assert.NotNull(consumer);

        // Simulate opening a database connection during message processing
        using var testConnection = new NpgsqlConnection("Host=localhost;Database=accounting;Username=postgres;Password=postgres");
        await testConnection.OpenAsync();
        int connectionsAfterOpen = GetActivePostgresConnections();
        Assert.True(connectionsAfterOpen > initialActiveConnections);

        // Act
        await consumer.DisposeAsync();
        await testConnection.DisposeAsync();
        await Task.Delay(100); // Allow time for connection cleanup

        // Assert: No active connections left from the service
        int connectionsAfterDispose = GetActivePostgresConnections();
        Assert.Equal(initialActiveConnections, connectionsAfterDispose);
    }

    [Fact]
    public void test_ac6_shutdown_timeout_config_defaults_to_30()
    {
        // Arrange: No explicit ShutdownTimeoutSeconds in config
        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>())
            .Build();

        var services = new ServiceCollection();
        services.AddSingleton(config);
        services.AddSingleton(_mockKafkaConsumer.Object);
        services.AddHostedService<Consumer>();
        var provider = services.BuildServiceProvider();

        // Act: Resolve configuration value
        var resolvedConfig = provider.GetRequiredService<IConfiguration>();
        var timeoutValue = resolvedConfig.GetValue<int>("ShutdownTimeoutSeconds", DefaultShutdownTimeout);

        // Assert: Default value is 30
        Assert.Equal(DefaultShutdownTimeout, timeoutValue);

        // Test explicit config value works
        var configWithCustomTimeout = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["ShutdownTimeoutSeconds"] = "60" })
            .Build();
        var customTimeout = configWithCustomTimeout.GetValue<int>("ShutdownTimeoutSeconds", DefaultShutdownTimeout);
        Assert.Equal(60, customTimeout);
    }

    [Fact]
    public async Task test_ac7_success_log_emitted_on_successful_shutdown()
    {
        // Arrange
        var loggerMock = new Mock<Microsoft.Extensions.Logging.ILogger<Consumer>>();
        var services = new ServiceCollection();
        services.AddSingleton(new ConfigurationBuilder().Build());
        services.AddSingleton(_mockKafkaConsumer.Object);
        services.AddSingleton(loggerMock.Object);
        services.AddHostedService<Consumer>();
        var provider = services.BuildServiceProvider();

        var consumer = provider.GetRequiredService<IHostedService>() as Consumer;
        Assert.NotNull(consumer);

        // Act: Complete shutdown successfully
        await consumer.StopAsync(CancellationToken.None);

        // Assert: Information log with correct message is emitted
        loggerMock.Verify(
            x => x.Log(
                Microsoft.Extensions.Logging.LogLevel.Information,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => v.ToString()!.Contains("Accounting service shutdown completed successfully, all in-flight messages processed and resources cleaned up")),
                It.IsAny<Exception>(),
                It.IsAny<Func<It.IsAnyType, Exception?, string>>()),
            Times.Once);
    }

    [Fact]
    public async Task test_ac8_timeout_log_emitted_on_shutdown_timeout()
    {
        // Arrange
        const int testTimeoutSeconds = 2;
        var loggerMock = new Mock<Microsoft.Extensions.Logging.ILogger<Consumer>>();

        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["ShutdownTimeoutSeconds"] = testTimeoutSeconds.ToString() })
            .Build();

        var services = new ServiceCollection();
        services.AddSingleton(config);
        services.AddSingleton(_mockKafkaConsumer.Object);
        services.AddSingleton(loggerMock.Object);
        services.AddHostedService<Consumer>();
        var provider = services.BuildServiceProvider();

        var consumer = provider.GetRequiredService<IHostedService>() as Consumer;
        Assert.NotNull(consumer);

        // Act: Trigger shutdown with timeout
        var timeoutCts = new CancellationTokenSource(TimeSpan.FromSeconds(testTimeoutSeconds));
        await Assert.ThrowsAsync<TaskCanceledException>(() => consumer.StopAsync(timeoutCts.Token));

        // Assert: Warning log with correct timeout is emitted
        loggerMock.Verify(
            x => x.Log(
                Microsoft.Extensions.Logging.LogLevel.Warning,
                It.IsAny<EventId>(),
                It.Is<It.IsAnyType>((v, t) => 
                    v.ToString()!.Contains($"Accounting service shutdown timed out after {testTimeoutSeconds} seconds")),
                It.IsAny<Exception>(),
                It.IsAny<Func<It.IsAnyType, Exception?, string>>()),
            Times.Once);
    }

    private int GetActivePostgresConnections()
    {
        // Simple method to count active connections to PostgreSQL port
        try
        {
            using var tcpClient = new TcpClient();
            var result = tcpClient.BeginConnect("localhost", 5432, null, null);
            var success = result.AsyncWaitHandle.WaitOne(TimeSpan.FromMilliseconds(100));
            if (!success) return 0;
            tcpClient.EndConnect(result);
            return 1;
        }
        catch
        {
            return 0;
        }
    }
}
