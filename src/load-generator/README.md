# Load Generator

This service generates synthetic traffic for the OpenTelemetry Demo application using Locust.

## Configuration

The load generator supports the following environment variables for configuration:

| Variable Name | Description | Required | Default Value |
|---------------|-------------|----------|---------------|
| `LOCUST_WEB_PORT` | Port to expose the Locust web UI on | No | `8089` |
| `LOCUST_USERS` | Peak number of concurrent Locust users | No | `10` |
| `LOCUST_HOST` | Host URL of the frontend service to test | Yes | `http://frontend:8080` |
| `LOCUST_HEADLESS` | Run Locust in headless mode without web UI (set to `true` to enable) | No | `false` |
| `LOCUST_AUTOSTART` | Automatically start the load test when Locust starts (set to `true` to enable) | No | `false` |
| `LOCUST_BROWSER_TRAFFIC_ENABLED` | Enable browser-based traffic generation using Playwright | No | `true` |
| `LOCUST_WEB_HOST` | Host address to bind the Locust web UI to | No | `0.0.0.0` |
| `FLAGD_HOST` | Hostname of the FlagD feature flag service | No | `localhost` |
| `FLAGD_PORT` | Port of the FlagD service | No | `8013` |
| `FLAGD_OFREP_PORT` | Port of the FlagD OFREP API | No | `8016` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint for sending telemetry data | No | `http://otelcol:4317` |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | Metrics temporality preference | No | `cumulative` |
| `OTEL_RESOURCE_ATTRIBUTES` | Additional OpenTelemetry resource attributes | No |  |
| `OTEL_SERVICE_NAME` | Service name for OpenTelemetry telemetry | No | `load-generator` |
| `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION` | Protocol buffers implementation to use | No | `python` |

## Examples

### Example 1: Run in headless mode with 50 users for 10 minutes
```yaml
environment:
  LOCUST_HOST: http://frontend:8080
  LOCUST_HEADLESS: true
  LOCUST_AUTOSTART: true
  LOCUST_USERS: 50
  LOCUST_RUN_TIME: 10m
```

### Example 2: Disable browser traffic
```yaml
environment:
  LOCUST_BROWSER_TRAFFIC_ENABLED: false
```

### Example 3: Custom FlagD configuration
```yaml
environment:
  FLAGD_HOST: custom-flagd-host
  FLAGD_OFREP_PORT: 9016
```

## Modifying the Load Generator

Please see the [Locust
documentation](https://docs.locust.io/en/2.16.0/writing-a-locustfile.html) to
learn more about modifying the locustfile.
