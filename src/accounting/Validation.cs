// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Oteldemo;
using Confluent.Kafka;

namespace Accounting;

public interface IOrderMessageValidator
{
    /// <summary>
    /// Validates raw Kafka order message payload against schema and business rules
    /// </summary>
    /// <param name="rawMessage">Raw byte payload from Kafka</param>
    /// <param name="metadata">Kafka message metadata (topic, partition, offset, timestamp)</param>
    /// <returns>Validation result: success with parsed Order object, or failure with error details</returns>
    ValidationResult<Order> Validate(byte[] rawMessage, KafkaMessageMetadata metadata);
}

public class ValidationResult<T>
{
    public bool IsValid { get; init; }
    public T? ValidPayload { get; init; }
    public List<string> ValidationErrors { get; init; } = new();
    public required KafkaMessageMetadata MessageMetadata { get; init; }
}

public class KafkaMessageMetadata
{
    public string Topic { get; init; } = string.Empty;
    public int Partition { get; init; }
    public long Offset { get; init; }
    public DateTime Timestamp { get; init; }
    public string? MessageKey { get; init; }
}

/// <summary>
/// Dead letter queue producer for invalid order messages
/// </summary>
public interface IInvalidOrderDlqProducer
{
    /// <summary>
    /// Routes invalid order message to DLQ topic with validation error metadata
    /// </summary>
    /// <param name="rawMessage">Original raw message payload</param>
    /// <param name="validationResult">Validation failure details</param>
    /// <returns>Awaitable task</returns>
    Task ProduceAsync(byte[] rawMessage, ValidationResult<Order> validationResult);
}

/// <summary>
/// Error codes for validation failures
/// </summary>
public enum OrderValidationErrorCode
{
    PayloadDeserializationFailed = 1001,
    MissingRequiredField = 1002,
    NumericValueOutOfRange = 1003,
    InvalidUuidFormat = 1004,
    InvalidItemStructure = 1005,
    InvalidShippingDetails = 1006
}
