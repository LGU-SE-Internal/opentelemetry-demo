// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using FluentValidation;
using Oteldemo;

namespace cart.Validators;

public class EmptyCartRequestValidator : AbstractValidator<EmptyCartRequest>
{
    public EmptyCartRequestValidator()
    {
        RuleFor(x => x.UserId)
            .NotEmpty()
            .WithMessage("User ID cannot be empty");
    }
}
