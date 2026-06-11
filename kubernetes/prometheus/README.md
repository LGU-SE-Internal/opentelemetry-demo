# Prometheus Service for OpenTelemetry Demo

This directory contains Kubernetes manifests for running Prometheus as part of the OpenTelemetry Demo.

## Configuration

### Retention Settings

Prometheus TSDB storage retention is configured with the following defaults:
- **Retention Time**: 30 days (`30d`)
- **Retention Size**: 3.5 GiB (`3.5Gi`)

### Modifying Retention Values

To override the default retention values for different deployment environments, you can modify the environment variables in the deployment manifest or use Kustomize/Helm patches:

1. Update the `PROMETHEUS_RETENTION_TIME` environment variable to change the retention period (use valid Prometheus duration format: `s` for seconds, `m` for minutes, `h` for hours, `d` for days, `w` for weeks, `y` for years)
2. Update the `PROMETHEUS_RETENTION_SIZE` environment variable to change the maximum storage size (use valid Kubernetes resource quantity format: `Ki`, `Mi`, `Gi`, `Ti`, etc.)

#### Important Note
The retention size **must not exceed the allocated PersistentVolumeClaim (PVC) capacity**. The default retention size is set to 3.5Gi, which is 87.5% of the default 4Gi PVC capacity, to leave buffer space for write-ahead logs and temporary files and avoid unexpected out-of-disk errors.

## CLI Arguments

The Prometheus container is started with the following explicit retention flags:
- `--storage.tsdb.retention.time=${PROMETHEUS_RETENTION_TIME}`
- `--storage.tsdb.retention.size=${PROMETHEUS_RETENTION_SIZE}`
