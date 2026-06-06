// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using FluentValidation;
using Oteldemo;

namespace cart.Validators;

public class GetCartRequestValidator : AbstractValidator<GetCartRequest>
{
    public GetCartRequestValidator()
    {
        RuleFor(x => x.UserId)
            .NotEmpty()
            .WithMessage("user_id is required");
    }
}
