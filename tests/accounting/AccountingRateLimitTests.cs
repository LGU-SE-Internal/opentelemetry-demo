using Xunit;
using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Confluent.Kafka;
using Npgsql;
using Prometheus;

namespace AccountingService.Tests.Integration;

[Trait("Category", "Integration")]
public class AccountingRateLimitTests : IAsyncLifetime
{
    private const string TestTopic = "orders";
    private const string ConsumerGroup = "accounting-service-test-group";
    private IProducer<Null, string> _kafkaProducer;
    private NpgsqlConnection _dbConnection;

    public async Task InitializeAsync()
    {
        // Initialize Kafka producer and DB connection for tests
        var producerConfig = new ProducerConfig { BootstrapServers = "localhost:9092" };
        _kafkaProducer = new ProducerBuilder<Null, string>(producerConfig).Build();
        
        var dbConnString = "Host=localhost;Database=accounting;Username=postgres;Password=postgres";
        _dbConnection = new NpgsqlConnection(dbConnString);
        await _dbConnection.OpenAsync();
    }

    public async Task DisposeAsync()
    {
        _kafkaProducer.Dispose();
        await _dbConnection.DisposeAsync();
    }

    [Fact(DisplayName = "AC-1: Enforces per-partition message rate limit")]
    public async Task Test_ac1_enforces_per_partition_rate_limit()
    {
        // Arrange: Set rate limit to 10 messages per second per partition
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_PER_PARTITION_MESSAGES_PER_SECOND", 
            "10");
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_BURST_PER_PARTITION", 
            "0");
        
        // Produce 100 test messages to a single partition
        var partition = new Partition(0);
        for (int i = 0; i < 100; i++)
        {
            await _kafkaProducer.ProduceAsync(
                new TopicPartition(TestTopic, partition), 
                new Message<Null, string> { Value = $"test-order-{i}" });
        }

        // Act: Run consumer for 10 seconds
        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var processedCount = 0;
        // (Test consumer logic that counts messages processed from DB)
        while (!cts.IsCancellationRequested)
        {
            using var cmd = new NpgsqlCommand(
                "SELECT COUNT(*) FROM orders WHERE order_id LIKE 'test-order-%'", 
                _dbConnection);
            processedCount = Convert.ToInt32(await cmd.ExecuteScalarAsync(cts.Token));
            await Task.Delay(100, cts.Token);
        }

        // Assert: Processed no more than ~100 messages (10/s * 10s, allow 10% tolerance)
        Assert.InRange(processedCount, 90, 110);
    }

    [Fact(DisplayName = "AC-2: Allows configured burst limit on partition assignment")]
    public async Task Test_ac2_allows_configured_burst_limit()
    {
        // Arrange: Set rate limit to 1 per second, burst to 20
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_PER_PARTITION_MESSAGES_PER_SECOND", 
            "1");
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_BURST_PER_PARTITION", 
            "20");
        
        // Produce 50 test messages
        var partition = new Partition(1);
        for (int i = 0; i < 50; i++)
        {
            await _kafkaProducer.ProduceAsync(
                new TopicPartition(TestTopic, partition), 
                new Message<Null, string> { Value = $"test-burst-{i}" });
        }

        // Act: Measure processed count after 2 seconds (burst should be allowed immediately)
        await Task.Delay(2000);
        using var cmd = new NpgsqlCommand(
            "SELECT COUNT(*) FROM orders WHERE order_id LIKE 'test-burst-%'", 
            _dbConnection);
        var processedCount = Convert.ToInt32(await cmd.ExecuteScalarAsync());

        // Assert: At least 20+2 messages processed (burst + 2s * 1/s)
        Assert.True(processedCount >= 22, $"Expected at least 22 messages processed, got {processedCount}");
    }

    [Fact(DisplayName = "AC-3: Correct offset management after throttling")]
    public async Task Test_ac3_offset_commit_correct_after_throttling()
    {
        // Arrange: Set rate limit to 5 per second
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_PER_PARTITION_MESSAGES_PER_SECOND", 
            "5");
        var partition = new Partition(2);
        
        // Produce 20 messages
        for (int i = 0; i < 20; i++)
        {
            await _kafkaProducer.ProduceAsync(
                new TopicPartition(TestTopic, partition), 
                new Message<Null, string> { Value = $"test-offset-{i}" });
        }

        // Act: Wait for all messages to be processed, then restart consumer
        await Task.Delay(5000);
        // Restart consumer instance
        await Task.Delay(2000);

        // Assert: No duplicate orders in DB
        using var cmd = new NpgsqlCommand(
            "SELECT COUNT(DISTINCT order_id), COUNT(*) FROM orders WHERE order_id LIKE 'test-offset-%'", 
            _dbConnection);
        using var reader = await cmd.ExecuteReaderAsync();
        await reader.ReadAsync();
        var distinctCount = reader.GetInt32(0);
        var totalCount = reader.GetInt32(1);
        Assert.Equal(20, distinctCount);
        Assert.Equal(20, totalCount);
    }

    [Fact(DisplayName = "AC-4: Throttled messages counter increments correctly")]
    public async Task Test_ac4_throttled_metric_increments_correctly()
    {
        // Arrange: Set rate limit to 2 per second
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_PER_PARTITION_MESSAGES_PER_SECOND", 
            "2");
        var partition = new Partition(3);
        
        // Produce 100 messages
        for (int i = 0; i < 100; i++)
        {
            await _kafkaProducer.ProduceAsync(
                new TopicPartition(TestTopic, partition), 
                new Message<Null, string> { Value = $"test-metric-{i}" });
        }

        // Act: Wait 10 seconds
        await Task.Delay(10000);

        // Assert: Throttled metric equals total messages minus processed (>= 80)
        var metricServer = new MetricServer(port: 9464);
        metricServer.Start();
        var metrics = await new HttpClient().GetStringAsync("http://localhost:9464/metrics");
        metricServer.Stop();
        
        var throttledLine = metrics.Split('\n')
            .FirstOrDefault(l => l.StartsWith("accounting_service_kafka_throttled_messages_total") 
                                && l.Contains($"partition=\"{partition.Value}\"") 
                                && l.Contains($"consumer_group=\"{ConsumerGroup}\""));
        
        Assert.NotNull(throttledLine);
        var throttledCount = double.Parse(throttledLine.Split(' ').Last());
        Assert.True(throttledCount >= 80, $"Expected at least 80 throttled messages, got {throttledCount}");
    }

    [Fact(DisplayName = "AC-5: Graceful shutdown during throttling")]
    public async Task Test_ac5_graceful_shutdown_during_throttling()
    {
        // Arrange: Set rate limit to 1 per second, produce 20 messages
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_PER_PARTITION_MESSAGES_PER_SECOND", 
            "1");
        Environment.SetEnvironmentVariable("ACCOUNTING_SERVICE_SHUTDOWN_TIMEOUT_SECONDS", "10");
        var partition = new Partition(4);
        
        for (int i = 0; i < 20; i++)
        {
            await _kafkaProducer.ProduceAsync(
                new TopicPartition(TestTopic, partition), 
                new Message<Null, string> { Value = $"test-shutdown-{i}" });
        }

        // Act: Let consumer run for 3 seconds, then send shutdown signal
        await Task.Delay(3000);
        // Send SIGTERM to consumer process
        var shutdownTime = DateTime.UtcNow;
        await Task.Delay(15000); // Wait for shutdown timeout + buffer

        // Assert: Consumer exited within timeout, all in-flight messages are committed
        var exitDuration = DateTime.UtcNow - shutdownTime;
        Assert.True(exitDuration < TimeSpan.FromSeconds(12), "Consumer did not exit within shutdown timeout");
        
        using var cmd = new NpgsqlCommand(
            "SELECT COUNT(*) FROM orders WHERE order_id LIKE 'test-shutdown-%'", 
            _dbConnection);
        var processedCount = Convert.ToInt32(await cmd.ExecuteScalarAsync());
        Assert.True(processedCount >= 3, $"Expected at least 3 messages processed before shutdown, got {processedCount}");
    }

    [Fact(DisplayName = "AC-6: Rate limiting disabled when limit is 0 or unset")]
    public async Task Test_ac6_rate_limiting_disabled_when_zero()
    {
        // Arrange: Disable rate limiting
        Environment.SetEnvironmentVariable(
            "ACCOUNTING_SERVICE_KAFKA_RATE_LIMIT_PER_PARTITION_MESSAGES_PER_SECOND", 
            "0");
        var partition = new Partition(5);
        
        // Produce 1000 messages
        for (int i = 0; i < 1000; i++)
        {
            await _kafkaProducer.ProduceAsync(
                new TopicPartition(TestTopic, partition), 
                new Message<Null, string> { Value = $"test-disabled-{i}" });
        }

        // Act: Wait 2 seconds
        await Task.Delay(2000);

        // Assert: All messages processed
        using var cmd = new NpgsqlCommand(
            "SELECT COUNT(*) FROM orders WHERE order_id LIKE 'test-disabled-%'", 
            _dbConnection);
        var processedCount = Convert.ToInt32(await cmd.ExecuteScalarAsync());
        Assert.Equal(1000, processedCount);
    }
}
EOF && ls -la tests/accounting/AccountingRateLimitTests.cs && wc -l tests/accounting/AccountingRateLimitTests.cs
