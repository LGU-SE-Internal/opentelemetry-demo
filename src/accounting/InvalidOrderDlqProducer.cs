// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Confluent.Kafka;
using System.Text.Json;
using Oteldemo;

namespace Accounting;

public class InvalidOrderDlqProducer : IInvalidOrderDlqProducer
{
    private readonly IProducer<Null, byte[]> _producer;
    private const string DlqTopic = "orders-dlq";

    public InvalidOrderDlqProducer(IProducer<Null, byte[]> producer)
    {
        _producer = producer;
    }

    public async Task ProduceAsync(byte[] rawMessage, ValidationResult<Order> validationResult)
    {
        var headers = new Headers
        {
            { "validation_errors", JsonSerializer.SerializeToUtf8Bytes(validationResult.ValidationErrors) },
            { "original_topic", System.Text.Encoding.UTF8.GetBytes(validationResult.MessageMetadata.Topic) },
            { "original_partition", BitConverter.GetBytes(validationResult.MessageMetadata.Partition) },
            { "original_offset", BitConverter.GetBytes(validationResult.MessageMetadata.Offset) },
            { "original_timestamp", BitConverter.GetBytes(validationResult.MessageMetadata.Timestamp.ToUniversalTime().ToBinary()) }
        };

        if (!string.IsNullOrEmpty(validationResult.MessageMetadata.MessageKey))
        {
            headers.Add("original_message_key", System.Text.Encoding.UTF8.GetBytes(validationResult.MessageMetadata.MessageKey));
        }

        var message = new Message<Null, byte[]>
        {
            Value = rawMessage,
            Headers = headers,
            Timestamp = new Timestamp(DateTime.UtcNow)
        };

        await _producer.ProduceAsync(DlqTopic, message);
    }
}
