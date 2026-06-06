# Payment Service

This service is responsible for processing and validating payments through the
application.

## Local Build

Copy the `demo.proto` file to this directory and run `npm ci`

## Docker Build

From the root directory, run:

```sh
docker compose build payment
```

## Configuration

### TLS/mTLS Configuration (Optional)
The payment service supports optional TLS encryption and mutual TLS (mTLS) authentication for incoming gRPC connections to secure sensitive payment transaction data. TLS/mTLS are completely opt-in features, default behavior remains unencrypted for local development.

All configuration variables are optional:
| Variable | Type | Description |
|----------|------|-------------|
| `PAYMENT_SERVICE_TLS_CERT_PATH` | string | Absolute path to server TLS certificate file (PEM format). Required to enable TLS mode. |
| `PAYMENT_SERVICE_TLS_KEY_PATH` | string | Absolute path to server TLS private key file (PEM format). Required to enable TLS mode. |
| `PAYMENT_SERVICE_TLS_CLIENT_CA_PATH` | string | Absolute path to CA certificate bundle (PEM format) for client certificate validation. Enables mTLS when set (requires TLS mode to be enabled). |

### Setup Examples

#### 1. TLS-only Mode (Server Authentication Only)
Provide only the server certificate and private key paths to enable encrypted TLS connections without client certificate validation:
```bash
export PAYMENT_SERVICE_TLS_CERT_PATH="/path/to/server.crt"
export PAYMENT_SERVICE_TLS_KEY_PATH="/path/to/server.key"
```

#### 2. Mutual TLS (mTLS) Mode (Server + Client Authentication)
Add the client CA certificate path to require all incoming clients present a valid certificate signed by the trusted CA:
```bash
export PAYMENT_SERVICE_TLS_CERT_PATH="/path/to/server.crt"
export PAYMENT_SERVICE_TLS_KEY_PATH="/path/to/server.key"
export PAYMENT_SERVICE_TLS_CLIENT_CA_PATH="/path/to/ca.crt"
```

#### Notes:
- Certificate files are read once at service startup, hot reloading is not supported
- Certificates must be provisioned by your deployment environment (certificate generation/renewal is out of scope for this service)
- When no TLS variables are set, the service uses insecure unencrypted connections by default for local development workflows
