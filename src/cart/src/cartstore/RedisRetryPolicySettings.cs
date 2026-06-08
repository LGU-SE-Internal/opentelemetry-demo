// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

namespace cart.cartstore;

public class RedisRetryPolicySettings
{
    public int MaxRetryAttempts { get; set; } = 3;
    public int InitialBackoffMs { get; set; } = 100;
    public int MaxBackoffMs { get; set; } = 1000;
}
