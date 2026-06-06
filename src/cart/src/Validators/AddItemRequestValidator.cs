// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using FluentValidation;
using Oteldemo;

namespace cart.Validators;

public class AddItemRequestValidator : AbstractValidator<AddItemRequest>
{
    public AddItemRequestValidator()
    {
        RuleFor(x => x.UserId)
            .NotEmpty()
            .WithMessage("user_id is required");

        RuleFor(x => x.Item)
            .NotNull()
            .WithMessage("item is required");

        RuleFor(x => x.Item.ProductId)
            .NotEmpty()
            .WithMessage("product_id is required")
            .When(x => x.Item != null);

        RuleFor(x => x.Item.Quantity)
            .GreaterThan(0)
            .WithMessage("quantity must be greater than 0")
            .When(x => x.Item != null);
    }
}
