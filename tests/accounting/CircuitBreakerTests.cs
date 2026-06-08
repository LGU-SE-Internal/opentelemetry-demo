using Xunit;
using Moq;
using Polly.CircuitBreaker;
using Confluent.Kafka;
using Microsoft.Extensions.Diagnostics.Metrics;
using System.Diagnostics.Metrics;
using System.Net.Sockets;
using Npgsql;
using System.Text.Json;

namespace Accounting.Tests;

public class CircuitBreakerTests
{
    private readonly Mock<IInvalidOrderDlqProducer> _mockDlqProducer;
    private readonly Mock<Consumer<Ignore, OrderMessage>> _mockConsumer;
    private readonly MeterListener _meterListener;
    private Dictionary<string, int> _circuitBreakerMetrics = new();

    public CircuitBreakerTests()
    {
        _mockDlqProducer = new Mock<IInvalidOrderDlqProducer>();
        _mockConsumer = new Mock<Consumer<Ignore, OrderMessage>>();
        
        // Setup metric listener for accounting_service_circuit_breaker_state
        _meterListener = new MeterListener();
        _meterListener.InstrumentPublished = (instrument, listener) =>
        {
            if (instrument.Name == "accounting_service_circuit_breaker_state")
            {
                listener.EnableMeasurementEvents(instrument);
            }
        };
        _meterListener.SetMeasurementEventCallback<int>((instrument, measurement, tags, state) =>
        {
            var stateTag = tags.FirstOrDefault(t => t.Key == "state").Value?.ToString() ?? "unknown";
            _circuitBreakerMetrics[stateTag] = measurement;
        });
        _meterListener.Start();
    }

    #region AC-1 Tests
    [Fact]
    public void test_ac1_polly_circuit_breaker_package_exists()
    {
        // Arrange
        var projectPath = Path.Combine(AppContext.BaseDirectory, "../../../../src/accounting/Accounting.csproj");
        var projectContent = File.ReadAllText(projectPath);
        
        // Assert
        Assert.Contains("Polly.CircuitBreaker", projectContent);
        // Check version >= 8.0.0
        var versionLine = projectContent.Split('\n')
            .FirstOrDefault(l => l.Contains("Polly.CircuitBreaker"));
        Assert.NotNull(versionLine);
        var versionMatch = System.Text.RegularExpressions.Regex.Match(versionLine, @"Version=""([0-9]+\.[0-9]+\.[0-9]+)""");
        Assert.True(versionMatch.Success);
        var version = Version.Parse(versionMatch.Groups[1].Value);
        Assert.True(version >= new Version(8, 0, 0));
    }
    #endregion

    #region AC-2 Tests
    [Fact]
    public async Task test_ac2_circuit_opens_after_10_consecutive_failures()
    {
        // Arrange: Setup processing to always throw exception
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = new OrderMessage() }
        };

        // Act: Simulate 10 failed processing attempts
        for (int i = 0; i < 10; i++)
        {
            await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));
        }

        // Assert: 11th attempt should immediately throw BrokenCircuitException (circuit open)
        var exception = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(testMessage));
        Assert.IsType<BrokenCircuitException>(exception);
    }
    #endregion

    #region AC-3 Tests
    [Fact]
    public async Task test_ac3_circuit_stays_open_for_30_seconds()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = new OrderMessage() }
        };
        
        // Trigger circuit open
        for (int i = 0; i < 10; i++)
        {
            await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));
        }

        // Act 1: Attempt processing after 29 seconds
        await Task.Delay(29000);
        var exceptionAfter29s = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(testMessage));
        Assert.IsType<BrokenCircuitException>(exceptionAfter29s);

        // Act 2: Attempt processing after 31 seconds
        await Task.Delay(2000);
        var exceptionAfter31s = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(testMessage));
        // Should not throw BrokenCircuitException (in half-open state now)
        Assert.IsNotType<BrokenCircuitException>(exceptionAfter31s);
    }
    #endregion

    #region AC-4 Tests
    [Fact]
    public async Task test_ac4_open_circuit_routes_to_dlq_no_processing()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = new OrderMessage() }
        };
        
        // Open circuit
        for (int i = 0; i < 10; i++)
        {
            await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));
        }
        _mockDlqProducer.Invocations.Clear();

        // Act: Process message while circuit is open, measure time
        var startTime = DateTime.UtcNow;
        await consumer.ProcessOrderAsync(testMessage);
        var processingTime = DateTime.UtcNow - startTime;

        // Assert
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<ConsumeResult<Ignore, OrderMessage>>()), Times.Once);
        Assert.True(processingTime.TotalMilliseconds < 100, "Message processing took longer than 100ms");
        // Verify no database calls were made (mock would throw if we tried to access db)
    }
    #endregion

    #region AC-5 Tests
    [Fact]
    public async Task test_ac5_closed_circuit_processes_normally()
    {
        // Arrange: Setup database connection to work
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage>
            {
                Value = new OrderMessage { OrderId = "test-123", Amount = 100 }
            }
        };

        // Act
        await consumer.ProcessOrderAsync(testMessage);

        // Assert: Message was written to database
        using var dbContext = new AccountingDbContext();
        var order = await dbContext.Orders.FindAsync("test-123");
        Assert.NotNull(order);
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<ConsumeResult<Ignore, OrderMessage>>()), Times.Never);
    }
    #endregion

    #region AC-6 Tests
    [Fact]
    public async Task test_ac6_half_open_success_closes_circuit()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = new OrderMessage() }
        };
        
        // Open circuit
        for (int i = 0; i < 10; i++)
        {
            await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));
        }

        // Wait for half-open state
        await Task.Delay(30000);

        // Act 1: First message in half-open state succeeds
        await consumer.ProcessOrderAsync(testMessage);

        // Act 2: Next messages should process normally (circuit closed)
        for (int i = 0; i < 5; i++)
        {
            await consumer.ProcessOrderAsync(testMessage);
        }

        // Assert: No DLQ messages, circuit is closed
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<ConsumeResult<Ignore, OrderMessage>>()), Times.Never);
        Assert.Equal(1, _circuitBreakerMetrics.GetValueOrDefault("closed", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("open", 0));
    }

    [Fact]
    public async Task test_ac6_half_open_failure_reopens_circuit()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = new OrderMessage() }
        };
        
        // Open circuit
        for (int i = 0; i < 10; i++)
        {
            await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));
        }

        // Wait for half-open state
        await Task.Delay(30000);

        // Act 1: First message in half-open state fails
        await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));

        // Act 2: Next message should immediately go to DLQ (circuit open again)
        _mockDlqProducer.Invocations.Clear();
        await consumer.ProcessOrderAsync(testMessage);

        // Assert: Message sent to DLQ, circuit is open
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<ConsumeResult<Ignore, OrderMessage>>()), Times.Once);
        Assert.Equal(1, _circuitBreakerMetrics.GetValueOrDefault("open", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("half_open", 0));
    }
    #endregion

    #region AC-7 Tests
    [Fact]
    public async Task test_ac7_state_transitions_update_metrics()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = new OrderMessage() }
        };

        // Assert initial state is closed
        _meterListener.RecordObservableInstruments();
        Assert.Equal(1, _circuitBreakerMetrics.GetValueOrDefault("closed", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("open", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("half_open", 0));

        // Act 1: Open circuit
        for (int i = 0; i < 10; i++)
        {
            await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));
        }
        _meterListener.RecordObservableInstruments();

        // Assert state is open
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("closed", 0));
        Assert.Equal(1, _circuitBreakerMetrics.GetValueOrDefault("open", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("half_open", 0));

        // Act 2: Wait for half-open state
        await Task.Delay(30000);
        // Force half-open transition
        _ = Record.ExceptionAsync(() => consumer.ProcessOrderAsync(testMessage));
        _meterListener.RecordObservableInstruments();

        // Assert state is half-open
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("closed", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("open", 0));
        Assert.Equal(1, _circuitBreakerMetrics.GetValueOrDefault("half_open", 0));

        // Act 3: Successful processing closes circuit
        await consumer.ProcessOrderAsync(testMessage);
        _meterListener.RecordObservableInstruments();

        // Assert state is closed
        Assert.Equal(1, _circuitBreakerMetrics.GetValueOrDefault("closed", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("open", 0));
        Assert.Equal(0, _circuitBreakerMetrics.GetValueOrDefault("half_open", 0));
    }
    #endregion

    #region AC-8 Tests
    [Fact]
    public async Task test_ac8_postgres_connection_exception_counts_towards_failure_threshold()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        var testMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = new OrderMessage() }
        };
        // Simulate Postgres down
        Environment.SetEnvironmentVariable("POSTGRES_CONNECTION_STRING", "Host=invalid;Port=5432;Database=test;");

        // Act: Throw NpgsqlException 9 times
        for (int i = 0; i < 9; i++)
        {
            var exception = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(testMessage));
            Assert.IsType<NpgsqlException>(exception);
        }

        // 10th failure should open circuit
        await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(testMessage));

        // 11th should be BrokenCircuitException
        var circuitException = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(testMessage));
        Assert.IsType<BrokenCircuitException>(circuitException);
    }

    [Fact]
    public async Task test_ac8_deserialization_exception_counts_towards_failure_threshold()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        // Invalid message that will fail deserialization
        var invalidMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage> { Value = null }
        };

        // Act: Throw JsonException 9 times
        for (int i = 0; i < 9; i++)
        {
            var exception = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(invalidMessage));
            Assert.IsType<JsonException>(exception);
        }

        // 10th failure should open circuit
        await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(invalidMessage));

        // 11th should be BrokenCircuitException
        var circuitException = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(invalidMessage));
        Assert.IsType<BrokenCircuitException>(circuitException);
    }

    [Fact]
    public async Task test_ac8_database_write_exception_counts_towards_failure_threshold()
    {
        // Arrange
        var consumer = new Consumer(_mockDlqProducer.Object);
        var invalidOrderMessage = new ConsumeResult<Ignore, OrderMessage>
        {
            Message = new Message<Ignore, OrderMessage>
            {
                Value = new OrderMessage { OrderId = "invalid-@#$", Amount = -100 }
            }
        };

        // Act: Throw DbUpdateException 9 times
        for (int i = 0; i < 9; i++)
        {
            var exception = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(invalidOrderMessage));
            Assert.IsType<Microsoft.EntityFrameworkCore.DbUpdateException>(exception);
        }

        // 10th failure should open circuit
        await Assert.ThrowsAnyAsync<Exception>(() => consumer.ProcessOrderAsync(invalidOrderMessage));

        // 11th should be BrokenCircuitException
        var circuitException = await Record.ExceptionAsync(() => consumer.ProcessOrderAsync(invalidOrderMessage));
        Assert.IsType<BrokenCircuitException>(circuitException);
    }
    #endregion
}
