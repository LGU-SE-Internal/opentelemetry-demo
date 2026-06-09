# OpenTelemetry Collector Configuration

This directory contains the OpenTelemetry Collector configuration for the demo application.

## Environment Variables

### `OTEL_REDIS_RECEIVER_ENDPOINT`
- **Purpose**: Configures the endpoint the Redis receiver connects to
- **Default**: `valkey-cart:6379`
- **Example**:
  ```bash
  OTEL_REDIS_RECEIVER_ENDPOINT="redis-production:6379"
  ```

### `OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS`
- **Purpose**: Configures allowed CORS origins for the OTLP HTTP receiver
- **Default**: `*` (allow all origins)
- **Format**: Comma-separated list of trusted origins
- **Example**:
  ```bash
  OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS="https://frontend.example.com,https://admin.example.com"
  ```

## Configuration Layers
- `otelcol-config.yml`: Base collector configuration: receivers, processors, and pipelines. Exports only to the debug exporter by default.
- `otelcol-config-full.yml`: Adds Kafka and PostgreSQL metric receivers
- `otelcol-config-observability.yml`: Adds Jaeger, Prometheus, OpenSearch exporters
- `otelcol-config-extras.yml`: Extras/customizations (empty stub)
