# Image Provider Service

The image-provider service is an Nginx-based static asset server that serves product images and other static assets for the OpenTelemetry Demo.

## Configuration

### Environment Variables

| Variable Name | Type | Required | Default | Description |
|---------------|------|----------|---------|-------------|
| `IMAGE_PROVIDER_PORT` | integer | Optional | 8080 | Port to listen on for plain HTTP requests |
| `OTEL_COLLECTOR_HOST` | string | Required | - | Hostname of the OpenTelemetry Collector |
| `OTEL_COLLECTOR_PORT_GRPC` | integer | Optional | 4317 | GRPC port of the OpenTelemetry Collector |
| `OTEL_SERVICE_NAME` | string | Optional | image-provider | Service name for OpenTelemetry instrumentation |
| `NGINX_RATE_LIMIT_RPS` | integer | Optional | 10 | Requests per second rate limit for image assets |
| `IMAGE_PROVIDER_TLS_CERT_PATH` | string | Optional | - | Absolute filesystem path to TLS server certificate file (PEM format). If set along with `IMAGE_PROVIDER_TLS_KEY_PATH`, HTTPS listener is enabled on port 8443. |
| `IMAGE_PROVIDER_TLS_KEY_PATH` | string | Optional | - | Absolute filesystem path to TLS server private key file (PEM format). Required if `IMAGE_PROVIDER_TLS_CERT_PATH` is set. |
| `IMAGE_PROVIDER_TLS_CA_CERT_PATH` | string | Optional | - | Absolute filesystem path to CA certificate bundle file (PEM format). If set, mTLS client certificate validation is enabled for all incoming HTTPS requests. |

## TLS Configuration

### Enabling HTTPS
To enable HTTPS support, provide both `IMAGE_PROVIDER_TLS_CERT_PATH` and `IMAGE_PROVIDER_TLS_KEY_PATH` environment variables pointing to valid PEM-formatted certificate and key files. When enabled:
- Service listens on port 8443 for HTTPS requests
- Port 8080 continues to serve plain HTTP requests (backward compatible)

### Enabling mTLS Authentication
To enable mTLS client certificate validation, provide `IMAGE_PROVIDER_TLS_CA_CERT_PATH` in addition to the server cert and key variables. When mTLS is enabled:
- All HTTPS requests to port 8443 require a valid client certificate signed by the provided CA
- Invalid or missing client certificates return 400 Bad Request error
- Plain HTTP traffic on port 8080 remains unauthenticated

## Example Usage

### Basic (no TLS)
```bash
docker run -p 8080:8080 \
  -e OTEL_COLLECTOR_HOST=otel-collector \
  otel/demo-image-provider:latest
```

### With HTTPS
```bash
docker run -p 8080:8080 -p 8443:8443 \
  -e OTEL_COLLECTOR_HOST=otel-collector \
  -e IMAGE_PROVIDER_TLS_CERT_PATH=/certs/server.crt \
  -e IMAGE_PROVIDER_TLS_KEY_PATH=/certs/server.key \
  -v ./certs:/certs:ro \
  otel/demo-image-provider:latest
```

### With mTLS
```bash
docker run -p 8080:8080 -p 8443:8443 \
  -e OTEL_COLLECTOR_HOST=otel-collector \
  -e IMAGE_PROVIDER_TLS_CERT_PATH=/certs/server.crt \
  -e IMAGE_PROVIDER_TLS_KEY_PATH=/certs/server.key \
  -e IMAGE_PROVIDER_TLS_CA_CERT_PATH=/certs/ca.crt \
  -v ./certs:/certs:ro \
  otel/demo-image-provider:latest
```
