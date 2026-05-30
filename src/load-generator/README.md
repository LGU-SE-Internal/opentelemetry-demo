# Load Generator

The load generator creates simulated traffic to the demo.

## Accessing the Load Generator

You can access the web interface to Locust at `http://localhost:8080/loadgen/`.

## Modifying the Load Generator

Please see the [Locust
documentation](https://docs.locust.io/en/2.16.0/writing-a-locustfile.html) to
learn more about modifying the locustfile.

## Health Endpoint

The load generator exposes a `/health` endpoint to check service initialization status:
- **200 OK** when initialization is complete: returns JSON payload `{"status": "healthy", "init_complete": true}`
- **503 Service Unavailable** when OTEL/OpenFeature initialization has not completed: returns JSON payload `{"status": "unavailable", "init_complete": false}`

This can be used by orchestration/CI pipelines to verify the service is ready before starting load test execution.
