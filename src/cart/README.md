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
| `CART_SERVICE_VALKEY_TLS_ENABLED` | Boolean | `false` | Flag to enable TLS encryption for Valkey connections |
| `CART_SERVICE_VALKEY_CA_CERT_PATH` | String | Empty | Optional path to PEM-formatted CA certificate file for validating Valkey server TLS certificate |
| `CART_SERVICE_VALKEY_CLIENT_CERT_PATH` | String | Empty | Optional path to PEM-formatted client certificate file for mTLS authentication |
| `CART_SERVICE_VALKEY_CLIENT_KEY_PATH` | String | Empty | Optional path to PEM-formatted client private key file, required if client certificate is provided |

### TLS Configuration Notes
- When TLS is enabled, minimum TLS version 1.2 is enforced for all connections
- Custom CA certificates are used to validate the server certificate instead of system trusted CAs when provided
- Client certificate and key paths must both be provided to enable mTLS authentication
- Invalid configuration (e.g. missing key for client cert, non-existent certificate files) will cause the service to fail fast on startup with a ConfigurationException
