// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Oteldemo;
using Google.Protobuf;
using System.Globalization;
using Google.Type;

namespace Accounting;

public class OrderMessageValidator : IOrderMessageValidator
{
    private const decimal MaxTotalCost = 100000.00m;
    private const decimal MaxShippingCost = 100000.00m;
    private const int MaxItemQuantity = 1000;
    private const decimal MaxUnitPrice = 10000.00m;
    private const decimal CostTolerance = 0.01m;

    public ValidationResult<Order> Validate(byte[] rawMessage, KafkaMessageMetadata metadata)
    {
        var result = new ValidationResult<Order>
        {
            MessageMetadata = metadata
        };

        Order order;
        try
        {
            order = Order.Parser.ParseFrom(rawMessage);
        }
        catch (Exception ex)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.PayloadDeserializationFailed}: Failed to deserialize payload to Order proto: {ex.Message}");
            return result;
        }

        // Validate required fields
        if (string.IsNullOrWhiteSpace(order.OrderId))
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.MissingRequiredField}: order_id");
        }
        if (string.IsNullOrWhiteSpace(order.UserId))
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.MissingRequiredField}: user_id");
        }
        if (order.Items == null || order.Items.Count == 0)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.MissingRequiredField}: items");
        }
        if (order.TotalCost == null)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.MissingRequiredField}: total_cost");
        }
        if (order.ShippingCost == null)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.MissingRequiredField}: shipping_cost");
        }
        if (order.ShippingAddress == null)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.MissingRequiredField}: shipping_address");
        }

        if (result.ValidationErrors.Any())
        {
            return result;
        }

        // Validate UUID formats
        if (!Guid.TryParse(order.OrderId, out _))
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.InvalidUuidFormat}: order_id = {order.OrderId}");
        }
        if (!Guid.TryParse(order.UserId, out _))
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.InvalidUuidFormat}: user_id = {order.UserId}");
        }

        // Validate numeric values for costs
        var totalCost = DecimalValueToDecimal(order.TotalCost);
        var shippingCost = DecimalValueToDecimal(order.ShippingCost);

        if (totalCost < 0 || totalCost > MaxTotalCost)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.NumericValueOutOfRange}: total_cost = {totalCost.ToString(CultureInfo.InvariantCulture)} (allowed range: 0 to {MaxTotalCost.ToString(CultureInfo.InvariantCulture)})");
        }
        if (shippingCost < 0 || shippingCost > MaxShippingCost)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.NumericValueOutOfRange}: shipping_cost = {shippingCost.ToString(CultureInfo.InvariantCulture)} (allowed range: 0 to {MaxShippingCost.ToString(CultureInfo.InvariantCulture)})");
        }

        // Validate items
        decimal itemsTotal = 0m;
        foreach (var item in order.Items)
        {
            if (item == null)
            {
                result.ValidationErrors.Add($"{OrderValidationErrorCode.InvalidItemStructure}: null item in items list");
                continue;
            }
            if (item.Quantity < 1 || item.Quantity > MaxItemQuantity)
            {
                result.ValidationErrors.Add($"{OrderValidationErrorCode.NumericValueOutOfRange}: item.quantity = {item.Quantity} (allowed range: 1 to {MaxItemQuantity})");
            }
            var unitPrice = DecimalValueToDecimal(item.UnitPrice);
            if (unitPrice < 0.01m || unitPrice > MaxUnitPrice)
            {
                result.ValidationErrors.Add($"{OrderValidationErrorCode.NumericValueOutOfRange}: item.unit_price = {unitPrice.ToString(CultureInfo.InvariantCulture)} (allowed range: 0.01 to {MaxUnitPrice.ToString(CultureInfo.InvariantCulture)})");
            }
            itemsTotal += unitPrice * item.Quantity;
        }

        // Validate total cost matches sum of items + shipping (within tolerance)
        var expectedTotal = itemsTotal + shippingCost;
        if (Math.Abs(totalCost - expectedTotal) > CostTolerance)
        {
            result.ValidationErrors.Add($"{OrderValidationErrorCode.NumericValueOutOfRange}: total_cost={totalCost.ToString(CultureInfo.InvariantCulture)}, expected at least {expectedTotal.ToString(CultureInfo.InvariantCulture)} (sum of items + shipping)");
        }

        result.IsValid = !result.ValidationErrors.Any();
        if (result.IsValid)
        {
            result.ValidPayload = order;
        }

        return result;
    }

    private static decimal DecimalValueToDecimal(DecimalValue value)
    {
        if (value == null) return 0m;
        if (!decimal.TryParse(value.Value, NumberStyles.Any, CultureInfo.InvariantCulture, out var result))
        {
            return 0m;
        }
        return result;
    }
}
