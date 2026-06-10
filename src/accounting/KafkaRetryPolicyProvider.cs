using Microsoft.Extensions.Configuration;
using Polly;
using Polly.Retry;
using System.Diagnostics;
using OpenTelemetry.Trace;
using Confluent.Kafka;

namespace Accounting.Service;

public interface IKafkaRetryPolicyProvider
{
    IAsyncPolicy GetConsumeRetryPolicy();
    IAsyncPolicy GetDlqProduceRetryPolicy();
}

public class KafkaTransientException : Exception
{
    public KafkaTransientException(Error error) : base(error.Reason) { }
}

public class IdempotencyViolationException : Exception
{
    public IdempotencyViolationException(string message) : base(message) { }
}

public class KafkaRetryPolicyProvider : IKafkaRetryPolicyProvider
{
    private readonly IAsyncPolicy _consumeRetryPolicy;
    private readonly IAsyncPolicy _dlqProduceRetryPolicy;
    private const string ActivitySourceName = "Accounting.Service";
    private static readonly ActivitySource _activitySource = new(ActivitySourceName);

    public KafkaRetryPolicyProvider(IConfiguration configuration)
    {
        // Validate configuration
        ValidateConfiguration(configuration);

        // Build consume retry policy
        var consumeMaxRetries = int.Parse(configuration["ACCOUNTING_KAFKA_CONSUME_MAX_RETRIES"] ?? "5");
        var consumeInitialBackoffMs = int.Parse(configuration["ACCOUNTING_KAFKA_CONSUME_INITIAL_BACKOFF_MS"] ?? "100");
        var consumeMaxBackoffMs = int.Parse(configuration["ACCOUNTING_KAFKA_CONSUME_MAX_BACKOFF_MS"] ?? "10000");
        _consumeRetryPolicy = BuildRetryPolicy(consumeMaxRetries, consumeInitialBackoffMs, consumeMaxBackoffMs);

        // Build DLQ produce retry policy
        var dlqMaxRetries = int.Parse(configuration["ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_RETRIES"] ?? "5");
        var dlqInitialBackoffMs = int.Parse(configuration["ACCOUNTING_KAFKA_DLQ_PRODUCE_INITIAL_BACKOFF_MS"] ?? "100");
        var dlqMaxBackoffMs = int.Parse(configuration["ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_BACKOFF_MS"] ?? "10000");
        _dlqProduceRetryPolicy = BuildRetryPolicy(dlqMaxRetries, dlqInitialBackoffMs, dlqMaxBackoffMs);
    }

    private static void ValidateConfiguration(IConfiguration configuration)
    {
        ValidateIntConfig(configuration, "ACCOUNTING_KAFKA_CONSUME_MAX_RETRIES", minValue: 0);
        ValidateIntConfig(configuration, "ACCOUNTING_KAFKA_CONSUME_INITIAL_BACKOFF_MS", minValue: 10, maxValue: 3600000);
        ValidateIntConfig(configuration, "ACCOUNTING_KAFKA_CONSUME_MAX_BACKOFF_MS", minValue: 10, maxValue: 3600000);
        ValidateIntConfig(configuration, "ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_RETRIES", minValue: 0);
        ValidateIntConfig(configuration, "ACCOUNTING_KAFKA_DLQ_PRODUCE_INITIAL_BACKOFF_MS", minValue: 10, maxValue: 3600000);
        ValidateIntConfig(configuration, "ACCOUNTING_KAFKA_DLQ_PRODUCE_MAX_BACKOFF_MS", minValue: 10, maxValue: 3600000);
    }

    private static void ValidateIntConfig(IConfiguration configuration, string key, int minValue, int? maxValue = null)
    {
        var valueStr = configuration[key];
        if (!int.TryParse(valueStr, out var value))
        {
            throw new InvalidOperationException($"Invalid configuration value for {key}: must be an integer");
        }

        if (value < minValue)
        {
            throw new InvalidOperationException($"Invalid configuration value for {key}: must be at least {minValue}");
        }

        if (maxValue.HasValue && value > maxValue.Value)
        {
            throw new InvalidOperationException($"Invalid configuration value for {key}: must be no more than {maxValue.Value}");
        }
    }

    private static IAsyncPolicy BuildRetryPolicy(int maxRetries, int initialBackoffMs, int maxBackoffMs)
    {
        if (maxRetries == 0)
        {
            return Policy.NoOpAsync();
        }

        return Policy
            .Handle<KafkaTransientException>()
            .WaitAndRetryAsync(
                retryCount: maxRetries,
                sleepDurationProvider: (attemptNumber, _) =>
                {
                    var backoff = TimeSpan.FromMilliseconds(initialBackoffMs * Math.Pow(2, attemptNumber - 1));
                    return backoff > TimeSpan.FromMilliseconds(maxBackoffMs)
                        ? TimeSpan.FromMilliseconds(maxBackoffMs)
                        : backoff;
                },
                onRetryAsync: (exception, delay, attemptNumber, _) =>
                {
                    using var activity = _activitySource.StartActivity("Kafka Retry Attempt", ActivityKind.Internal);
                    activity?.SetTag("kafka.retry_attempt_number", attemptNumber);
                    activity?.SetTag("kafka.retry_max_attempts", maxRetries);
                    activity?.SetStatus(Status.Error);
                    return Task.CompletedTask;
                });
    }

    public IAsyncPolicy GetConsumeRetryPolicy() => _consumeRetryPolicy;

    public IAsyncPolicy GetDlqProduceRetryPolicy() => _dlqProduceRetryPolicy;
}
