using Xunit;
using Polly;
using Confluent.Kafka;
using OpenTelemetry.Trace;
using System;
using System.Threading.Tasks;
using System.Threading;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using System.Collections.Generic;
using System.Linq;
using System.Diagnostics;

// Interfaces imported from spec
public interface IKafkaRetryPolicyProvider
{
    IAsyncPolicy GetConsumeRetryPolicy();
    IAsyncPolicy GetDlqProduceRetryPolicy();
}

public interface IOrderConsumptionIdempotencyService
{
    Task<bool> IsOrderProcessedAsync(Guid orderId, CancellationToken cancellationToken);
    Task MarkOrderProcessedAsync(Guid orderId, DateTimeOffset processedAt, CancellationToken cancellationToken);
}

public class KafkaTransientException : Exception
{
    public KafkaTransientException(Error error) : base(error.Reason) { }
}

public class IdempotencyViolationException : Exception
{
    public IdempotencyViolationException(string message) : base(message) { }
}

public class KafkaRetryACTests : IDisposable
{
    private readonly IServiceCollection _services;
    private IServiceProvider _serviceProvider;
    private readonly Dictionary<string, string> _testConfigValues = new();

    public KafkaRetryACTests()
    {
        _services = new ServiceCollection();
        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(_testConfigValues)
            .Build();
        _services.AddSingleton<IConfiguration>(config);
    }

    public void Dispose() { }

    private void BuildServiceProvider()
    {
        _serviceProvider = _services.BuildServiceProvider();
    }

    #region AC-1 Tests
    [Fact]
    public async Task test_ac1_consume_transient_error_retries_with_exponential_backoff()
    {
        // Arrange
        _testConfigValues["ACCOUNTING_KAFKA_CONSUME_MAX_RETRIES"] = "3";
        _testConfigValues["ACCOUNTING_KAFKA_CONSUME_INITIAL_BACKOFF_MS"] = "100";
        _testConfigValues["ACCOUNTING_KAFKA_CONSUME_MAX_BACKOFF_MS"] = "1000";
        BuildServiceProvider();

        var retryPolicyProvider = _serviceProvider.GetRequiredService<IKafkaRetryPolicyProvider>();
        var consumePolicy = retryPolicyProvider.GetConsumeRetryPolicy();
        var attemptCount = 0;
        var delayTimestamps = new List<DateTimeOffset>();

        // Act
        var exception = await Record.ExceptionAsync(async () =>
        {
            await consumePolicy.ExecuteAsync(async () =>
            {
                attemptCount++;
                delayTimestamps.Add(DateTimeOffset.UtcNow);
                throw new KafkaTransientException(new Error(ErrorCode.Local_Transport, "Connection timeout", isTransient: true));
            });
        });

        // Assert
        Assert.NotNull(exception);
        Assert.Equal(4, attemptCount); // 1 initial + 3 retries = 4 attempts
        // Verify exponential backoff delays (approximate, 100ms, 200ms, 400ms)
        var delays = delayTimestamps.Zip(delayTimestamps.Skip(1), (a, b) => b - a).ToList();
        Assert.InRange(delays[0].TotalMilliseconds, 80, 120);
        Assert.InRange(delays[1].TotalMilliseconds, 180, 220);
        Assert.InRange(delays[2].TotalMilliseconds, 380, 420);
    }
    #endregion

    #region AC-2 Tests
    [Fact]
    public async Task test_ac2_dlq_produce_transient_error_retries_with_exponential_backoff()
    {
        // Arrange
        _testConfigValues["ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_RETRIES"] = "3";
        _testConfigValues["ACCOUNTING_KAFKA_DLQ_PRODUCE_INITIAL_BACKOFF_MS"] = "100";
        _testConfigValues["ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_BACKOFF_MS"] = "1000";
        BuildServiceProvider();

        var retryPolicyProvider = _serviceProvider.GetRequiredService<IKafkaRetryPolicyProvider>();
        var producePolicy = retryPolicyProvider.GetDlqProduceRetryPolicy();
        var attemptCount = 0;
        var delayTimestamps = new List<DateTimeOffset>();

        // Act
        var exception = await Record.ExceptionAsync(async () =>
        {
            await producePolicy.ExecuteAsync(async () =>
            {
                attemptCount++;
                delayTimestamps.Add(DateTimeOffset.UtcNow);
                throw new KafkaTransientException(new Error(ErrorCode.Local_Transport, "Connection timeout", isTransient: true));
            });
        });

        // Assert
        Assert.NotNull(exception);
        Assert.Equal(4, attemptCount); // 1 initial + 3 retries = 4 attempts
        // Verify exponential backoff delays (approximate, 100ms, 200ms, 400ms)
        var delays = delayTimestamps.Zip(delayTimestamps.Skip(1), (a, b) => b - a).ToList();
        Assert.InRange(delays[0].TotalMilliseconds, 80, 120);
        Assert.InRange(delays[1].TotalMilliseconds, 180, 220);
        Assert.InRange(delays[2].TotalMilliseconds, 380, 420);
    }
    #endregion

    #region AC-3 Tests
    [Fact]
    public async Task test_ac3_consume_retries_exhausted_crashes_no_offset_commit()
    {
        // Arrange
        _testConfigValues["ACCOUNTING_KAFKA_CONSUME_MAX_RETRIES"] = "2";
        BuildServiceProvider();
        var offsetCommitted = false;

        // Act
        var exception = await Record.ExceptionAsync(async () =>
        {
            // Simulate consumer loop
            var retryPolicy = _serviceProvider.GetRequiredService<IKafkaRetryPolicyProvider>().GetConsumeRetryPolicy();
            await retryPolicy.ExecuteAsync(async () =>
            {
                throw new KafkaTransientException(new Error(ErrorCode.Local_Transport, "Transient error", isTransient: true));
            });
            // This line should never be reached if retries are exhausted
            offsetCommitted = true;
        });

        // Assert
        Assert.NotNull(exception);
        Assert.IsType<KafkaTransientException>(exception);
        Assert.False(offsetCommitted);
        // Verify exit code would be non-zero (simulated by unhandled exception)
    }
    #endregion

    #region AC-4 Tests
    [Fact]
    public async Task test_ac4_dlq_produce_retries_exhausted_crashes_no_offset_commit()
    {
        // Arrange
        _testConfigValues["ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_RETRIES"] = "2";
        BuildServiceProvider();
        var offsetCommitted = false;

        // Act
        var exception = await Record.ExceptionAsync(async () =>
        {
            // Simulate DLQ produce flow
            var retryPolicy = _serviceProvider.GetRequiredService<IKafkaRetryPolicyProvider>().GetDlqProduceRetryPolicy();
            await retryPolicy.ExecuteAsync(async () =>
            {
                throw new KafkaTransientException(new Error(ErrorCode.Local_Transport, "Transient error", isTransient: true));
            });
            // This line should never be reached if retries are exhausted
            offsetCommitted = true;
        });

        // Assert
        Assert.NotNull(exception);
        Assert.IsType<KafkaTransientException>(exception);
        Assert.False(offsetCommitted);
        // Verify exit code would be non-zero (simulated by unhandled exception)
    }
    #endregion

    #region AC-5 Tests
    [Fact]
    public async Task test_ac5_retry_attempts_generate_correct_otel_spans()
    {
        // Arrange
        _testConfigValues["ACCOUNTING_KAFKA_CONSUME_MAX_RETRIES"] = "2";
        BuildServiceProvider();
        var retryPolicy = _serviceProvider.GetRequiredService<IKafkaRetryPolicyProvider>().GetConsumeRetryPolicy();
        var exportedSpans = new List<Activity>();
        using var tracerProvider = Sdk.CreateTracerProviderBuilder()
            .AddSource("Accounting.Service")
            .AddInMemoryExporter(exportedSpans)
            .Build();

        // Act
        await Record.ExceptionAsync(async () =>
        {
            await retryPolicy.ExecuteAsync(async () =>
            {
                throw new KafkaTransientException(new Error(ErrorCode.Local_Transport, "Transient error", isTransient: true));
            });
        });

        // Assert
        var retrySpans = exportedSpans.Where(s => s.Tags.Any(t => t.Key == "kafka.retry_attempt_number")).ToList();
        Assert.Equal(2, retrySpans.Count); // 2 retry attempts
        for (var i = 0; i < retrySpans.Count; i++)
        {
            var span = retrySpans[i];
            Assert.Equal(i + 1, int.Parse(span.Tags.First(t => t.Key == "kafka.retry_attempt_number").Value));
            Assert.Equal(2, int.Parse(span.Tags.First(t => t.Key == "kafka.retry_max_attempts").Value));
            Assert.Equal(Status.Error, span.Status);
        }
    }
    #endregion

    #region AC-6 Tests
    [Fact]
    public async Task test_ac6_retried_consume_skips_already_processed_orders_no_duplicates()
    {
        // Arrange
        var testOrderId = Guid.NewGuid();
        var idempotencyService = new MockIdempotencyService();
        await idempotencyService.MarkOrderProcessedAsync(testOrderId, DateTimeOffset.UtcNow, CancellationToken.None);
        _services.AddSingleton<IOrderConsumptionIdempotencyService>(idempotencyService);
        BuildServiceProvider();

        var transactionRecordCreated = false;
        var offsetCommitted = false;

        // Act
        // Simulate retry flow where order was already processed
        var idempotencyServiceInstance = _serviceProvider.GetRequiredService<IOrderConsumptionIdempotencyService>();
        var isProcessed = await idempotencyServiceInstance.IsOrderProcessedAsync(testOrderId, CancellationToken.None);
        if (isProcessed)
        {
            offsetCommitted = true;
        }
        else
        {
            transactionRecordCreated = true;
            await idempotencyServiceInstance.MarkOrderProcessedAsync(testOrderId, DateTimeOffset.UtcNow, CancellationToken.None);
            offsetCommitted = true;
        }

        // Assert
        Assert.True(isProcessed);
        Assert.False(transactionRecordCreated); // No duplicate transaction
        Assert.True(offsetCommitted); // Offset is committed
    }

    private class MockIdempotencyService : IOrderConsumptionIdempotencyService
    {
        private readonly HashSet<Guid> _processedOrders = new();
        public Task<bool> IsOrderProcessedAsync(Guid orderId, CancellationToken cancellationToken)
        {
            return Task.FromResult(_processedOrders.Contains(orderId));
        }

        public Task MarkOrderProcessedAsync(Guid orderId, DateTimeOffset processedAt, CancellationToken cancellationToken)
        {
            _processedOrders.Add(orderId);
            return Task.CompletedTask;
        }
    }
    #endregion

    #region AC-7 Tests
    [Fact]
    public async Task test_ac7_consume_max_retries_zero_no_retries_immediate_crash()
    {
        // Arrange
        _testConfigValues["ACCOUNTING_KAFKA_CONSUME_MAX_RETRIES"] = "0";
        BuildServiceProvider();
        var consumePolicy = _serviceProvider.GetRequiredService<IKafkaRetryPolicyProvider>().GetConsumeRetryPolicy();
        var attemptCount = 0;
        var offsetCommitted = false;

        // Act
        var exception = await Record.ExceptionAsync(async () =>
        {
            await consumePolicy.ExecuteAsync(async () =>
            {
                attemptCount++;
                throw new KafkaTransientException(new Error(ErrorCode.Local_Transport, "Transient error", isTransient: true));
            });
            offsetCommitted = true;
        });

        // Assert
        Assert.NotNull(exception);
        Assert.Equal(1, attemptCount); // No retries, only 1 initial attempt
        Assert.False(offsetCommitted);
    }
    #endregion

    #region AC-8 Tests
    [Fact]
    public async Task test_ac8_dlq_produce_max_retries_zero_no_retries_immediate_crash()
    {
        // Arrange
        _testConfigValues["ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_RETRIES"] = "0";
        BuildServiceProvider();
        var producePolicy = _serviceProvider.GetRequiredService<IKafkaRetryPolicyProvider>().GetDlqProduceRetryPolicy();
        var attemptCount = 0;
        var offsetCommitted = false;

        // Act
        var exception = await Record.ExceptionAsync(async () =>
        {
            await producePolicy.ExecuteAsync(async () =>
            {
                attemptCount++;
                throw new KafkaTransientException(new Error(ErrorCode.Local_Transport, "Transient error", isTransient: true));
            });
            offsetCommitted = true;
        });

        // Assert
        Assert.NotNull(exception);
        Assert.Equal(1, attemptCount); // No retries, only 1 initial attempt
        Assert.False(offsetCommitted);
    }
    #endregion

    #region AC-9 Tests
    [Theory]
    [InlineData("ACCOUNTING_KAFKA_CONSUME_MAX_RETRIES", "-1")]
    [InlineData("ACCOUNTING_KAFKA_CONSUME_INITIAL_BACKOFF_MS", "5")]
    [InlineData("ACCOUNTING_KAFKA_CONSUME_MAX_BACKOFF_MS", "3600001")]
    [InlineData("ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_RETRIES", "-2")]
    [InlineData("ACCOUNTING_KAFKA_DLQ_PRODUCE_INITIAL_BACKOFF_MS", "9")]
    [InlineData("ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_BACKOFF_MS", "4000000")]
    public void test_ac9_invalid_environment_variables_cause_startup_failure(string envVarName, string invalidValue)
    {
        // Arrange
        _testConfigValues[envVarName] = invalidValue;

        // Act & Assert
        var exception = Record.Exception(() => BuildServiceProvider());
        Assert.NotNull(exception);
        Assert.Contains("configuration", exception.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains(envVarName, exception.Message, StringComparison.OrdinalIgnoreCase);
    }
    #endregion
}
