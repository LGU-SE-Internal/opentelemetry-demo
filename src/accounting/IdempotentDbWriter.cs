// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Microsoft.EntityFrameworkCore;
using Polly;

namespace Accounting;

internal class IdempotentDbWriter : IIdempotentDbWriter
{
    private readonly IDbContextFactory<DBContext> _dbContextFactory;
    private readonly IAsyncPolicy _retryPolicy;

    public IdempotentDbWriter(IDbContextFactory<DBContext> dbContextFactory, IAsyncPolicy retryPolicy)
    {
        _dbContextFactory = dbContextFactory;
        _retryPolicy = retryPolicy;
    }

    public async Task<bool> ExecuteWriteAsync(string idempotencyKey, Func<Task> operation)
    {
        using var dbContext = await _dbContextFactory.CreateDbContextAsync();
        
        // Check if idempotency key already exists
        var existingOrder = await dbContext.Orders
            .FirstOrDefaultAsync(o => o.IdempotencyKey == idempotencyKey);
        
        if (existingOrder != null)
        {
            return true;
        }

        await _retryPolicy.ExecuteAsync(async () =>
        {
            await operation();
        });

        return true;
    }
}
