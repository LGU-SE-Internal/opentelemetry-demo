# Load Generator

This service generates synthetic traffic for the OpenTelemetry Demo application using Locust.

## Configuration

The load generator supports the following environment variables for configuration:

| Variable Name | Description | Required | Default Value |
|---------------|-------------|----------|---------------|
| `LOCUST_WEB_PORT` | Port to expose the Locust web UI on | No | `8089` |
| `LOCUST_USERS` | Peak number of concurrent Locust users | No | `10` |
| `LOCUST_RUN_TIME` | Duration to run the load test for (e.g. `10m`, `1h`) | No |  |
| `LOCUST_SPAWN_RATE` | Rate to spawn new users at (users per second) | No | `1` |
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

## people.json File

The `people.json` file contains sample user data that the load generator uses when simulating checkout processes. Each entry in this file represents a user persona with personal, address, and payment information used to complete mock purchases.

### Required Fields
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `email` | string | User's email address | `"larry_sergei@example.com"` |
| `name` | string | User's full name | `"Larry Sergei"` |
| `id` | string | Unique identifier for the user | `"usr_123456"` |
| `address` | object | User's physical address | See address fields below |
| `userCurrency` | string | Currency the user prefers to use | `"USD"` |
| `creditCard` | object | User's payment card information | See credit card fields below |

#### Address Object Fields
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `streetAddress` | string | Street address including number | `"1600 Amphitheatre Parkway"` |
| `zipCode` | string | Postal/zip code | `"94043"` |
| `city` | string | City name | `"Mountain View"` |
| `state` | string | State/region code | `"CA"` |
| `country` | string | Country name | `"United States"` |

#### Credit Card Object Fields
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `creditCardNumber` | string | Payment card number (formatted with hyphens) | `"4432-8015-6152-0454"` |
| `creditCardExpirationMonth` | integer | 1-indexed expiration month | `1` |
| `creditCardExpirationYear` | integer | 4-digit expiration year | `2039` |
| `creditCardCvv` | integer | Card verification value | `672` |

### Optional Fields
There are currently no optional fields for people.json entries. All fields documented above are required.

### Important Notes
- The file must contain a valid JSON array of user entries
- All entries must follow the schema documented above for the load generator to work correctly
- Any invalid entries or JSON formatting errors will cause the load generator to fail on startup

## Testing

Unit tests for the load generator helper functions are available in `test_locustfile.py`.

### Prerequisites
Install test dependencies:
```bash
pip install pytest
```

### Running Tests
```bash
cd src/load-generator
pytest test_locustfile.py -v
```

The tests run independently without requiring any external services or dependencies beyond pytest.
