# Flagd Service Configuration

## Overview
Flagd is a feature flag evaluation service used in the OpenTelemetry Demo. This service now supports TLS and mTLS encryption for client connections.

## Configuration Options

### TLS Configuration Environment Variables
All TLS variables are optional. When not set, the service defaults to plaintext mode for backward compatibility.

| Variable Name | Type | Default | Description |
|---------------|------|---------|-------------|
| `FLAGD_TLS_SERVER_CERT_PATH` | string | "" | Absolute path to server TLS certificate file (PEM format). Required for server-only TLS and mTLS modes. |
| `FLAGD_TLS_SERVER_KEY_PATH` | string | "" | Absolute path to server TLS private key file (PEM format). Required for server-only TLS and mTLS modes. |
| `FLAGD_TLS_CA_CERT_PATH` | string | "" | Absolute path to CA certificate bundle file (PEM format). Required for mTLS mode to authenticate client certificates. |
| `FLAGD_TLS_CLIENT_AUTH_REQUIRED` | boolean | false | Set to `true` to enable mutual TLS authentication (requires all three TLS variables above to be set). |

## Usage Modes

### 1. Plaintext Mode (Default)
No TLS configuration required. All connections are unencrypted. This maintains backward compatibility with existing deployments.

### 2. Server-Only TLS Mode
Encrypts traffic between clients and flagd, but does not authenticate clients.
Configuration:
```bash
FLAGD_TLS_SERVER_CERT_PATH=/path/to/server.crt
FLAGD_TLS_SERVER_KEY_PATH=/path/to/server.key
```

### 3. Mutual TLS (mTLS) Mode
Encrypts traffic and requires clients to present valid certificates signed by the provided CA.
Configuration:
```bash
FLAGD_TLS_SERVER_CERT_PATH=/path/to/server.crt
FLAGD_TLS_SERVER_KEY_PATH=/path/to/server.key
FLAGD_TLS_CA_CERT_PATH=/path/to/ca.crt
FLAGD_TLS_CLIENT_AUTH_REQUIRED=true
```

## Error Conditions
- If server cert is provided without server key: Service fails to start with "missing TLS private key" error
- If server key is provided without server cert: Service fails to start with "missing TLS certificate" error
- If mTLS is enabled without CA cert: Service fails to start with "missing CA certificate bundle for client authentication" error
- If any provided certificate/key file is unreadable: Service fails to start with file access error

