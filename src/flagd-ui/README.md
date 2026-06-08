# Flagd-ui

This application provides a user interface for configuring the feature
flags of the flagd service.

This is a [Phoenix](https://www.phoenixframework.org/) project.

## Running the application

The application can be run with the rest of the demo using the documented
[docker compose or make commands](https://opentelemetry.io/docs/demo/#running-the-demo).

## Local development

* Run `mix setup` to install and setup dependencies
* Create a `data` folder: `mkdir data`.
* Copy [../flagd/demo.flagd.json](../flagd/demo.flagd.json) to `./data/demo.flagd.json`
  * `cp ../flagd/demo.flagd.json ./data/demo.flagd.json`
* Start Phoenix endpoint with `mix phx.server` or inside IEx with `iex -S mix phx.server`

Now you can visit `localhost:4000` from your browser.

## Programmatic use through the API

This service exposes a REST API to ease its usage in a programmatic way for
power users.

You can read the current configuration using this HTTP call:

```json
$ curl localhost:8080/feature/api/read | jq

{
  "flags": {
    "adFailure": {
      "defaultVariant": "off",
      "description": "Fail ad service",
      "state": "ENABLED",
      "variants": {
        "off": false,
        "on": true
      }
    },
    "adHighCpu": {
      "defaultVariant": "off",
      "description": "Triggers high cpu load in the ad service",
      "state": "ENABLED",
      "variants": {
        "off": false,
        "on": true
      }
    },
    "adManualGc": {
      "defaultVariant": "off",
      "description": "Triggers full manual garbage collections in the ad service",
      "state": "ENABLED",
      "variants": {
        "off": false,
        "on": true
      }
    },
    ...
  }
}
```

You can also write a new settings file by sending a new configuration inside
the `data` field of a POST request body.

Bear in mind that _all_ the data will be rewritten by this write operation.

```sh
$ curl --header "Content-Type: application/json" \
  --request POST \
  --data '{"data": {"$schema":"https://flagd.dev/schema/v0/flags.json","flags":{"adFailure":{"defaultVariant":"on","description":"Fail ad service","state":"ENABLED","variants":{"off":false,"on":true}}...' \
  http://localhost:8080/feature/api/write
```

In addition to the `/read` and `/write` endpoint, we also offer these endpoint
to stay compatible with the old version of Flagd-ui:

* `/read-file` (`GET`)
* `/write-to-file` (`POST`)

## Health Check Endpoints

The service exposes two unauthenticated health check endpoints for Kubernetes monitoring:

1. **GET /health/live**
   - Purpose: Liveness probe to verify the service process is running
   - Authentication: None (publicly accessible)
   - Success Response (200 OK):
     ```json
     {"status": "ok", "check": "liveness"}
     ```
   - Failure: If the service is not running, Kubernetes will receive connection timeout/refused error

2. **GET /health/ready**
   - Purpose: Readiness probe to verify the service is fully initialized and ready to serve user traffic
   - Authentication: None (publicly accessible)
   - Success Response (200 OK):
     ```json
     {"status": "ok", "check": "readiness"}
     ```
   - Failure Response (503 Service Unavailable):
     ```json
     {"status": "error", "check": "readiness", "reason": "<error description>"}
     ```

### Kubernetes Probe Configuration Example
```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
```

## Production TLS Configuration

For production deployments, you can enable encrypted HTTPS communication for the flagd-ui service using the following environment variables:

### Environment Variables
| Variable Name | Type | Required | Default | Description |
|---------------|------|----------|---------|-------------|
| `FLAGD_UI_TLS_ENABLED` | Boolean | No | `false` | Toggles TLS support for the flagd-ui HTTP server. Valid values: `true`, `false` |
| `FLAGD_UI_TLS_CERT_PATH` | String | Yes (if TLS enabled) | `` | Absolute file path to PEM-encoded TLS server certificate file |
| `FLAGD_UI_TLS_KEY_PATH` | String | Yes (if TLS enabled) | `` | Absolute file path to PEM-encoded TLS server private key file |
| `FLAGD_UI_TLS_CLIENT_CA_PATH` | String | No | `` | Absolute file path to PEM-encoded CA certificate bundle. If provided, mTLS client authentication is enabled |
| `FLAGD_UI_TLS_CLIENT_REQUIRE` | Boolean | No | `true` (if client CA path provided) | Controls whether client certificate is mandatory when mTLS is enabled |

### Basic TLS Setup (HTTPS only)
1. Ensure you have a valid TLS certificate and private key in PEM format accessible to the service process
2. Set the following environment variables:
```bash
FLAGD_UI_TLS_ENABLED=true
FLAGD_UI_TLS_CERT_PATH=/path/to/server.crt
FLAGD_UI_TLS_KEY_PATH=/path/to/server.key
```
3. Restart the service. The service will now listen for HTTPS connections on port 4000 (default) instead of HTTP.

### mTLS Setup (Mutual Authentication)
To require clients to present a valid certificate to connect to the service:
1. Complete the basic TLS setup above
2. Add your CA certificate bundle path:
```bash
FLAGD_UI_TLS_CLIENT_CA_PATH=/path/to/ca.crt
# Optional: Make client certificates optional
# FLAGD_UI_TLS_CLIENT_REQUIRE=false
```

### Certificate Management Best Practices
- Use certificates issued by a trusted public CA or internal PKI system
- Ensure certificate files have restrictive file permissions (recommended: 0600 for private keys, 0644 for certificates)
- Implement certificate rotation processes before certificates expire
- Private keys should never be stored in version control or container images, mount them at runtime using secrets management systems
- For automatic certificate issuance and renewal, use tools like cert-manager in Kubernetes or Let's Encrypt clients with automated reload workflows

### Troubleshooting
- If the service fails to start with "Missing required TLS configuration" error: Verify both `FLAGD_UI_TLS_CERT_PATH` and `FLAGD_UI_TLS_KEY_PATH` are set and non-empty when `FLAGD_UI_TLS_ENABLED=true`
- If the service fails to start with invalid file errors: Verify the certificate/key/CA files exist, are readable by the service user, and are valid PEM format
- If clients cannot connect with TLS handshake errors: Verify the client trusts the server certificate CA, and for mTLS, the client certificate is signed by the configured CA and not expired

## Health Check Endpoints

The service exposes two health check endpoints for Kubernetes monitoring:

### Liveness Probe (`GET /health/live` or `GET /health/liveness`)
- **Purpose**: Verify the service process is running
- **Authentication**: None (publicly accessible)
- **Success Response**:
  - Status Code: `200 OK`
  - Content-Type: `application/json`
  - Body:
    ```json
    {
      "status": "ok",
      "check": "liveness",
      "timestamp": "<UTC ISO 8601 timestamp>"
    }
    ```
- **Failure Conditions**: Only fails if the service process is not running (connection timeout/refused)
- **Kubernetes Usage**:
  ```yaml
  livenessProbe:
    httpGet:
      path: /health/live
      port: 4000
    initialDelaySeconds: 5
    periodSeconds: 10
  ```

### Readiness Probe (`GET /health/ready` or `GET /health/readiness`)
- **Purpose**: Verify the service is fully initialized and ready to serve user traffic
- **Authentication**: None (publicly accessible)
- **Success Response**:
  - Status Code: `200 OK`
  - Content-Type: `application/json`
  - Body:
    ```json
    {
      "status": "ok",
      "check": "readiness",
      "timestamp": "<UTC ISO 8601 timestamp>"
    }
    ```
- **Failure Response**:
  - Status Code: `503 Service Unavailable`
  - Content-Type: `application/json`
  - Body:
    ```json
    {
      "status": "error",
      "check": "readiness",
      "reason": "<comma-separated list of unready components>",
      "timestamp": "<UTC ISO 8601 timestamp>"
    }
    ```
- **Checks Performed**:
  1. Phoenix server is fully booted
  2. Static assets are compiled and available
  3. Connection to flagd service is established (if configured)
- **Kubernetes Usage**:
  ```yaml
  readinessProbe:
    httpGet:
      path: /health/ready
      port: 4000
    initialDelaySeconds: 10
    periodSeconds: 5
  ```

## Health Check Endpoints

The flagd-ui service provides two health check endpoints for Kubernetes monitoring:

### 1. Liveness Probe (`GET /health/live`)
- **Purpose**: Verify that the service process is running
- **Authentication**: No required credentials (publicly accessible)
- **Success Response**:
  - Status Code: `200 OK`
  - Content-Type: `application/json`
  - Body:
    ```json
    {"status": "ok", "check": "liveness"}
    ```
- **Failure Condition**: If the service is not running, Kubernetes will receive a connection timeout or connection refused error.

### 2. Readiness Probe (`GET /health/ready`)
- **Purpose**: Verify that the service is fully initialized and ready to serve user traffic
- **Authentication**: No required credentials (publicly accessible)
- **Success Response**:
  - Status Code: `200 OK`
  - Content-Type: `application/json`
  - Body:
    ```json
    {"status": "ok", "check": "readiness"}
    ```
- **Failure Response (when not ready)**:
  - Status Code: `503 Service Unavailable`
  - Content-Type: `application/json`
  - Body:
    ```json
    {"status": "error", "check": "readiness", "reason": "<uninitialized components>"}
    ```

### Kubernetes Probe Configuration Example
```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
```
