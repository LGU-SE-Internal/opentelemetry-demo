using Xunit;
using Moq;
using Confluent.Kafka;
using Microsoft.Extensions.Configuration;
using Accounting;
using System.Net.Sockets;
using System.Text.Json;

namespace AccountingService.Tests;

public class ConsumerKafkaRetryDlqTests
{
    private readonly Mock<IConsumer<string, string>> _mockConsumer;
    private readonly Mock<IProducer<string, string>> _mockDlqProducer;
    private readonly IConfiguration _configuration;
    private readonly Consumer _consumer;

    public ConsumerKafkaRetryDlqTests()
    {
        _mockConsumer = new Mock<IConsumer<string, string>>();
        _mockDlqProducer = new Mock<IProducer<string, string>>();
        
        var inMemorySettings = new Dictionary<string, string?>
        {
            {"KAFKA_CONSUMER_MAX_RETRY_ATTEMPTS", "3"},
            {"KAFKA_CONSUMER_INITIAL_RETRY_DELAY_MS", "1000"},
            {"KAFKA_CONSUMER_MAX_RETRY_DELAY_MS", "10000"},
            {"KAFKA_CONSUMER_DLQ_TOPIC_NAME", "accounting-service-dlq"}
        };
        _configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(inMemorySettings)
            .Build();
        
        _consumer = new Consumer(_configuration, _mockConsumer.Object, _mockDlqProducer.Object);
    }

    [Fact]
    public async Task test_ac1_transient_exception_retries_up_to_max_before_dlq()
    {
        // Arrange
        var testMessage = new ConsumeResult<string, string>
        {
            Message = new Message<string, string> { Key = "test-key", Value = "valid-value" },
            Topic = "accounting-topic",
            Partition = new Partition(0),
            Offset = new Offset(123)
        };
        var transientException = new SocketException(); // Network error = transient
        int processCallCount = 0;

        // Mock processing to always throw transient exception
        _consumer.ProcessMessage = (msg, ct) =>
        {
            processCallCount++;
            throw transientException;
        };

        // Act
        var result = await _consumer.ProcessMessageWithRetry(testMessage, CancellationToken.None);

        // Assert
        Assert.False(result); // Should return false for DLQ routing
        Assert.Equal(4, processCallCount); // 1 initial + 3 retries = 4 attempts
        _mockDlqProducer.Verify(p => p.ProduceAsync(
            It.Is<string>(t => t == "accounting-service-dlq"),
            It.Is<Message<string, string>>(m => 
                m.Key == testMessage.Key && 
                m.Value == testMessage.Value &&
                m.Headers.Any(h => h.Key == "x-retry-attempts" && GetHeaderValue(h) == "3")
            ),
            It.IsAny<CancellationToken>()
        ), Times.Once);
    }

    [Fact]
    public async Task test_ac2_retry_delays_follow_exponential_backoff()
    {
        // Arrange
        var testMessage = new ConsumeResult<string, string>
        {
            Message = new Message<string, string> { Key = "test-key", Value = "valid-value" }
        };
        var transientException = new TimeoutException(); // Transient
        List<TimeSpan> observedDelays = new();
        int attempt = 0;

        _consumer.ProcessMessage = (msg, ct) =>
        {
            attempt++;
            throw transientException;
        };
        _consumer.DelayFunction = (delay, ct) =>
        {
            observedDelays.Add(delay);
            return Task.CompletedTask;
        };

        // Act
        await _consumer.ProcessMessageWithRetry(testMessage, CancellationToken.None);

        // Assert expected delays: attempt 0 = 1000ms, attempt1=2000ms, attempt2=4000ms
        Assert.Equal(3, observedDelays.Count);
        Assert.Equal(TimeSpan.FromMilliseconds(1000), observedDelays[0]);
        Assert.Equal(TimeSpan.FromMilliseconds(2000), observedDelays[1]);
        Assert.Equal(TimeSpan.FromMilliseconds(4000), observedDelays[2]);
    }

    [Fact]
    public async Task test_ac3_exceeded_retry_messages_sent_to_dlq_with_all_headers()
    {
        // Arrange
        var originalHeaders = new Headers
        {
            {"original-header-1", "value1"u8.ToArray()},
            {"original-header-2", "value2"u8.ToArray()}
        };
        var testMessage = new ConsumeResult<string, string>
        {
            Message = new Message<string, string> 
            { 
                Key = "test-key", 
                Value = "valid-value",
                Headers = originalHeaders
            }
        };
        var testException = new InvalidOperationException("Database connection timed out");
        _consumer.ProcessMessage = (msg, ct) => throw testException;

        // Act
        await _consumer.ProcessMessageWithRetry(testMessage, CancellationToken.None);

        // Assert
        _mockDlqProducer.Verify(p => p.ProduceAsync(
            It.Is<string>(t => t == "accounting-service-dlq"),
            It.Is<Message<string, string>>(m => 
                // Original properties preserved
                m.Key == testMessage.Key &&
                m.Value == testMessage.Value &&
                // Original headers preserved
                m.Headers.Any(h => h.Key == "original-header-1" && GetHeaderValue(h) == "value1") &&
                m.Headers.Any(h => h.Key == "original-header-2" && GetHeaderValue(h) == "value2") &&
                // Failure headers present
                m.Headers.Any(h => h.Key == "x-failure-timestamp" && !string.IsNullOrEmpty(GetHeaderValue(h))) &&
                m.Headers.Any(h => h.Key == "x-failure-reason" && GetHeaderValue(h) == testException.Message) &&
                m.Headers.Any(h => h.Key == "x-failure-stack-trace" && GetHeaderValue(h).Length <= 1024) &&
                m.Headers.Any(h => h.Key == "x-retry-attempts" && GetHeaderValue(h) == "3")
            ),
            It.IsAny<CancellationToken>()
        ), Times.Once);
    }

    [Fact]
    public async Task test_ac4_permanent_exception_routes_to_dlq_immediately()
    {
        // Arrange
        var testMessage = new ConsumeResult<string, string>
        {
            Message = new Message<string, string> { Key = "test-key", Value = "{ invalid-json }" }
        };
        var permanentException = new JsonException("Invalid JSON format"); // Permanent
        int processCallCount = 0;
        List<TimeSpan> observedDelays = new();

        _consumer.ProcessMessage = (msg, ct) =>
        {
            processCallCount++;
            throw permanentException;
        };
        _consumer.DelayFunction = (delay, ct) =>
        {
            observedDelays.Add(delay);
            return Task.CompletedTask;
        };

        // Act
        var result = await _consumer.ProcessMessageWithRetry(testMessage, CancellationToken.None);

        // Assert
        Assert.False(result);
        Assert.Equal(1, processCallCount); // No retries, only 1 attempt
        Assert.Empty(observedDelays); // No delays
        _mockDlqProducer.Verify(p => p.ProduceAsync(
            It.IsAny<string>(),
            It.Is<Message<string, string>>(m => 
                m.Headers.Any(h => h.Key == "x-retry-attempts" && GetHeaderValue(h) == "0")
            ),
            It.IsAny<CancellationToken>()
        ), Times.Once);
    }

    [Fact]
    public void test_ac5_configuration_reads_from_environment_with_defaults()
    {
        // Arrange: no environment variables set, use defaults
        var emptyConfig = new ConfigurationBuilder().Build();
        var testConsumer = new Consumer(emptyConfig, _mockConsumer.Object, _mockDlqProducer.Object);

        // Assert default values
        Assert.Equal(3, testConsumer.MaxRetryAttempts);
        Assert.Equal(1000, testConsumer.InitialRetryDelayMs);
        Assert.Equal(10000, testConsumer.MaxRetryDelayMs);
        Assert.Equal("accounting-service-dlq", testConsumer.DlqTopicName);

        // Arrange: with environment variables set
        var envConfig = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                {"KAFKA_CONSUMER_MAX_RETRY_ATTEMPTS", "5"},
                {"KAFKA_CONSUMER_INITIAL_RETRY_DELAY_MS", "500"},
                {"KAFKA_CONSUMER_MAX_RETRY_DELAY_MS", "8000"},
                {"KAFKA_CONSUMER_DLQ_TOPIC_NAME", "custom-dlq-topic"}
            }).Build();
        testConsumer = new Consumer(envConfig, _mockConsumer.Object, _mockDlqProducer.Object);

        // Assert configured values
        Assert.Equal(5, testConsumer.MaxRetryAttempts);
        Assert.Equal(500, testConsumer.InitialRetryDelayMs);
        Assert.Equal(8000, testConsumer.MaxRetryDelayMs);
        Assert.Equal("custom-dlq-topic", testConsumer.DlqTopicName);
    }

    [Fact]
    public async Task test_ac6_successfully_processed_messages_committed_no_duplicates()
    {
        // Arrange
        var testMessage = new ConsumeResult<string, string>
        {
            Message = new Message<string, string> { Key = "test-key", Value = "valid-value" },
            TopicPartitionOffset = new TopicPartitionOffset("test-topic", 0, 123)
        };
        int processCallCount = 0;
        _consumer.ProcessMessage = (msg, ct) =>
        {
            processCallCount++;
            return Task.CompletedTask; // Success on first attempt
        };

        // Act
        var result = await _consumer.ProcessMessageWithRetry(testMessage, CancellationToken.None);

        // Assert
        Assert.True(result); // Success, can be committed
        Assert.Equal(1, processCallCount); // No retries
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<string>(), It.IsAny<Message<string, string>>(), It.IsAny<CancellationToken>()), Times.Never);
        _mockConsumer.Verify(c => c.Commit(testMessage), Times.Once);
    }

    [Fact]
    public async Task test_ac7_dlq_messages_retain_all_original_properties()
    {
        // Arrange
        var originalHeaders = new Headers
        {
            {"traceparent", "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"u8.ToArray()},
            {"user-id", "user123"u8.ToArray()}
        };
        var testMessage = new ConsumeResult<string, string>
        {
            Message = new Message<string, string> 
            { 
                Key = "order-123", 
                Value = "{\"orderId\":123,\"amount\":99.99}",
                Headers = originalHeaders,
                Timestamp = new Timestamp(DateTime.UtcNow)
            }
        };
        _consumer.ProcessMessage = (msg, ct) => throw new JsonException("Invalid JSON");

        Message<string, string>? producedDlqMessage = null;
        _mockDlqProducer.Setup(p => p.ProduceAsync(It.IsAny<string>(), It.IsAny<Message<string, string>>(), It.IsAny<CancellationToken>()))
            .Callback<string, Message<string, string>, CancellationToken>((t, m, ct) => producedDlqMessage = m)
            .Returns(Task.FromResult(new DeliveryResult<string, string>()));

        // Act
        await _consumer.ProcessMessageWithRetry(testMessage, CancellationToken.None);

        // Assert
        Assert.NotNull(producedDlqMessage);
        Assert.Equal(testMessage.Message.Key, producedDlqMessage.Key);
        Assert.Equal(testMessage.Message.Value, producedDlqMessage.Value);
        Assert.Equal(testMessage.Message.Timestamp.UnixTimestampMs, producedDlqMessage.Timestamp.UnixTimestampMs);
        // Verify all original headers are present
        foreach (var originalHeader in originalHeaders)
        {
            Assert.True(producedDlqMessage.Headers.TryGetLastBytes(originalHeader.Key, out var headerValue));
            Assert.Equal(originalHeader.GetValueBytes(), headerValue);
        }
    }

    [Fact]
    public async Task test_ac8_consumer_continues_processing_after_failed_message()
    {
        // Arrange
        var message1 = new ConsumeResult<string, string> { Message = new Message<string, string> { Key = "msg1", Value = "invalid" }, Offset = 1 };
        var message2 = new ConsumeResult<string, string> { Message = new Message<string, string> { Key = "msg2", Value = "valid" }, Offset = 2 };
        
        int processCallCount = 0;
        _consumer.ProcessMessage = (msg, ct) =>
        {
            processCallCount++;
            if (msg.Key == "msg1") throw new JsonException("Permanent failure");
            return Task.CompletedTask;
        };

        // Act: process both messages sequentially
        var result1 = await _consumer.ProcessMessageWithRetry(message1, CancellationToken.None);
        var result2 = await _consumer.ProcessMessageWithRetry(message2, CancellationToken.None);

        // Assert
        Assert.False(result1);
        Assert.True(result2);
        Assert.Equal(2, processCallCount); // Both messages processed, no blocking
        // Verify both DLQ for message1 and commit for message2 happened
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<string>(), It.Is<Message<string, string>>(m => m.Key == "msg1"), It.IsAny<CancellationToken>()), Times.Once);
        _mockConsumer.Verify(c => c.Commit(message2), Times.Once);
    }

    private static string GetHeaderValue(IHeader header)
    {
        return System.Text.Encoding.UTF8.GetString(header.GetValueBytes());
    }
}
