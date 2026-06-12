# Fraud Detection Service

This service receives new orders by a Kafka topic and returns cases which are
suspected of fraud.

## Local Build

To build the protos and the service binary, run from the repo root:

```sh
cp -r ../../pb/ src/main/proto/
./gradlew shadowJar
```

## Docker Build

To build using Docker run from the repo root:

```sh
docker build -f ./src/fraud-detection/Dockerfile .
```

## Metrics

The service exposes Prometheus metrics at the unauthenticated endpoint `/q/metrics`.

### Business Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| fraud_check_requests_total | Counter | status (success/failure/invalid), reason (optional: error/validation reason) | Total count of processed fraud check gRPC requests |
| fraud_check_request_duration_seconds | Histogram | None | Distribution of fraud check request processing latency in seconds |
| fraud_check_success_rate_percent | Gauge | None | Percentage of successful fraud check requests (excluding invalid input requests) over the last 5 minute window |

### Standard JVM Runtime Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| jvm_memory_used_bytes | Gauge | area (heap/nonheap) | Current JVM memory usage |
| jvm_threads_active | Gauge | None | Current active JVM thread count |
| jvm_gc_collection_seconds_sum | Counter | gc | Total time spent in garbage collection |
| jvm_gc_collection_seconds_count | Counter | gc | Total number of garbage collection events |

