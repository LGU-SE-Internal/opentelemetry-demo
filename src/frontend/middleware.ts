import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { AddToCartSchema, GetCartSchema, CheckoutSchema, ProductSearchSchema, GetProductSchema } from "./middleware/validationSchemas";
import { z, ZodError } from "zod";

function sanitizeString(input: string): string {
  return input
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<[^>]+>/g, "")
    .replace(/('|"|;|--|\/\*|\*\/|UNION|SELECT|INSERT|DELETE|UPDATE|DROP|ALTER)/gi, "")
    .trim();
}

function sanitizeRecursive(data: any): any {
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
}

const routeSchemas: Record<string, Record<string, z.ZodSchema>> = {
  "/api/cart": {
    POST: AddToCartSchema,
  },
  "/api/cart/[userId]": {
    GET: GetCartSchema,
  },
  "/api/checkout": {
    POST: CheckoutSchema,
  },
  "/api/products/search": {
    GET: ProductSearchSchema,
  },
  "/api/products/[productId]": {
    GET: GetProductSchema,
  },
};

export async function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  const method = request.method;

  // Find matching route schema
  let matchedSchema: z.ZodSchema | undefined;
  for (const [route, methods] of Object.entries(routeSchemas)) {
    // Convert Next.js route pattern to regex
    const routeRegex = new RegExp(`^${route.replace(/\[([^\]]+)\]/g, "([^/]+)")}$`);
    if (routeRegex.test(pathname) && methods[method]) {
      matchedSchema = methods[method];
      break;
    }
  }

  if (!matchedSchema) {
    return NextResponse.next();
  }

  try {
    // Get request data
    const query = Object.fromEntries(request.nextUrl.searchParams);
    const params = query; // path params are in query for Next.js
    let body = {};
    if (method === "POST" && request.body) {
      try {
        body = await request.json();
      } catch (e) {
        // Invalid JSON
        return NextResponse.json(
          {
            error: "Bad Request",
            message: "Invalid JSON in request body",
            details: [{ field: "body", issue: "Invalid JSON format" }],
          },
          { status: 400 }
        );
      }
    }

    // Validate
    const validationData = { query, params, body };
    const validatedData = matchedSchema.parse(validationData);
    
    // Sanitize
    const sanitizedData = sanitizeRecursive(validatedData);

    // Pass sanitized data to the API route
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("x-validated-data", JSON.stringify(sanitizedData));

    return NextResponse.next({
      request: {
        headers: requestHeaders,
      },
    });
  } catch (error) {
    if (error instanceof ZodError) {
      const details = error.issues.map((issue) => ({
        field: issue.path.join("."),
        issue: issue.message,
      }));

      return NextResponse.json(
        {
          error: "Bad Request",
          message: "Invalid input data. Please check your request parameters and try again.",
          details,
        },
        { status: 400 }
      );
    }

    return NextResponse.json(
      {
        error: "Internal Server Error",
        message: "An unexpected error occurred while validating your request.",
        details: [],
      },
      { status: 500 }
    );
  }
}

export const config = {
  matcher: [
    "/api/cart",
    "/api/cart/:userId*",
    "/api/checkout",
    "/api/products/search",
    "/api/products/:productId*",
  ],
};
