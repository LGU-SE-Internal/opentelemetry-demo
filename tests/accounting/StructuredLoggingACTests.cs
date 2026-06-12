using Xunit;
using Microsoft.Extensions.Logging;
using OpenTelemetry.Trace;
using OpenTelemetry.Logs;
using System.Diagnostics;
using AccountingService;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;

namespace AccountingService.Tests;

public class StructuredLoggingACTests
{
    // AC-1: All 8 structured logging methods present in Log.cs following OpenTelemetry ILogger pattern
    [Fact]
    public void test_ac1_all_log_methods_exist_in_log_class()
    {
        // Arrange
        var logClassType = typeof(Log);
        var expectedMethods = new List<string>
        {
            "KafkaConnectionFailed",
            "KafkaMessageConsumeError",
            "DatabaseWriteSuccess",
            "DatabaseWriteFailed",
            "OrderValidationFailed",
            "DlqMessageProduced",
            "GracefulShutdownStarted",
            "GracefulShutdownCompleted"
        };

        // Act & Assert
        foreach (var methodName in expectedMethods)
        {
            var method = logClassType.GetMethod(methodName, BindingFlags.Public | BindingFlags.Static);
            Assert.NotNull(method);
        }
    }

    // AC-2: Kafka connection failure events trigger KafkaConnectionFailed log with broker address and exception
    [Fact]
    public void test_ac2_kafka_connection_failed_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testBroker = "kafka:9092";
        var testException = new System.Exception("Connection timed out");

        // Act
        Log.KafkaConnectionFailed(logger, testBroker, testException);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Error && l.Message.Contains("Kafka connection failed"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "brokerAddress" && a.Value?.ToString() == testBroker));
        Assert.Equal(testException, log.Exception);
    }

    // AC-3: Kafka message consumption failure triggers KafkaMessageConsumeError log with topic, partition, offset, exception
    [Fact]
    public void test_ac3_kafka_message_consume_error_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testTopic = "orders";
        var testPartition = 3;
        var testOffset = 123456L;
        var testException = new System.Exception("Deserialization failed");

        // Act
        Log.KafkaMessageConsumeError(logger, testTopic, testPartition, testOffset, testException);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Error && l.Message.Contains("Kafka message consume error"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "topic" && a.Value?.ToString() == testTopic));
        Assert.True(log.Attributes.Any(a => a.Key == "partition" && (int)a.Value == testPartition));
        Assert.True(log.Attributes.Any(a => a.Key == "offset" && (long)a.Value == testOffset));
        Assert.Equal(testException, log.Exception);
    }

    // AC-4: Successful database write triggers DatabaseWriteSuccess log with operation type, order ID, customer ID
    [Fact]
    public void test_ac4_database_write_success_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testOperation = "InsertOrder";
        var testOrderId = System.Guid.NewGuid();
        var testCustomerId = System.Guid.NewGuid();

        // Act
        Log.DatabaseWriteSuccess(logger, testOperation, testOrderId, testCustomerId);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Information && l.Message.Contains("Database write success"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "operationType" && a.Value?.ToString() == testOperation));
        Assert.True(log.Attributes.Any(a => a.Key == "orderId" && (System.Guid)a.Value == testOrderId));
        Assert.True(log.Attributes.Any(a => a.Key == "customerId" && (System.Guid)a.Value == testCustomerId));
    }

    // AC-5: Failed database write triggers DatabaseWriteFailed log with operation type, order ID, customer ID, exception
    [Fact]
    public void test_ac5_database_write_failed_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testOperation = "UpdateInvoice";
        var testOrderId = System.Guid.NewGuid();
        var testCustomerId = System.Guid.NewGuid();
        var testException = new System.Exception("Unique constraint violation");

        // Act
        Log.DatabaseWriteFailed(logger, testOperation, testOrderId, testCustomerId, testException);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Error && l.Message.Contains("Database write failed"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "operationType" && a.Value?.ToString() == testOperation));
        Assert.True(log.Attributes.Any(a => a.Key == "orderId" && (System.Guid)a.Value == testOrderId));
        Assert.True(log.Attributes.Any(a => a.Key == "customerId" && (System.Guid)a.Value == testCustomerId));
        Assert.Equal(testException, log.Exception);
    }

    // AC-6: Order validation failure triggers OrderValidationFailed log with order ID, customer ID, validation error, correlation ID
    [Fact]
    public void test_ac6_order_validation_failed_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testOrderId = System.Guid.NewGuid();
        var testCustomerId = System.Guid.NewGuid();
        var testValidationError = "Order total cannot be negative";
        var testCorrelationId = "corr-12345abc";

        // Act
        Log.OrderValidationFailed(logger, testOrderId, testCustomerId, testValidationError, testCorrelationId);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Warning && l.Message.Contains("Order validation failed"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "orderId" && (System.Guid)a.Value == testOrderId));
        Assert.True(log.Attributes.Any(a => a.Key == "customerId" && (System.Guid)a.Value == testCustomerId));
        Assert.True(log.Attributes.Any(a => a.Key == "validationError" && a.Value?.ToString() == testValidationError));
        Assert.True(log.Attributes.Any(a => a.Key == "correlationId" && a.Value?.ToString() == testCorrelationId));
    }

    // AC-7: DLQ message production triggers DlqMessageProduced log with DLQ topic, original order ID, failure reason, correlation ID
    [Fact]
    public void test_ac7_dlq_message_produced_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testDlqTopic = "orders-dlq";
        var testOrderId = System.Guid.NewGuid();
        var testFailureReason = "Validation failed after 3 retries";
        var testCorrelationId = "corr-67890def";

        // Act
        Log.DlqMessageProduced(logger, testDlqTopic, testOrderId, testFailureReason, testCorrelationId);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Warning && l.Message.Contains("DLQ message produced"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "dlqTopic" && a.Value?.ToString() == testDlqTopic));
        Assert.True(log.Attributes.Any(a => a.Key == "originalOrderId" && (System.Guid)a.Value == testOrderId));
        Assert.True(log.Attributes.Any(a => a.Key == "failureReason" && a.Value?.ToString() == testFailureReason));
        Assert.True(log.Attributes.Any(a => a.Key == "correlationId" && a.Value?.ToString() == testCorrelationId));
    }

    // AC-8: Graceful shutdown start triggers GracefulShutdownStarted log with start timestamp
    [Fact]
    public void test_ac8_graceful_shutdown_started_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testStartTime = System.DateTimeOffset.UtcNow;

        // Act
        Log.GracefulShutdownStarted(logger, testStartTime);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Information && l.Message.Contains("Graceful shutdown started"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "startTime" && (System.DateTimeOffset)a.Value == testStartTime));
    }

    // AC-9: Graceful shutdown completion triggers GracefulShutdownCompleted log with completion timestamp and duration
    [Fact]
    public void test_ac9_graceful_shutdown_completed_log_triggered()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testCompleteTime = System.DateTimeOffset.UtcNow;
        var testDuration = System.TimeSpan.FromSeconds(12.5);

        // Act
        Log.GracefulShutdownCompleted(logger, testCompleteTime, testDuration);

        // Assert
        var log = logRecords.FirstOrDefault(l => l.LogLevel == LogLevel.Information && l.Message.Contains("Graceful shutdown completed"));
        Assert.NotNull(log);
        Assert.True(log.Attributes.Any(a => a.Key == "completeTime" && (System.DateTimeOffset)a.Value == testCompleteTime));
        Assert.True(log.Attributes.Any(a => a.Key == "duration" && (System.TimeSpan)a.Value == testDuration));
    }

    // AC-10: No unstructured Console.WriteLine/Console.Write calls exist in accounting service
    [Fact]
    public void test_ac10_no_unstructured_console_calls()
    {
        // Arrange
        var sourceDir = System.IO.Path.Combine(System.IO.Directory.GetCurrentDirectory(), "..", "..", "..", "..", "src", "accounting");
        var csFiles = System.IO.Directory.GetFiles(sourceDir, "*.cs", System.IO.SearchOption.AllDirectories);

        // Act
        var consoleCalls = new List<string>();
        foreach (var file in csFiles)
        {
            var content = System.IO.File.ReadAllText(file);
            if (content.Contains("Console.WriteLine") || content.Contains("Console.Write"))
            {
                consoleCalls.Add(file);
            }
        }

        // Assert
        Assert.Empty(consoleCalls);
    }

    // AC-11: All log events include trace ID and span ID context automatically
    [Fact]
    public void test_ac11_logs_include_trace_and_span_ids()
    {
        // Arrange
        var logRecords = new List<LogRecord>();
        using var tracerProvider = Sdk.CreateTracerProviderBuilder()
            .AddSource("AccountingService")
            .Build();
        using var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddOpenTelemetry(options => options.AddInMemoryExporter(logRecords));
        });
        var logger = loggerFactory.CreateLogger("AccountingService.Log");
        var testTraceId = ActivityTraceId.CreateRandom();
        var testSpanId = ActivitySpanId.CreateRandom();

        // Act
        using var activity = new Activity("TestLogOperation")
            .SetTraceId(testTraceId)
            .SetSpanId(testSpanId)
            .Start();

        // Test with one of our log methods
        Log.DatabaseWriteSuccess(logger, "TestOperation", System.Guid.NewGuid(), System.Guid.NewGuid());

        activity.Stop();

        // Assert
        var log = logRecords.First();
        Assert.Equal(testTraceId.ToHexString(), log.TraceId?.ToHexString());
        Assert.Equal(testSpanId.ToHexString(), log.SpanId?.ToHexString());
    }
}
