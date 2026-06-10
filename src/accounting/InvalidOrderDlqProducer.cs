// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Confluent.Kafka;
using Oteldemo;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace Accounting;

public interface IInvalidOrderDlqProducer
{
    /// <summary>
    /// Produces a message to the invalid order DLQ when database operations fail
    /// </summary>
    /// <param name="order">Order that failed processing</param>
    /// <param name="failureReason">Reason for failure (e.g. circuit open, database error)</param>
    /// <param name="cancellationToken">Cancellation token</param>
    Task ProduceAsync(Order order, string failureReason, CancellationToken cancellationToken = default);
}

public class InvalidOrderDlqProducer : IInvalidOrderDlqProducer
{
    private readonly IProducer<string, byte[]> _producer;
    private readonly ILogger<InvalidOrderDlqProducer> _logger;
    private readonly string _dlqTopicName;

    public InvalidOrderDlqProducer(IProducer<string, byte[]> producer, ILogger<InvalidOrderDlqProducer> logger, string dlqTopicName)
    {
        _producer = producer;
        _logger = logger;
        _dlqTopicName = dlqTopicName;
    }

    public async Task ProduceAsync(Order order, string failureReason, CancellationToken cancellationToken = default)
    {
        try
        {
            var dlqMessage = new
            {
                Order = order,
                FailureReason = failureReason,
                Timestamp = DateTimeOffset.UtcNow
            };
            
            var messageBytes = JsonSerializer.SerializeToUtf8Bytes(dlqMessage);
            
            await _producer.ProduceAsync(_dlqTopicName, new Message<string, byte[]>
            {
                Key = order.OrderId,
                Value = messageBytes
            }, cancellationToken);
            
            _logger.LogInformation("Produced order {OrderId} to invalid order DLQ, reason: {FailureReason}", order.OrderId, failureReason);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to produce order {OrderId} to invalid order DLQ", order.OrderId);
            throw;
        }
    }
}
