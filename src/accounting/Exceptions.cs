// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

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
