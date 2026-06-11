# Cart Service

This service stores user shopping carts in Valkey.

## Local Build

Run `dotnet restore` and `dotnet build`.

## Docker Build

From the root directory of this repository, run:

```sh
docker compose build cart
```

## Configuration

The following environment variables can be used to configure the Valkey connection:

| Variable Name | Type | Default | Description |
|---------------|------|---------|-------------|
| `CART_VALKEY_TLS_ENABLED` | Boolean | `false` | Flag to enable TLS encryption for Valkey connections |
| `CART_VALKEY_TLS_INSECURE_SKIP_VERIFY` | Boolean | `false` | Flag to skip TLS certificate validation (development use only, not recommended for production) |
| `CART_VALKEY_CA_CERT_PATH` | String | Empty | Optional path to PEM-formatted CA certificate file for validating Valkey server TLS certificate |
| `CART_VALKEY_CLIENT_CERT_PATH` | String | Empty | Optional path to PEM-formatted client certificate file for mTLS authentication |
| `CART_VALKEY_CLIENT_KEY_PATH` | String | Empty | Optional path to PEM-formatted client private key file, required if client certificate is provided |

### TLS Configuration Notes
- When TLS is enabled, minimum TLS version 1.2 is enforced for all connections
- Custom CA certificates are used to validate the server certificate instead of system trusted CAs when provided
- Client certificate and key paths must both be provided to enable mTLS authentication
- Invalid configuration (e.g. missing key for client cert, non-existent certificate files) will cause the service to fail fast on startup with a ConfigurationException

## Kubernetes Deployment Configuration

### Enable TLS Connection to Valkey
1. Create a Kubernetes secret containing your Valkey CA certificate:
   ```sh
   kubectl create secret generic valkey-ca-cert --from-file=ca.crt=/path/to/your/ca.crt
   ```
2. Set the following environment variables in the cartservice deployment manifest:
   - `CART_VALKEY_TLS_ENABLED=true`
   - `CART_VALKEY_CA_CERT_PATH=/certs/valkey/ca/ca.crt`

### Enable mTLS Authentication to Valkey
Follow the TLS enablement steps above, then:
1. Create Kubernetes secrets for your client certificate and private key:
   ```sh
   kubectl create secret generic valkey-client-cert --from-file=tls.crt=/path/to/your/client.crt
   kubectl create secret generic valkey-client-key --from-file=tls.key=/path/to/your/client.key
   ```
2. Add the following environment variables to the cartservice deployment manifest:
   - `CART_VALKEY_CLIENT_CERT_PATH=/certs/valkey/client/tls.crt`
   - `CART_VALKEY_CLIENT_KEY_PATH=/certs/valkey/client/tls.key`

### Disable Certificate Validation for Development
For non-production testing only, add the following environment variable to skip TLS certificate validation:
- `CART_VALKEY_TLS_INSECURE_SKIP_VERIFY=true`
