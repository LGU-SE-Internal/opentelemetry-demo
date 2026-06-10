using Xunit;
using Microsoft.Extensions.Logging;
using OpenTelemetry.Trace;
using OpenTelemetry.Logs;
using System.Diagnostics;
using AccountingService;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Extensions.Configuration;

namespace AccountingService.Tests;

public class LoggingACTests
{
    [Fact]
    public void test_ac1_no_console_write_calls()
    {
        // AC-1: Scanning the accounting service codebase finds zero occurrences of Console.WriteLine or Console.Write calls
        // Arrange: Get all .cs files in accounting service source directory
        var sourceDir = Path.Combine(Directory.GetCurrentDirectory(), "..", "..", "..", "..", "src", "accounting");
        var csFiles = Directory.GetFiles(sourceDir, "*.cs", SearchOption.AllDirectories);

        // Act: Scan each file for Console.Write/Console.WriteLine
        var consoleCalls = new List<string>();
        foreach (var file in csFiles)
        {
            var content = File.ReadAllText(file);
            if (content.Contains("Console.WriteLine") || content.Contains("Console.Write"))
            {
                consoleCalls.Add(file);
            }
        }

        // Assert: No console calls found
        Assert.Empty(consoleCalls);
    }

    [Fact]
    public void test_ac2_logs_include_trace_and_span_ids_in_trace_context()
    {
        // AC-2: When executing any operation in active trace, 100% of logs include valid TraceId and SpanId
        // Arrange: Setup OpenTelemetry logger with in-memory exporter
        var logRecords = new List<LogRecord>();
        using var tracerProvider = Sdk.CreateTracerProviderBuilder()
            .AddSource("AccountingService")
            .Build();

        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options =>
            {
                options.AddInMemoryExporter(logRecords);
            });
        });

        var logger = loggerFactory.CreateLogger<Consumer>();
        var testTraceId = ActivityTraceId.CreateRandom();
        var testSpanId = ActivitySpanId.CreateRandom();

        // Act: Start an activity (trace context) and generate a log
        using var activity = new Activity("TestOperation")
            .SetTraceId(testTraceId)
            .SetSpanId(testSpanId)
            .Start();

        logger.LogInformation("Test log in trace context");

        activity.Stop();

        // Assert: All logs have matching TraceId and SpanId
        Assert.NotEmpty(logRecords);
        foreach (var log in logRecords)
        {
            Assert.Equal(testTraceId.ToHexString(), log.TraceId?.ToHexString());
            Assert.Equal(testSpanId.ToHexString(), log.SpanId?.ToHexString());
        }
    }

    [Fact]
    public void test_ac3_original_log_messages_preserved()
    {
        // AC-3: All original message text from legacy Console.WriteLine calls is present exactly in structured log Message field
        // Arrange: Get legacy messages from existing Console calls (we check the original source for expected messages)
        // Note: This test compares against the known legacy messages from the original implementation
        var sourceDir = Path.Combine(Directory.GetCurrentDirectory(), "..", "..", "..", "..", "src", "accounting");
        var consumerContent = File.ReadAllText(Path.Combine(sourceDir, "Consumer.cs"));
        var programContent = File.ReadAllText(Path.Combine(sourceDir, "Program.cs"));

        // Extract original Console.WriteLine messages
        var originalMessages = new List<string>();
        foreach (var line in consumerContent.Split('\n').Concat(programContent.Split('\n')))
        {
            var trimmedLine = line.TrimStart();
            if (trimmedLine.StartsWith("Console.WriteLine"))
            {
                var startIdx = trimmedLine.IndexOf('"') + 1;
                var endIdx = trimmedLine.LastIndexOf('"');
                if (startIdx > 0 && endIdx > startIdx)
                {
                    var message = trimmedLine.Substring(startIdx, endIdx - startIdx);
                    originalMessages.Add(message);
                }
            }
        }

        // Act: Capture all structured log messages from service operations
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options =>
            {
                options.AddInMemoryExporter(logRecords);
            });
        });

        // Simulate service operations that generate all expected logs
        // (implementation will trigger all original log cases)

        // Assert: All original messages are present exactly in log records
        var loggedMessages = logRecords.Select(l => l.Message).ToList();
        foreach (var originalMsg in originalMessages)
        {
            Assert.Contains(originalMsg, loggedMessages);
        }
    }

    [Fact]
    public void test_ac4_order_processing_logs_include_order_id()
    {
        // AC-4: Log entries for order processing operations include OrderId property
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger<Consumer>();
        var testOrderId = "test-order-12345";

        // Act: Process an order with the test order ID
        // (Consumer processing will generate order processing logs)
        var order = new Order { OrderId = testOrderId };
        // Call processing method that logs order processing

        // Assert: Order processing logs have OrderId property with correct value
        var orderLogs = logRecords.Where(l => l.Message.Contains("order") || l.Message.Contains("processing"));
        Assert.NotEmpty(orderLogs);
        foreach (var log in orderLogs)
        {
            Assert.True(log.Attributes.Any(a => a.Key == "OrderId" && a.Value?.ToString() == testOrderId));
        }
    }

    [Fact]
    public void test_ac5_kafka_consumer_logs_include_kafka_offset()
    {
        // AC-5: Log entries for Kafka consumer operations include KafkaOffset property
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger<Consumer>();
        var testOffset = 123456L;

        // Act: Consume a Kafka message with the test offset
        // (consumer processing will generate Kafka operation logs)

        // Assert: Kafka operation logs have KafkaOffset property with correct value
        var kafkaLogs = logRecords.Where(l => l.Message.Contains("Kafka") || l.Message.Contains("consume") || l.Message.Contains("offset"));
        Assert.NotEmpty(kafkaLogs);
        foreach (var log in kafkaLogs)
        {
            Assert.True(log.Attributes.Any(a => a.Key == "KafkaOffset" && Convert.ToInt64(a.Value) == testOffset));
        }
    }

    [Fact]
    public void test_ac6_log_level_warning_emits_only_warning_and_above()
    {
        // AC-6: Setting ASPNETCORE_LOGGING__LOGLEVEL__AccountingService to Warning results in only Warning, Error, Critical logs
        // Arrange
        Environment.SetEnvironmentVariable("ASPNETCORE_LOGGING__LOGLEVEL__AccountingService", "Warning");
        var logRecords = new List<LogRecord>();

        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddConfiguration(new ConfigurationBuilder()
                .AddEnvironmentVariables()
                .Build());
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });

        var logger = loggerFactory.CreateLogger<Consumer>();

        // Act: Emit logs at all levels
        logger.LogDebug("Debug test");
        logger.LogInformation("Info test");
        logger.LogWarning("Warning test");
        logger.LogError("Error test");
        logger.LogCritical("Critical test");

        // Assert: Only Warning, Error, Critical logs are present
        Assert.Equal(3, logRecords.Count);
        Assert.All(logRecords, l => 
            l.LogLevel is LogLevel.Warning or LogLevel.Error or LogLevel.Critical);
        
        Environment.SetEnvironmentVariable("ASPNETCORE_LOGGING__LOGLEVEL__AccountingService", null);
    }

    [Fact]
    public void test_ac7_log_level_debug_emits_all_logs()
    {
        // AC-7: Setting ASPNETCORE_LOGGING__LOGLEVEL__AccountingService to Debug results in all levels emitted
        // Arrange
        Environment.SetEnvironmentVariable("ASPNETCORE_LOGGING__LOGLEVEL__AccountingService", "Debug");
        var logRecords = new List<LogRecord>();

        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddConfiguration(new ConfigurationBuilder()
                .AddEnvironmentVariables()
                .Build());
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });

        var logger = loggerFactory.CreateLogger<Consumer>();

        // Act: Emit logs at all levels
        logger.LogDebug("Debug test");
        logger.LogInformation("Info test");
        logger.LogWarning("Warning test");
        logger.LogError("Error test");
        logger.LogCritical("Critical test");

        // Assert: All log levels are present
        Assert.Equal(5, logRecords.Count);
        Assert.Contains(logRecords, l => l.LogLevel == LogLevel.Debug);
        Assert.Contains(logRecords, l => l.LogLevel == LogLevel.Information);
        Assert.Contains(logRecords, l => l.LogLevel == LogLevel.Warning);
        Assert.Contains(logRecords, l => l.LogLevel == LogLevel.Error);
        Assert.Contains(logRecords, l => l.LogLevel == LogLevel.Critical);
        
        Environment.SetEnvironmentVariable("ASPNETCORE_LOGGING__LOGLEVEL__AccountingService", null);
    }

    [Fact]
    public void test_ac8_logs_compatible_with_otel_collector_schema()
    {
        // AC-8: Log entries are compatible with existing OpenTelemetry collector ingestion pipeline
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options =>
            {
                options.SetResourceBuilder(OpenTelemetry.Resources.ResourceBuilder.CreateDefault()
                    .AddService("accounting-service"));
                options.AddInMemoryExporter(logRecords);
            });
        });
        var logger = loggerFactory.CreateLogger<Program>();

        // Act: Generate a log entry
        logger.LogInformation("Test log for collector compatibility");

        // Assert: Log follows required schema
        var log = logRecords.First();
        Assert.NotNull(log.Timestamp);
        Assert.True(log.Timestamp.Kind == DateTimeKind.Utc);
        Assert.NotNull(log.SeverityText);
        Assert.NotNull(log.Message);
        var serviceName = log.Resource.Attributes.First(a => a.Key == "service.name").Value?.ToString();
        Assert.Equal("accounting-service", serviceName);
    }
}
