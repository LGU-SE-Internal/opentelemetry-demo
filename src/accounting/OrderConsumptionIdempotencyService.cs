using Microsoft.EntityFrameworkCore;
using System;
using System.Threading;
using System.Threading.Tasks;

namespace Accounting.Service;

public interface IOrderConsumptionIdempotencyService
{
    Task<bool> IsOrderProcessedAsync(Guid orderId, CancellationToken cancellationToken);
    Task MarkOrderProcessedAsync(Guid orderId, DateTimeOffset processedAt, CancellationToken cancellationToken);
}

public class ProcessedOrder
{
    public Guid OrderId { get; set; }
    public DateTimeOffset ProcessedAt { get; set; }
}

public class OrderConsumptionIdempotencyService : IOrderConsumptionIdempotencyService
{
    private readonly AccountingDbContext _dbContext;

    public OrderConsumptionIdempotencyService(AccountingDbContext dbContext)
    {
        _dbContext = dbContext;
    }

    public async Task<bool> IsOrderProcessedAsync(Guid orderId, CancellationToken cancellationToken)
    {
        return await _dbContext.ProcessedOrders.AnyAsync(o => o.OrderId == orderId, cancellationToken);
    }

    public async Task MarkOrderProcessedAsync(Guid orderId, DateTimeOffset processedAt, CancellationToken cancellationToken)
    {
        _dbContext.ProcessedOrders.Add(new ProcessedOrder { OrderId = orderId, ProcessedAt = processedAt });
        await _dbContext.SaveChangesAsync(cancellationToken);
    }
}
