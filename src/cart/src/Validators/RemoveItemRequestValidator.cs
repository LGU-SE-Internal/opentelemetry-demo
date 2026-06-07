// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using FluentValidation;
using Oteldemo;

namespace cart.Validators;

public class RemoveItemRequestValidator : AbstractValidator<RemoveItemRequest>
{
    public RemoveItemRequestValidator()
    {
        RuleFor(x => x.UserId)
            .NotEmpty()
            .WithMessage("User ID cannot be empty");

        RuleFor(x => x.ProductId)
            .NotEmpty()
            .WithMessage("Product ID cannot be empty");
    }
}
