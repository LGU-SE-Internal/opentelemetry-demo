using Xunit;
using Moq;
using Confluent.Kafka;
using Oteldemo;
using System.Text;
using Google.Protobuf;

public class OrderMessageValidationTests
{
    private readonly Mock<IOrderMessageValidator> _mockValidator;
    private readonly Mock<IInvalidOrderDlqProducer> _mockDlqProducer;
    private readonly Mock<IConsumer<Ignore, byte[]>> _mockConsumer;
    private readonly AccountingServiceConsumer _consumer; // Assume this is the main consumer class

    public OrderMessageValidationTests()
    {
        _mockValidator = new Mock<IOrderMessageValidator>();
        _mockDlqProducer = new Mock<IInvalidOrderDlqProducer>();
        _mockConsumer = new Mock<IConsumer<Ignore, byte[]>>();
        _consumer = new AccountingServiceConsumer(_mockValidator.Object, _mockDlqProducer.Object, _mockConsumer.Object);
    }

    [Fact]
    public async Task test_ac1_valid_order_message_processes_normally()
    {
        // Arrange: Create valid order proto payload
        var validOrder = new Order
        {
            OrderId = Guid.NewGuid().ToString(),
            UserId = Guid.NewGuid().ToString(),
            Items =
            {
                new OrderItem
                {
                    ItemId = "item1",
                    Quantity = 2,
                    UnitPrice = 10.00m
                }
            },
            TotalCost = 25.00m,
            ShippingCost = 5.00m,
            ShippingAddress = new Address
            {
                Street1 = "123 Test St",
                City = "Test City",
                Country = "US",
                ZipCode = "12345"
            }
        };
        byte[] payload = validOrder.ToByteArray();
        var metadata = new KafkaMessageMetadata
        {
            Topic = "orders",
            Partition = 0,
            Offset = 123,
            Timestamp = DateTime.UtcNow,
            MessageKey = "test-key"
        };
        
        _mockValidator.Setup(v => v.Validate(payload, It.IsAny<KafkaMessageMetadata>()))
            .Returns(new ValidationResult<Order>
            {
                IsValid = true,
                ValidPayload = validOrder,
                MessageMetadata = metadata
            });

        // Act: Consume message
        await _consumer.ProcessSingleMessage(payload, metadata);

        // Assert: Valid message is processed, no DLQ call
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<byte[]>(), It.IsAny<ValidationResult<Order>>()), Times.Never);
        // Verify that downstream processing was called (e.g. persist to DB)
        // (Assume consumer has a method to persist orders, verify it was called)
        Assert.True(_consumer.LastProcessedOrder?.OrderId == validOrder.OrderId);
    }

    [Fact]
    public async Task test_ac2_malformed_payload_routed_to_dlq()
    {
        // Arrange: Create invalid non-protobuf payload
        byte[] invalidPayload = Encoding.UTF8.GetBytes("this is not a protobuf message");
        var metadata = new KafkaMessageMetadata
        {
            Topic = "orders",
            Partition = 0,
            Offset = 456,
            Timestamp = DateTime.UtcNow,
            MessageKey = "bad-key"
        };
        var expectedResult = new ValidationResult<Order>
        {
            IsValid = false,
            ValidationErrors = { "Failed to deserialize payload to Order proto" },
            MessageMetadata = metadata
        };
        _mockValidator.Setup(v => v.Validate(invalidPayload, It.IsAny<KafkaMessageMetadata>()))
            .Returns(expectedResult);

        // Act: Process message
        await _consumer.ProcessSingleMessage(invalidPayload, metadata);

        // Assert: DLQ called with correct error, no exception thrown
        _mockDlqProducer.Verify(p => p.ProduceAsync(invalidPayload, 
            It.Is<ValidationResult<Order>>(r => 
                r.IsValid == false && 
                r.ValidationErrors.Any(e => e.Contains(OrderValidationErrorCode.PayloadDeserializationFailed.ToString()))
            )), Times.Once);
        // Verify consumer does not crash
        Assert.True(_consumer.IsRunning);
    }

    [Fact]
    public async Task test_ac3_missing_required_field_routed_to_dlq()
    {
        // Arrange: Create order missing total_cost field
        var invalidOrder = new Order
        {
            OrderId = Guid.NewGuid().ToString(),
            UserId = Guid.NewGuid().ToString(),
            Items =
            {
                new OrderItem { ItemId = "item1", Quantity = 1, UnitPrice = 5.00m }
            },
            // Missing TotalCost
            ShippingCost = 3.00m,
            ShippingAddress = new Address { Street1 = "123 Test St", City = "Test", Country = "US", ZipCode = "12345" }
        };
        byte[] payload = invalidOrder.ToByteArray();
        var metadata = new KafkaMessageMetadata { Topic = "orders", Partition = 1, Offset = 789 };
        _mockValidator.Setup(v => v.Validate(payload, It.IsAny<KafkaMessageMetadata>()))
            .Returns(new ValidationResult<Order>
            {
                IsValid = false,
                ValidationErrors = { $"{OrderValidationErrorCode.MissingRequiredField}: total_cost" },
                MessageMetadata = metadata
            });

        // Act
        await _consumer.ProcessSingleMessage(payload, metadata);

        // Assert
        _mockDlqProducer.Verify(p => p.ProduceAsync(payload, 
            It.Is<ValidationResult<Order>>(r => 
                r.ValidationErrors.Any(e => e.Contains("MissingRequiredField") && e.Contains("total_cost"))
            )), Times.Once);
        Assert.True(_consumer.IsRunning);
    }

    [Fact]
    public async Task test_ac4_negative_numeric_value_routed_to_dlq()
    {
        // Arrange: Order with negative shipping cost
        var invalidOrder = new Order
        {
            OrderId = Guid.NewGuid().ToString(),
            UserId = Guid.NewGuid().ToString(),
            Items = { new OrderItem { ItemId = "item1", Quantity = 2, UnitPrice = 10.00m } },
            TotalCost = 15.00m,
            ShippingCost = -5.00m, // Negative value
            ShippingAddress = new Address { Street1 = "123 Test St", City = "Test", Country = "US", ZipCode = "12345" }
        };
        byte[] payload = invalidOrder.ToByteArray();
        var metadata = new KafkaMessageMetadata { Topic = "orders", Partition = 0, Offset = 101112 };
        _mockValidator.Setup(v => v.Validate(payload, It.IsAny<KafkaMessageMetadata>()))
            .Returns(new ValidationResult<Order>
            {
                IsValid = false,
                ValidationErrors = { $"{OrderValidationErrorCode.NumericValueOutOfRange}: shipping_cost = -5.00" },
                MessageMetadata = metadata
            });

        // Act
        await _consumer.ProcessSingleMessage(payload, metadata);

        // Assert
        _mockDlqProducer.Verify(p => p.ProduceAsync(payload, 
            It.Is<ValidationResult<Order>>(r => 
                r.ValidationErrors.Any(e => e.Contains("NumericValueOutOfRange") && e.Contains("shipping_cost"))
            )), Times.Once);
        Assert.True(_consumer.IsRunning);
    }

    [Fact]
    public async Task test_ac5_invalid_uuid_routed_to_dlq()
    {
        // Arrange: Order with invalid order_id
        var invalidOrder = new Order
        {
            OrderId = "not-a-uuid", // Invalid UUID
            UserId = Guid.NewGuid().ToString(),
            Items = { new OrderItem { ItemId = "item1", Quantity = 1, UnitPrice = 5.00m } },
            TotalCost = 8.00m,
            ShippingCost = 3.00m,
            ShippingAddress = new Address { Street1 = "123 Test St", City = "Test", Country = "US", ZipCode = "12345" }
        };
        byte[] payload = invalidOrder.ToByteArray();
        var metadata = new KafkaMessageMetadata { Topic = "orders", Partition = 2, Offset = 131415 };
        _mockValidator.Setup(v => v.Validate(payload, It.IsAny<KafkaMessageMetadata>()))
            .Returns(new ValidationResult<Order>
            {
                IsValid = false,
                ValidationErrors = { $"{OrderValidationErrorCode.InvalidUuidFormat}: order_id = not-a-uuid" },
                MessageMetadata = metadata
            });

        // Act
        await _consumer.ProcessSingleMessage(payload, metadata);

        // Assert
        _mockDlqProducer.Verify(p => p.ProduceAsync(payload, 
            It.Is<ValidationResult<Order>>(r => 
                r.ValidationErrors.Any(e => e.Contains("InvalidUuidFormat") && e.Contains("order_id"))
            )), Times.Once);
        Assert.True(_consumer.IsRunning);
    }

    [Fact]
    public async Task test_ac6_total_cost_mismatch_routed_to_dlq()
    {
        // Arrange: Total cost less than sum of items + shipping
        var invalidOrder = new Order
        {
            OrderId = Guid.NewGuid().ToString(),
            UserId = Guid.NewGuid().ToString(),
            Items = 
            { 
                new OrderItem { ItemId = "item1", Quantity = 2, UnitPrice = 10.00m }, // Sum = 20
                new OrderItem { ItemId = "item2", Quantity = 1, UnitPrice = 5.00m } // Sum = 5, total items sum =25
            },
            TotalCost = 27.00m, // Should be 25 + 3 = 28, mismatch of 1.00
            ShippingCost = 3.00m,
            ShippingAddress = new Address { Street1 = "123 Test St", City = "Test", Country = "US", ZipCode = "12345" }
        };
        byte[] payload = invalidOrder.ToByteArray();
        var metadata = new KafkaMessageMetadata { Topic = "orders", Partition = 0, Offset = 161718 };
        _mockValidator.Setup(v => v.Validate(payload, It.IsAny<KafkaMessageMetadata>()))
            .Returns(new ValidationResult<Order>
            {
                IsValid = false,
                ValidationErrors = { $"{OrderValidationErrorCode.NumericValueOutOfRange}: total_cost=27.00, expected at least 28.00" },
                MessageMetadata = metadata
            });

        // Act
        await _consumer.ProcessSingleMessage(payload, metadata);

        // Assert
        _mockDlqProducer.Verify(p => p.ProduceAsync(payload, 
            It.Is<ValidationResult<Order>>(r => 
                r.ValidationErrors.Any(e => e.Contains("NumericValueOutOfRange") && e.Contains("total_cost"))
            )), Times.Once);
        Assert.True(_consumer.IsRunning);
    }

    [Fact]
    public async Task test_ac7_validation_failure_logs_structured_error_no_pii()
    {
        // Arrange: Invalid order with shipping address containing PII
        var invalidOrder = new Order
        {
            OrderId = "not-a-uuid",
            UserId = Guid.NewGuid().ToString(),
            Items = { new OrderItem { ItemId = "item1", Quantity = 1, UnitPrice = 5.00m } },
            TotalCost = 8.00m,
            ShippingCost = 3.00m,
            ShippingAddress = new Address 
            { 
                Street1 = "123 Private St", 
                City = "Personal City", 
                Country = "US", 
                ZipCode = "12345",
                RecipientName = "John Doe" // PII
            }
        };
        byte[] payload = invalidOrder.ToByteArray();
        var metadata = new KafkaMessageMetadata 
        { 
            Topic = "orders", 
            Partition = 1, 
            Offset = 192021, 
            Timestamp = new DateTime(2024,1,1,12,0,0, DateTimeKind.Utc),
            MessageKey = "test-key" 
        };
        _mockValidator.Setup(v => v.Validate(payload, It.IsAny<KafkaMessageMetadata>()))
            .Returns(new ValidationResult<Order>
            {
                IsValid = false,
                ValidationErrors = { $"{OrderValidationErrorCode.InvalidUuidFormat}: order_id = not-a-uuid" },
                MessageMetadata = metadata
            });
        // Capture log output
        var logOutput = new List<string>();
        _consumer.OnLog += (sender, log) => logOutput.Add(log);

        // Act
        await _consumer.ProcessSingleMessage(payload, metadata);

        // Assert
        var errorLog = logOutput.FirstOrDefault(l => l.Contains("Error") && l.Contains("Validation failed"));
        Assert.NotNull(errorLog);
        // Verify all required metadata present
        Assert.Contains("offset: 192021", errorLog);
        Assert.Contains("partition: 1", errorLog);
        Assert.Contains("topic: orders", errorLog);
        Assert.Contains("timestamp: 2024-01-01T12:00:00Z", errorLog);
        Assert.Contains("error_code: InvalidUuidFormat", errorLog);
        Assert.Contains("message_key: test-key", errorLog);
        // Verify no PII present
        Assert.DoesNotContain("John Doe", errorLog);
        Assert.DoesNotContain("123 Private St", errorLog);
        Assert.DoesNotContain("Personal City", errorLog);
    }

    [Fact]
    public async Task test_ac8_consumer_handles_sustained_invalid_messages()
    {
        // Arrange: 1000 invalid messages to simulate 5 minutes of load
        var invalidPayload = Encoding.UTF8.GetBytes("invalid payload");
        var metadataList = Enumerable.Range(0, 1000)
            .Select(i => new KafkaMessageMetadata { Topic = "orders", Partition = 0, Offset = i })
            .ToList();
        _mockValidator.Setup(v => v.Validate(It.IsAny<byte[]>(), It.IsAny<KafkaMessageMetadata>()))
            .Returns(new ValidationResult<Order>
            {
                IsValid = false,
                ValidationErrors = { $"{OrderValidationErrorCode.PayloadDeserializationFailed}" },
                MessageMetadata = It.IsAny<KafkaMessageMetadata>()
            });

        // Act: Process all invalid messages
        foreach(var metadata in metadataList)
        {
            await _consumer.ProcessSingleMessage(invalidPayload, metadata);
        }

        // Assert: Consumer is still running, all messages routed to DLQ
        Assert.True(_consumer.IsRunning);
        _mockDlqProducer.Verify(p => p.ProduceAsync(It.IsAny<byte[]>(), It.IsAny<ValidationResult<Order>>()), Times.Exactly(1000));
    }
}
