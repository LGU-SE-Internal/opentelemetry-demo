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
            .WithMessage("User ID cannot be empty");

        RuleFor(x => x.Item)
            .NotNull()
            .WithMessage("item is required");

        RuleFor(x => x.Item.ProductId)
            .NotEmpty()
            .WithMessage("Product ID cannot be empty")
            .When(x => x.Item != null);

        RuleFor(x => x.Item.Quantity)
            .GreaterThan(0)
            .WithMessage("Quantity must be greater than 0")
            .When(x => x.Item != null);
    }
}
