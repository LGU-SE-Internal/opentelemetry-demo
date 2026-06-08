import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { rateLimit } from 'next-rate-limit';
import { AddToCartSchema, GetCartSchema, CheckoutSchema, ProductSearchSchema, GetProductSchema } from "./middleware/validationSchemas";
import { z, ZodError } from "zod";

// Rate limit configuration
const MAX_REQUESTS = parseInt(process.env.FRONTEND_RATE_LIMIT_MAX_REQUESTS || '100', 10);
const WINDOW_SECONDS = parseInt(process.env.FRONTEND_RATE_LIMIT_WINDOW_SECONDS || '60', 10);
const WINDOW_MS = WINDOW_SECONDS * 1000;

const { limit, isRateLimited, getRemaining, getResetTime } = rateLimit({
  windowMs: WINDOW_MS,
  max: MAX_REQUESTS,
  standardHeaders: true,
  legacyHeaders: false,
});

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

  // Skip rate limiting for internal API endpoints
  if (pathname.startsWith('/api/internal/')) {
    return NextResponse.next();
  }

  // Extract client IP address
  const xForwardedFor = request.headers.get('X-Forwarded-For');
  const clientIp = xForwardedFor ? xForwardedFor.split(',')[0].trim() : request.ip || 'unknown';

  // Check rate limit
  const { limited, reset } = await limit(clientIp);
  if (limited) {
    const retryAfter = Math.ceil((reset - Date.now()) / 1000);
    
    // Log structured rate limit violation event
    console.log(JSON.stringify({
      event: 'rate_limit_violation',
      timestamp: new Date().toISOString(),
      client_ip: clientIp,
      endpoint: pathname,
      rate_limit_threshold: MAX_REQUESTS,
      window_length_seconds: WINDOW_SECONDS,
      retry_after_seconds: retryAfter
    }));

    // Return 429 response
    return NextResponse.json(
      {
        error: "Too Many Requests",
        message: `You have exceeded the rate limit of ${MAX_REQUESTS} requests per ${WINDOW_SECONDS}s`,
        retryAfter: retryAfter
      },
      {
        status: 429,
        headers: {
          'Retry-After': retryAfter.toString(),
          'RateLimit-Limit': MAX_REQUESTS.toString(),
          'RateLimit-Remaining': '0',
          'RateLimit-Reset': Math.ceil(reset / 1000).toString()
        }
      }
    );
  }

  // Add rate limit headers to successful responses
  const remaining = await getRemaining(clientIp);
  const resetTime = await getResetTime(clientIp);
  const responseHeaders = new Headers();
  responseHeaders.set('RateLimit-Limit', MAX_REQUESTS.toString());
  responseHeaders.set('RateLimit-Remaining', remaining.toString());
  responseHeaders.set('RateLimit-Reset', Math.ceil(resetTime / 1000).toString());

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
    return NextResponse.next({ headers: responseHeaders });
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
          { status: 400, headers: responseHeaders }
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
      headers: responseHeaders
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
        { status: 400, headers: responseHeaders }
      );
    }

    return NextResponse.json(
      {
        error: "Internal Server Error",
        message: "An unexpected error occurred while validating your request.",
        details: [],
      },
      { status: 500, headers: responseHeaders }
    );
  }
}

export const config = {
  matcher: [
    "/api/:path*",
  ],
};
