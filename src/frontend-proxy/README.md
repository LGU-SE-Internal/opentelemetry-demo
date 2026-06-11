# Frontend Proxy Service

This service acts as a reverse proxy for the various user-facing web interfaces.

## Modifying the Envoy Configuration

The envoy configuration is generated from the `envoy.tmpl.yaml` file in this
directory. Environment variables are substituted at deploy-time.

## Rate Limit Configuration

The frontend proxy supports rate limiting per API route groups. You can configure the following environment variables:

| Variable Name | Type | Default Value | Valid Range | Description |
|---------------|------|---------------|-------------|-------------|
| RATE_LIMIT_PUBLIC_ROUTES_RPM | integer | 60 | 0 - 10000 | Requests per minute allowed for public unauthenticated routes (product listings, search, etc.) |
| RATE_LIMIT_HEALTH_ROUTES_RPM | integer | 300 | 0 - 10000 | Requests per minute allowed for health check endpoints |
| RATE_LIMIT_CHECKOUT_ROUTES_RPM | integer | 20 | 0 - 10000 | Requests per minute allowed for checkout/payment related routes |
| RATE_LIMIT_ENABLED | boolean | false | true/false | Global toggle for all rate limit enforcement |

### Route Groups
- Public routes: `/api/products*`, `/api/categories*`, `/api/search*`
- Health routes: `/health*`, `/ready*`, `/live*`
- Checkout routes: `/api/cart*`, `/api/checkout*`, `/api/payment*`
