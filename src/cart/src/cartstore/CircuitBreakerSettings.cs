// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

namespace cart.cartstore;

public class CircuitBreakerSettings
{
    public int FailureThreshold { get; set; } = 5;
    public TimeSpan ResetTimeout { get; set; } = TimeSpan.FromSeconds(30);
    public TimeSpan SamplingDuration { get; set; } = TimeSpan.FromMinutes(1);
}
