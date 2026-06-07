# Prometheus Service Configuration

This document describes the configurable parameters for the Prometheus service in the OpenTelemetry Demo.

## Configuration Parameters

| Parameter | Type | Origin | Description | Default Value |
|-----------|------|--------|-------------|---------------|
| `PROMETHEUS_RETENTION_TIME` | String | Environment Variable / Helm Value | Prometheus TSDB retention time, valid duration string (e.g. `15d`, `24h`) | `15d` |
| `PROMETHEUS_RETENTION_SIZE` | String | Environment Variable / Helm Value | Prometheus TSDB retention size, valid size string (e.g. `10GB`, `512MB`). Set to `0` to disable size-based retention. | `0` |
| `PROMETHEUS_SCRAPE_INTERVAL` | String | Environment Variable / Helm Value | Global Prometheus scrape interval, valid duration string | `15s` |
| `prometheus.resources.requests.cpu` | String | Helm Value | CPU resource request for Prometheus container | `100m` |
| `prometheus.resources.requests.memory` | String | Helm Value | Memory resource request for Prometheus container | `256Mi` |
| `prometheus.resources.limits.cpu` | String | Helm Value | CPU resource limit for Prometheus container | `500m` |
| `prometheus.resources.limits.memory` | String | Helm Value | Memory resource limit for Prometheus container | `1Gi` |

## Validation

All configuration parameters are validated before Prometheus starts. If any parameter is invalid, the container will fail to start with a descriptive error message:

- **Invalid retention time**: "Invalid PROMETHEUS_RETENTION_TIME value: <value>. Must be valid Prometheus duration (e.g. 15d, 24h)"
- **Invalid retention size**: "Invalid PROMETHEUS_RETENTION_SIZE value: <value>. Must be valid Prometheus size (e.g. 10GB, 512MB) or 0 to disable"
- **Invalid scrape interval**: "Invalid PROMETHEUS_SCRAPE_INTERVAL value: <value>. Must be valid Prometheus duration (e.g. 15s, 1m)"

## Backwards Compatibility

When no custom environment variables or Helm values are set, all parameters retain their original default values, ensuring full backwards compatibility with previous deployments.

