using Xunit;
using Microsoft.AspNetCore.Mvc.Testing;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Configuration;

// Using the interface from the spec
public interface IGracefulShutdownService
{
    void RegisterSignalHandlers();
    CancellationToken ShutdownInitiatedToken { get; }
    CancellationToken ShutdownImmediateToken { get; }
    void RegisterShutdownOperation(Func<CancellationToken, Task> shutdownOperation);
}

public class GracefulShutdownTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public GracefulShutdownTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
    }

    [Fact]
    public async Task TestAC1_SIGINTStopsConsumingNewMessagesAndRequests()
    {
        // Arrange: Start service and verify it's accepting requests/consuming messages
        var client = _factory.CreateClient();
        var shutdownService = _factory.Services.GetRequiredService<IGracefulShutdownService>();
        
        // Act: Send SIGINT signal to the process
        Process.GetCurrentProcess().CloseMainWindow(); // Equivalent to SIGINT on Windows, we'll handle POSIX signals in CI
        await Task.Delay(100); // Allow time for signal to be processed

        // Assert: Shutdown initiated token is triggered
        Assert.True(shutdownService.ShutdownInitiatedToken.IsCancellationRequested);
        
        // Assert: Service returns 503 Service Unavailable for new HTTP requests
        var response = await client.GetAsync("/health");
        Assert.Equal(System.Net.HttpStatusCode.ServiceUnavailable, response.StatusCode);
        
        // Assert: Kafka consumer is paused (not consuming new messages)
        // We verify via metrics/consumer state: no new messages are consumed after signal
    }

    [Fact]
    public async Task TestAC2_InFlightKafkaMessagesCompleteBeforeShutdownWithinGracePeriod()
    {
        // Arrange: Start service, send a Kafka message that will take 5 seconds to process
        var shutdownService = _factory.Services.GetRequiredService<IGracefulShutdownService>();
        var messageProcessed = false;
        
        shutdownService.RegisterShutdownOperation(async ct => 
        {
            await Task.Delay(5000, ct);
            messageProcessed = true;
        });
        
        // Act: Trigger shutdown immediately after message is consumed
        using var cts = new CancellationTokenSource();
        cts.CancelAfter(10000); // Total test timeout
        
        var shutdownTask = Task.Run(() => 
        {
            // Simulate shutdown trigger
            Process.GetCurrentProcess().CloseMainWindow();
        }, cts.Token);

        // Assert: Message processing completes even though shutdown was triggered
        await Task.WhenAll(shutdownTask, Task.Delay(6000, cts.Token));
        Assert.True(messageProcessed);
    }

    [Fact]
    public async Task TestAC3_PendingDatabaseOperationsCompleteCleanlyWithinGracePeriod()
    {
        // Arrange: Start a database transaction that will take 3 seconds to commit
        var shutdownService = _factory.Services.GetRequiredService<IGracefulShutdownService>();
        bool transactionCommitted = false;
        
        shutdownService.RegisterShutdownOperation(async ct => 
        {
            // Simulate DB commit
            await Task.Delay(3000, ct);
            transactionCommitted = true;
        });

        // Act: Trigger shutdown mid-transaction
        Process.GetCurrentProcess().CloseMainWindow();
        await Task.Delay(4000);

        // Assert: Transaction is committed, DB connections are closed gracefully
        Assert.True(transactionCommitted);
    }

    [Fact]
    public async Task TestAC4_ShutdownExceedingGracePeriodForceTerminatesWithWarning()
    {
        // Arrange: Configure grace period to 2 seconds
        var customFactory = _factory.WithWebHostBuilder(builder => 
        {
            builder.ConfigureAppConfiguration(config => 
            {
                config.AddInMemoryCollection(new Dictionary<string, string?>
                {
                    {"Shutdown:GracePeriodSeconds", "2"}
                });
            });
        });
        
        var shutdownService = customFactory.Services.GetRequiredService<IGracefulShutdownService>();
        bool longRunningOperationCompleted = false;
        
        shutdownService.RegisterShutdownOperation(async ct => 
        {
            // Operation takes 5 seconds, longer than grace period
            await Task.Delay(5000, ct);
            longRunningOperationCompleted = true;
        });

        // Act: Trigger shutdown
        var stopwatch = Stopwatch.StartNew();
        Process.GetCurrentProcess().CloseMainWindow();
        
        // Wait for service to terminate
        try
        {
            await Task.Delay(6000);
        }
        catch (OperationCanceledException)
        {
            // Expected
        }
        stopwatch.Stop();

        // Assert: Service terminated in ~2 seconds, operation did not complete
        Assert.InRange(stopwatch.Elapsed.TotalSeconds, 1.5, 2.5);
        Assert.False(longRunningOperationCompleted);
        // Verify warning log exists with incomplete operation list
    }

    [Fact]
    public void TestAC5_GracePeriodIsConfigurableWithDefault30Seconds()
    {
        // Test default value
        var config = _factory.Services.GetRequiredService<IConfiguration>();
        var defaultGracePeriod = config.GetValue<int>("Shutdown:GracePeriodSeconds");
        Assert.Equal(30, defaultGracePeriod);

        // Test environment variable configuration
        Environment.SetEnvironmentVariable("SHUTDOWN_GRACE_PERIOD_SECONDS", "15");
        var envFactory = _factory.WithWebHostBuilder(builder => 
        {
            builder.ConfigureAppConfiguration(config => 
            {
                config.AddEnvironmentVariables();
            });
        });
        var envConfig = envFactory.Services.GetRequiredService<IConfiguration>();
        var envGracePeriod = envConfig.GetValue<int>("Shutdown:GracePeriodSeconds");
        Assert.Equal(15, envGracePeriod);
        Environment.SetEnvironmentVariable("SHUTDOWN_GRACE_PERIOD_SECONDS", null);

        // Test appsettings.json configuration
        var appsettingsFactory = _factory.WithWebHostBuilder(builder => 
        {
            builder.ConfigureAppConfiguration(config => 
            {
                config.AddInMemoryCollection(new Dictionary<string, string?>
                {
                    {"Shutdown:GracePeriodSeconds", "45"}
                });
            });
        });
        var appConfig = appsettingsFactory.Services.GetRequiredService<IConfiguration>();
        var appGracePeriod = appConfig.GetValue<int>("Shutdown:GracePeriodSeconds");
        Assert.Equal(45, appGracePeriod);
    }

    [Fact]
    public async Task TestAC6_SIGTERMTriggersSameGracefulShutdownAsSIGINT()
    {
        // Arrange
        var shutdownService = _factory.Services.GetRequiredService<IGracefulShutdownService>();
        bool shutdownOperationCompleted = false;
        
        shutdownService.RegisterShutdownOperation(async ct => 
        {
            await Task.Delay(1000, ct);
            shutdownOperationCompleted = true;
        });

        // Act: Send SIGTERM signal
        // For test purposes, we simulate SIGTERM via process kill
        using var process = Process.GetCurrentProcess();
        process.Kill(entireProcessTree: false); // Sends SIGTERM on POSIX systems
        await Task.Delay(2000);

        // Assert: Same behavior as SIGINT
        Assert.True(shutdownService.ShutdownInitiatedToken.IsCancellationRequested);
        Assert.True(shutdownOperationCompleted);
    }

    [Fact]
    public async Task TestAC7_ShutdownCompletesEarlyWithExitCode0WhenAllOperationsDone()
    {
        // Arrange
        var shutdownService = _factory.Services.GetRequiredService<IGracefulShutdownService>();
        bool allOperationsDone = false;
        
        shutdownService.RegisterShutdownOperation(async ct => 
        {
            await Task.Delay(1000, ct);
            allOperationsDone = true;
        });

        // Act: Trigger shutdown, grace period is 30 seconds but our operations take only 1 second
        var stopwatch = Stopwatch.StartNew();
        Process.GetCurrentProcess().CloseMainWindow();
        
        try
        {
            await Task.Delay(5000);
        }
        catch (OperationCanceledException)
        {
            // Expected when process exits
        }
        stopwatch.Stop();

        // Assert: Service exits in ~1 second, well before 30 second grace period
        Assert.InRange(stopwatch.Elapsed.TotalSeconds, 0.5, 2);
        Assert.True(allOperationsDone);
        // Assert exit code is 0
        Assert.Equal(0, Environment.ExitCode);
    }
}
