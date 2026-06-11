# OpenTelemetry Collector TLS/mTLS Configuration Guide

## Overview
This guide describes how to configure TLS (Transport Layer Security) and mTLS (mutual TLS) for the OpenTelemetry Collector in the OpenTelemetry Demo to secure communication between the collector and all connected services.

## Environment Variables
All TLS configuration supports environment variable substitution using the `${VAR_NAME}` syntax. The following environment variables are available:

| Variable Name | Purpose | Required For |
|---------------|---------|--------------|
| `OTELCOL_TLS_CA_FILE` | Trusted CA certificate file path | Server identity verification on exporters, client verification on receivers |
| `OTELCOL_TLS_SERVER_CERT_FILE` | Server TLS certificate path | TLS-enabled receivers |
| `OTELCOL_TLS_SERVER_KEY_FILE` | Server TLS private key path | TLS-enabled receivers |
| `OTELCOL_TLS_CLIENT_CA_FILE` | Client CA certificate path | mTLS-enabled receivers |
| `OTELCOL_TLS_CLIENT_CERT_FILE` | Client TLS certificate path | mTLS-enabled exporters |
| `OTELCOL_TLS_CLIENT_KEY_FILE` | Client TLS private key path | mTLS-enabled exporters |

## Configuration Examples

### 1. Enable TLS for OTLP Receivers (Server Side)
To configure the collector to accept only TLS-encrypted connections for OTLP gRPC and HTTP protocols:

```bash
# Set required environment variables
export OTELCOL_TLS_OTLP_GRPC_INSECURE=false
export OTELCOL_TLS_OTLP_HTTP_INSECURE=false
export OTELCOL_TLS_SERVER_CERT_FILE=/path/to/server.crt
export OTELCOL_TLS_SERVER_KEY_FILE=/path/to/server.key
export OTELCOL_TLS_CA_FILE=/path/to/ca.crt
```

When these variables are set, the collector will:
- Reject all unencrypted connection attempts to OTLP endpoints
- Require TLS 1.2 or higher for all connections
- Present the server certificate to connecting clients

### 2. Enable mTLS for OTLP Receivers
To enforce client certificate authentication (mTLS) for all incoming OTLP connections:

```bash
# Add to the TLS configuration above
export OTELCOL_TLS_CLIENT_CA_FILE=/path/to/client-ca.crt
```

With mTLS enabled:
- All clients must present a valid certificate signed by the CA specified in `OTELCOL_TLS_CLIENT_CA_FILE`
- Connections without valid client certificates will be rejected

### 3. Enable TLS for Exporters (Client Side)
To configure exporters to use TLS when connecting to their target endpoints:

#### Jaeger Exporter
```bash
export OTELCOL_TLS_JAEGER_INSECURE=false
export OTELCOL_TLS_CA_FILE=/path/to/ca.crt
```

#### Prometheus Exporter
```bash
export OTELCOL_TLS_PROMETHEUS_INSECURE=false
export OTELCOL_TLS_CA_FILE=/path/to/ca.crt
```

#### OpenSearch Exporter
```bash
export OTELCOL_TLS_OPENSEARCH_INSECURE=false
export OTELCOL_TLS_CA_FILE=/path/to/ca.crt
```

When TLS is enabled for exporters:
- Only TLS 1.2+ encrypted connections will be initiated
- The server's certificate will be verified against the provided CA file
- Unencrypted connection attempts will be refused

### 4. Enable mTLS for Exporters
To present a client certificate when connecting to mTLS-enabled endpoints:

```bash
# Add to the exporter TLS configuration above
export OTELCOL_TLS_CLIENT_CERT_FILE=/path/to/client.crt
export OTELCOL_TLS_CLIENT_KEY_FILE=/path/to/client.key
```

### 5. Disable Certificate Validation (Non-Production Only)
For testing environments with self-signed certificates, you can disable server certificate validation (not recommended for production):

```bash
export OTELCOL_TLS_JAEGER_INSECURE_SKIP_VERIFY=true
# Repeat for other exporters as needed:
# export OTELCOL_TLS_PROMETHEUS_INSECURE_SKIP_VERIFY=true
# export OTELCOL_TLS_OPENSEARCH_INSECURE_SKIP_VERIFY=true
# export OTELCOL_TLS_FIREPIT_INSECURE_SKIP_VERIFY=true
```

## Backward Compatibility
All TLS configurations default to `insecure: true`, so existing non-TLS deployments will continue to work without any changes required. No modifications are needed unless you explicitly want to enable TLS/mTLS.

## Troubleshooting
- If the collector fails to start with TLS configuration errors, verify that:
  1. All certificate/key file paths are correct and accessible by the collector process
  2. Certificate files are in PEM format and not corrupted
  3. Private keys are not password-protected (the collector does not support encrypted private keys)
- If connections are rejected with mTLS enabled:
  1. Ensure the client certificate is signed by the CA specified in `client_ca_file`
  2. Verify the client certificate has not expired
  3. Check that the certificate's Common Name or Subject Alternative Name matches the expected hostname/IP

## Notes
- Certificate lifecycle management (generation, rotation, etc.) is the responsibility of your deployment tooling (e.g. Kubernetes cert-manager, HashiCorp Vault)
- This configuration uses the built-in TLS support from the OpenTelemetry Collector core distribution, no additional dependencies are required
- Custom TLS cipher suite configuration is not exposed in this setup; the collector uses its default secure cipher suites
