// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

namespace Accounting;

public interface IIdempotentDbWriter
{
    Task<bool> ExecuteWriteAsync(string idempotencyKey, Func<Task> operation);
}
