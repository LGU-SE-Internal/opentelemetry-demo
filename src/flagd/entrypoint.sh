#!/bin/sh
set -e

# Initialize start arguments
START_ARGS="start --uri file:./etc/flagd/demo.flagd.json"

# Check TLS configuration
if [ -n "$FLAGD_TLS_SERVER_CERT_PATH" ] && [ -n "$FLAGD_TLS_SERVER_KEY_PATH" ]; then
    START_ARGS="$START_ARGS --cert $FLAGD_TLS_SERVER_CERT_PATH --key $FLAGD_TLS_SERVER_KEY_PATH"
    
    # Check for mTLS
    if [ "$FLAGD_TLS_CLIENT_AUTH_REQUIRED" = "true" ]; then
        if [ -z "$FLAGD_TLS_CA_CERT_PATH" ]; then
            echo "ERROR: FLAGD_TLS_CLIENT_AUTH_REQUIRED is true but FLAGD_TLS_CA_CERT_PATH is not set"
            exit 1
        fi
        START_ARGS="$START_ARGS --client-ca $FLAGD_TLS_CA_CERT_PATH"
    fi
elif [ -n "$FLAGD_TLS_SERVER_CERT_PATH" ]; then
    echo "ERROR: FLAGD_TLS_SERVER_CERT_PATH is set but FLAGD_TLS_SERVER_KEY_PATH is missing"
    exit 1
elif [ -n "$FLAGD_TLS_SERVER_KEY_PATH" ]; then
    echo "ERROR: FLAGD_TLS_SERVER_KEY_PATH is set but FLAGD_TLS_SERVER_CERT_PATH is missing"
    exit 1
fi

# Execute the flagd command
exec flagd $START_ARGS
