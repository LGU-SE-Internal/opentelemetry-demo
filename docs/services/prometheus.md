# Prometheus Service Configuration

## Overview
This service provides Prometheus monitoring for the OpenTelemetry Demo, with configurable parameters for storage retention, scraping, and resource allocation.

## Configuration Parameters

### Environment Variables / Helm Values
| Parameter | Type | Description | Valid Format | Default Value |
|-----------|------|-------------|--------------|---------------|
| `PROMETHEUS_RETENTION_TIME` | String | Prometheus TSDB retention time | Valid Prometheus duration (e.g. `15d`, `24h`, `360m`) | `15d` |
| `PROMETHEUS_RETENTION_SIZE` | String | Prometheus TSDB retention size limit | Valid Prometheus size (e.g. `10GB`, `512MB`, `1TiB`) or `0` to disable | `0` (disabled) |
| `PROMETHEUS_SCRAPE_INTERVAL` | String | Global Prometheus metrics scrape interval | Valid Prometheus duration (e.g. `15s`, `1m`, `5m`) | `15s` |

### Helm Resource Parameters
| Parameter | Type | Description | Default Value |
|-----------|------|-------------|---------------|
| `prometheus.resources.requests.cpu` | String | CPU resource request for Prometheus container | `100m` |
| `prometheus.resources.requests.memory` | String | Memory resource request for Prometheus container | `256Mi` |
| `prometheus.resources.limits.cpu` | String | CPU resource limit for Prometheus container | `500m` |
| `prometheus.resources.limits.memory` | String | Memory resource limit for Prometheus container | `1Gi` |

## Validation
All configuration parameters are validated before Prometheus starts. If any parameter is invalid, the container will fail to start with a descriptive error message indicating the invalid value and expected format.
