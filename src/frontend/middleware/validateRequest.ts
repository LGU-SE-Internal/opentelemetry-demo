import { z, ZodError } from "zod";
import { NextApiRequest, NextApiResponse } from "next";

type ValidationResult = {
  success: true;
  data: any;
} | {
  success: false;
  error: {
    error: string;
    message: string;
    details: Array<{ field: string; issue: string }>;
  };
};

function sanitizeString(input: string): string {
  // Remove HTML tags, inline JavaScript, and common SQL injection patterns
  return input
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<[^>]+>/g, "")
    .replace(/('|"|;|--|\/\*|\*\/|UNION|SELECT|INSERT|DELETE|UPDATE|DROP|ALTER)/gi, "")
    .trim();
}

export function validateRequest(schema: z.ZodSchema) {
  return async (req: NextApiRequest, res: NextApiResponse, next: () => void) => {
    try {
      const validationData = {
        query: req.query,
        params: req.query, // Next.js API routes put path params in req.query
        body: req.body,
      };

      const validatedData = await schema.parseAsync(validationData);
      
      // Apply sanitization to all string fields
      const sanitizeRecursive = (data: any): any => {
        if (typeof data === "string") {
          return sanitizeString(data);
        }
        if (Array.isArray(data)) {
          return data.map(sanitizeRecursive);
        }
        if (typeof data === "object" && data !== null) {
          return Object.fromEntries(
            Object.entries(data).map(([key, value]) => [key, sanitizeRecursive(value)])
          );
        }
        return data;
      };

      (req as any).validatedData = sanitizeRecursive(validatedData);
      next();
    } catch (error) {
      if (error instanceof ZodError) {
        const details = error.issues.map((issue) => ({
          field: issue.path.join("."),
          issue: issue.message,
        }));

        return res.status(400).json({
          error: "Bad Request",
          message: "Invalid input data. Please check your request parameters and try again.",
          details,
        });
      }

      return res.status(500).json({
        error: "Internal Server Error",
        message: "An unexpected error occurred while validating your request.",
        details: [],
      });
    }
  };
}

// Helper to create sanitized string schemas
export const zSanitizedString = () => z.string().transform(sanitizeString);
