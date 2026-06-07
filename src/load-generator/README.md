# Load Generator

The load generator creates simulated traffic to the demo.

## Accessing the Load Generator

You can access the web interface to Locust at `http://localhost:8080/loadgen/`.

## Configuration

The load generator supports the following environment variables for OTLP telemetry export configuration:

| Env Var Name | Type | Default | Description |
|--------------|------|---------|-------------|
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | string | `"http://otel-collector:4318/v1/traces"` | OTLP traces export endpoint URL |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | string | `"http://otel-collector:4318/v1/metrics"` | OTLP metrics export endpoint URL |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | string | `"http://otel-collector:4318/v1/logs"` | OTLP logs export endpoint URL |
| `OTEL_EXPORTER_OTLP_INSECURE` | boolean | `False` | If true, disable TLS encryption for OTLP connections |
| `OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE` | string | `""` | Path to PEM-encoded client certificate for mTLS authentication |
| `OTEL_EXPORTER_OTLP_CLIENT_KEY` | string | `""` | Path to PEM-encoded client private key for mTLS authentication |
| `OTEL_EXPORTER_OTLP_CERTIFICATE_AUTHORITY` | string | `""` | Path to PEM-encoded CA certificate to verify server TLS certificate |
| `OTEL_EXPORTER_OTLP_RETRY_MAX_ATTEMPTS` | integer | `5` | Maximum number of retries for failed OTLP export attempts |
| `OTEL_EXPORTER_OTLP_RETRY_INITIAL_DELAY` | float | `1.0` | Initial backoff delay in seconds for retries |
| `OTEL_EXPORTER_OTLP_RETRY_MAX_DELAY` | float | `5.0` | Maximum backoff delay in seconds for retries |

## Modifying the Load Generator

Please see the [Locust
documentation](https://docs.locust.io/en/2.16.0/writing-a-locustfile.html) to
learn more about modifying the locustfile.
