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
        // First check if idempotency key already exists
        await using var checkContext = await _dbContextFactory.CreateDbContextAsync();
        var exists = await checkContext.Orders.AnyAsync(o => o.IdempotencyKey == idempotencyKey);
        if (exists)
        {
            return true;
        }

        // Execute the operation with retry policy
        await _retryPolicy.ExecuteAsync(async () =>
        {
            await using var writeContext = await _dbContextFactory.CreateDbContextAsync();
            await using var transaction = await writeContext.Database.BeginTransactionAsync();
            
            // Double check existence inside transaction to prevent race conditions
            var transactionExists = await writeContext.Orders.AnyAsync(o => o.IdempotencyKey == idempotencyKey);
            if (transactionExists)
            {
                await transaction.CommitAsync();
                return;
            }

            // Execute the user-provided write operation
            await operation();

            // Save changes
            await writeContext.SaveChangesAsync();
            await transaction.CommitAsync();
        });

        return true;
    }
}
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
