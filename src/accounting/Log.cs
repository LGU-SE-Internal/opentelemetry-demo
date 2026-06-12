// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Microsoft.Extensions.Logging;
using Oteldemo;

namespace Accounting
{
    internal static partial class Log
    {
        [LoggerMessage(
            Level = LogLevel.Information,
            Message = "Order details: {@OrderResult}.")]
        public static partial void OrderReceivedMessage(ILogger logger, OrderResult orderResult);

        [LoggerMessage(
            Level = LogLevel.Error,
            Message = "Kafka connection failed for broker {BrokerAddress}.")]
        public static partial void KafkaConnectionFailed(ILogger logger, string brokerAddress, Exception exception);

        [LoggerMessage(
            Level = LogLevel.Error,
            Message = "Kafka message consume error on topic {Topic}, partition {Partition}, offset {Offset}.")]
        public static partial void KafkaMessageConsumeError(ILogger logger, string topic, int partition, long offset, Exception exception);

        [LoggerMessage(
            Level = LogLevel.Information,
            Message = "Database write success for {OperationType}, order {OrderId}, customer {CustomerId}.")]
        public static partial void DatabaseWriteSuccess(ILogger logger, string operationType, Guid orderId, Guid customerId);

        [LoggerMessage(
            Level = LogLevel.Error,
            Message = "Database write failed for {OperationType}, order {OrderId}, customer {CustomerId}.")]
        public static partial void DatabaseWriteFailed(ILogger logger, string operationType, Guid orderId, Guid customerId, Exception exception);

        [LoggerMessage(
            Level = LogLevel.Warning,
            Message = "Order validation failed for order {OrderId}, customer {CustomerId}: {ValidationError}. Correlation ID: {CorrelationId}")]
        public static partial void OrderValidationFailed(ILogger logger, Guid orderId, Guid customerId, string validationError, string correlationId);

        [LoggerMessage(
            Level = LogLevel.Warning,
            Message = "Message produced to DLQ topic {DlqTopic} for original order {OriginalOrderId}. Failure reason: {FailureReason}. Correlation ID: {CorrelationId}")]
        public static partial void DlqMessageProduced(ILogger logger, string dlqTopic, Guid originalOrderId, string failureReason, string correlationId);

        [LoggerMessage(
            Level = LogLevel.Information,
            Message = "Graceful shutdown started at {StartTime}.")]
        public static partial void GracefulShutdownStarted(ILogger logger, DateTimeOffset startTime);

        [LoggerMessage(
            Level = LogLevel.Information,
            Message = "Graceful shutdown completed at {CompleteTime}, total duration: {Duration}.")]
        public static partial void GracefulShutdownCompleted(ILogger logger, DateTimeOffset completeTime, TimeSpan duration);
    }
}
