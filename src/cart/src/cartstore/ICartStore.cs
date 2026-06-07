// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System.Threading.Tasks;
using System.Threading;

namespace cart.cartstore;

public interface ICartStore
{
    void Initialize();

    Task AddItemAsync(string userId, string productId, int quantity);
    Task EmptyCartAsync(string userId);

    Task<Oteldemo.Cart> GetCartAsync(string userId);

    bool Ping();

    Task FlushAsync(CancellationToken cancellationToken = default);
}
