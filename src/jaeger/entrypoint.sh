#!/bin/bash
set -eo pipefail

# Initialize command array
cmd=("jaeger-query")

# Read TLS environment variables
TLS_CERT=${JAEGER_QUERY_TLS_CERT_PATH:-}
TLS_KEY=${JAEGER_QUERY_TLS_KEY_PATH:-}
TLS_CA=${JAEGER_QUERY_TLS_CA_PATH:-}
TLS_CLIENT_AUTH=${JAEGER_QUERY_TLS_CLIENT_AUTH:-none}

# Validate TLS configuration
if [[ -n "$TLS_CERT" && -z "$TLS_KEY" ]]; then
    echo "TLS certificate path provided but private key path missing" >&2
    exit 1
fi

if [[ -z "$TLS_CERT" && -n "$TLS_KEY" ]]; then
    echo "TLS private key path provided but certificate path missing" >&2
    exit 1
fi

if [[ "$TLS_CLIENT_AUTH" == "required" && -z "$TLS_CA" ]]; then
    echo "mTLS client auth enabled but CA certificate path missing" >&2
    exit 1
fi

# Validate file existence and readability
if [[ -n "$TLS_CERT" ]]; then
    if [[ ! -f "$TLS_CERT" || ! -r "$TLS_CERT" ]]; then
        echo "Failed to read certificate file at $TLS_CERT: $(ls -ld "$TLS_CERT" 2>&1 || echo "File not found")" >&2
        exit 1
    fi
fi

if [[ -n "$TLS_KEY" ]]; then
    if [[ ! -f "$TLS_KEY" || ! -r "$TLS_KEY" ]]; then
        echo "Failed to read private key file at $TLS_KEY: $(ls -ld "$TLS_KEY" 2>&1 || echo "File not found")" >&2
        exit 1
    fi
fi

if [[ -n "$TLS_CA" ]]; then
    if [[ ! -f "$TLS_CA" || ! -r "$TLS_CA" ]]; then
        echo "Failed to read CA certificate file at $TLS_CA: $(ls -ld "$TLS_CA" 2>&1 || echo "File not found")" >&2
        exit 1
    fi
fi

# Add TLS flags if certificate and key are provided
if [[ -n "$TLS_CERT" && -n "$TLS_KEY" ]]; then
    cmd+=("--query.tls.enabled=true")
    cmd+=("--query.tls.cert=$TLS_CERT")
    cmd+=("--query.tls.key=$TLS_KEY")
    
    # Add CA and client auth if configured
    if [[ -n "$TLS_CA" ]]; then
        cmd+=("--query.tls.client-ca=$TLS_CA")
        cmd+=("--query.tls.client-auth=$TLS_CLIENT_AUTH")
    fi
fi

# Execute jaeger-query
echo "Starting jaeger-query with command: ${cmd[*]}"
exec "${cmd[@]}"
