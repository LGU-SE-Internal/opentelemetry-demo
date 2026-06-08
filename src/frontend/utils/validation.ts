import { z } from 'zod';

// Shared UUID regex
const UUID_REGEX = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

// Sanitization functions
export function sanitizeString(str: string): string {
  // Trim whitespace
  let sanitized = str.trim();
  // Strip HTML/XML tags
  sanitized = sanitized.replace(/<[^>]*>/g, '');
  // Strip control characters (ASCII 0-31, 127)
  sanitized = sanitized.replace(/[\x00-\x1F\x7F]/g, '');
  return sanitized;
}

// Zod preprocessor for string sanitization
const sanitizedString = z.string().transform(sanitizeString);

// Shared schemas
export const uuidSchema = z.string().regex(UUID_REGEX, 'Invalid UUID format');

// Cart schemas
export const postCartSchema = z.object({
  productId: uuidSchema,
  quantity: z.number().int().min(1, 'Quantity must be at least 1').max(100, 'Quantity cannot exceed 100')
});

export const getCartSchema = z.object({
  userId: uuidSchema.optional()
});

export const deleteCartItemSchema = z.object({
  productId: uuidSchema
});

// Checkout schema
export const checkoutSchema = z.object({
  userId: uuidSchema,
  email: z.string().email('Invalid email format'),
  address: z.object({
    street: sanitizedString.max(255, 'Street cannot exceed 255 characters'),
    city: sanitizedString.max(100, 'City cannot exceed 100 characters'),
    state: sanitizedString.max(100, 'State cannot exceed 100 characters'),
    zipCode: sanitizedString.regex(/^\d{5}(-\d{4})?$/, 'Invalid US zip code format'),
    country: sanitizedString.max(100, 'Country cannot exceed 100 characters')
  }),
  payment: z.object({
    cardNumber: sanitizedString.regex(/^\d{16}$/, 'Card number must be 16 digits'),
    expirationDate: sanitizedString.regex(/^(0[1-9]|1[0-2])\/\d{2}$/, 'Invalid expiration date format (MM/YY)'),
    cvv: sanitizedString.regex(/^\d{3,4}$/, 'CVV must be 3 or 4 digits')
  })
});

// Products schema
export const getProductsSchema = z.object({
  search: sanitizedString.max(100, 'Search string cannot exceed 100 characters').optional(),
  page: z.coerce.number().int().min(1, 'Page must be at least 1').max(100, 'Page cannot exceed 100').optional().default(1),
  limit: z.coerce.number().int().min(1, 'Limit must be at least 1').max(50, 'Limit cannot exceed 50').optional().default(20)
});

// Recommendations schema
export const getRecommendationsSchema = z.object({
  productId: uuidSchema,
  limit: z.coerce.number().int().min(1, 'Limit must be at least 1').max(20, 'Limit cannot exceed 20').optional().default(5)
});

// Validation error format
export type ValidationError = {
  error: 'Bad Request';
  message: string;
  details: Array<{
    field: string;
    issue: string;
  }>;
};

export function formatValidationError(error: z.ZodError): ValidationError {
  return {
    error: 'Bad Request',
    message: 'Invalid request parameters',
    details: error.issues.map(issue => ({
      field: issue.path.join('.'),
      issue: issue.message
    }))
  };
}
