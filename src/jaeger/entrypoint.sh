#!/bin/bash
set -eo pipefail

# Initialize variables
JAEGER_PID=0
JAEGER_EXIT_CODE=0
SHUTDOWN_GRACE_PERIOD_SECONDS=${SHUTDOWN_GRACE_PERIOD_SECONDS:-30}
# Validate grace period, default to 30 if <=0
if [[ $SHUTDOWN_GRACE_PERIOD_SECONDS -le 0 ]]; then
    SHUTDOWN_GRACE_PERIOD_SECONDS=30
fi

# Cleanup function to remove temporary resources
cleanup() {
    echo "Cleaning up temporary files in /tmp/jaeger/"
    rm -rf /tmp/jaeger/* 2>/dev/null || true
}

# Signal handler for SIGINT and SIGTERM
handle_shutdown_signal() {
    local SIGNAL=$1
    echo "Received $SIGNAL signal, initiating graceful shutdown..."
    
    if [[ $JAEGER_PID -ne 0 ]]; then
        echo "Sending SIGTERM to jaeger-query process (PID $JAEGER_PID)"
        kill -TERM "$JAEGER_PID" 2>/dev/null || true
        
        # Wait for grace period
        local WAIT_COUNT=0
        while kill -0 "$JAEGER_PID" 2>/dev/null && [[ $WAIT_COUNT -lt $SHUTDOWN_GRACE_PERIOD_SECONDS ]]; do
            sleep 1
            WAIT_COUNT=$((WAIT_COUNT + 1))
        done
        
        # If still running after grace period, force kill
        if kill -0 "$JAEGER_PID" 2>/dev/null; then
            echo "jaeger-query did not exit within $SHUTDOWN_GRACE_PERIOD_SECONDS seconds, sending SIGKILL"
            kill -KILL "$JAEGER_PID" 2>/dev/null || true
            wait "$JAEGER_PID" 2>/dev/null || true
            JAEGER_EXIT_CODE=137
        fi
    fi
    
    cleanup
    exit $JAEGER_EXIT_CODE
}

# Set up signal traps
trap 'handle_shutdown_signal SIGINT' INT
trap 'handle_shutdown_signal SIGTERM' TERM

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

# Add any command line arguments passed to the entrypoint
cmd+=("$@")

# Execute jaeger-query in background
echo "Starting jaeger-query with command: ${cmd[*]}"
"${cmd[@]}" &
JAEGER_PID=$!

# Wait for jaeger-query to exit
wait "$JAEGER_PID" 2>/dev/null || JAEGER_EXIT_CODE=$?

# Cleanup and exit with jaeger's exit code
cleanup
exit $JAEGER_EXIT_CODE
