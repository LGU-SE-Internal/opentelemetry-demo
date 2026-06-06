import { z } from "zod";
import { zSanitizedString } from "./validateRequest";

// Common schemas
const ProductIdSchema = z.string().uuid("productId must be a valid UUID");
const UserIdSchema = z.string().uuid("userId must be a valid UUID");
const PositiveIntegerSchema = z.number().int().positive("must be a positive integer");

// Endpoint specific schemas
export const AddToCartSchema = z.object({
  body: z.object({
    productId: ProductIdSchema,
    quantity: PositiveIntegerSchema,
    userId: UserIdSchema,
  }),
});

export const GetCartSchema = z.object({
  params: z.object({
    userId: UserIdSchema,
  }),
  query: z.object({
    page: z.coerce.number().int().positive("page must be a positive integer").optional(),
    limit: z.coerce.number().int().positive("limit must be a positive integer").max(100, "limit cannot exceed 100").optional(),
  }),
});

export const CheckoutSchema = z.object({
  body: z.object({
    userId: UserIdSchema,
    shippingAddress: z.object({
      street: zSanitizedString().min(1, "street address is required"),
      city: zSanitizedString().min(1, "city is required"),
      state: zSanitizedString().min(1, "state is required"),
      zipCode: zSanitizedString().min(5, "zip code must be at least 5 characters"),
      country: zSanitizedString().min(2, "country code must be at least 2 characters"),
    }),
    paymentDetails: z.object({
      cardNumber: z.string().length(16, "card number must be 16 digits").regex(/^\d+$/, "card number must only contain digits"),
      expiryDate: z.string().regex(/^(0[1-9]|1[0-2])\/\d{2}$/, "expiry date must be in MM/YY format"),
      cvv: z.string().length(3, "CVV must be 3 digits").regex(/^\d+$/, "CVV must only contain digits"),
    }),
    cartItems: z.array(z.object({
      productId: ProductIdSchema,
      quantity: PositiveIntegerSchema,
      price: z.number().positive("price must be a positive number"),
    })).min(1, "cart cannot be empty"),
  }),
});

export const ProductSearchSchema = z.object({
  query: z.object({
    query: zSanitizedString().optional(),
    category: zSanitizedString().optional(),
    priceMin: z.coerce.number().nonnegative("priceMin must be a non-negative number").optional(),
    priceMax: z.coerce.number().positive("priceMax must be a positive number").optional(),
    sort: z.enum(["price_asc", "price_desc", "name_asc", "name_desc", "popularity"]).optional(),
  }),
});

export const GetProductSchema = z.object({
  params: z.object({
    productId: ProductIdSchema,
  }),
});
