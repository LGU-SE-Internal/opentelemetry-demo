# Load Generator

The load generator creates simulated traffic to the demo.

## Accessing the Load Generator

You can access the web interface to Locust at `http://localhost:8080/loadgen/`.

## Configuration

The load generator supports the following environment variables for configuration:

| Environment Variable | Description | Required | Default Value |
|----------------------|-------------|----------|---------------|
| `LOCUST_WEB_PORT` | Port on which the Locust web interface listens | No | `8089` |
| `LOCUST_USERS` | Number of concurrent simulated users to run | No | `5` |
| `LOCUST_HOST` | Base URL of the target service to send traffic to | No | `http://frontend-proxy:8080` |
| `LOCUST_WEB_HOST` | Host address the Locust web interface binds to | No | `load-generator` |
| `LOCUST_AUTOSTART` | Whether to automatically start the load test on container startup | No | `true` |
| `LOCUST_HEADLESS` | Run Locust in headless mode without the web interface | No | `false` |
| `FLAGD_HOST` | Hostname of the flagd feature flag service | No | `localhost` |
| `FLAGD_OFREP_PORT` | Port for the flagd OFREP (OpenFeature Remote Evaluation Protocol) endpoint | No | `8016` |

### Common Configuration Examples

#### 1. Run with 50 concurrent users
```yaml
environment:
  LOCUST_USERS: 50
```

#### 2. Run in headless mode (no web UI)
```yaml
environment:
  LOCUST_HEADLESS: "true"
  LOCUST_AUTOSTART: "true"
```

#### 3. Point to a custom flagd instance
```yaml
environment:
  FLAGD_HOST: "my-custom-flagd"
  FLAGD_OFREP_PORT: 8080
```

## Modifying the Load Generator

Please see the [Locust
documentation](https://docs.locust.io/en/2.16.0/writing-a-locustfile.html) to
learn more about modifying the locustfile.
