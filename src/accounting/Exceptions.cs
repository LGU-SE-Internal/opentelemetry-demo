// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Polly.CircuitBreaker;

namespace Accounting;

[Serializable]
internal class InvalidTlsConfigurationException : Exception
{
    public InvalidTlsConfigurationException() { }
    public InvalidTlsConfigurationException(string message) : base(message) { }
    public InvalidTlsConfigurationException(string message, Exception inner) : base(message, inner) { }
    protected InvalidTlsConfigurationException(
        System.Runtime.Serialization.SerializationInfo info,
        System.Runtime.Serialization.StreamingContext context) : base(info, context) { }
}

public class CircuitBreakerOpenException : InvalidOperationException
{
    public CircuitState CurrentState { get; }
    public TimeSpan RemainingBreakDuration { get; }

    public CircuitBreakerOpenException(CircuitState currentState, TimeSpan remainingBreakDuration, string message)
        : base(message)
    {
        CurrentState = currentState;
        RemainingBreakDuration = remainingBreakDuration;
    }
}
