#!/bin/sh
set -e

# Initialize start arguments
START_ARGS="start --uri file:./etc/flagd/demo.flagd.json"

# Check TLS configuration
if [ -n "$FLAGD_TLS_CERT_PATH" ] && [ -n "$FLAGD_TLS_KEY_PATH" ]; then
    START_ARGS="$START_ARGS --cert $FLAGD_TLS_CERT_PATH --key $FLAGD_TLS_KEY_PATH"
    
    # Check for mTLS
    if [ -n "$FLAGD_TLS_CA_CERT_PATH" ]; then
        START_ARGS="$START_ARGS --client-ca $FLAGD_TLS_CA_CERT_PATH"
    fi
elif [ -n "$FLAGD_TLS_CERT_PATH" ]; then
    echo "ERROR: FLAGD_TLS_CERT_PATH is set but FLAGD_TLS_KEY_PATH is missing"
    exit 1
elif [ -n "$FLAGD_TLS_KEY_PATH" ]; then
    echo "ERROR: FLAGD_TLS_KEY_PATH is set but FLAGD_TLS_CERT_PATH is missing"
    exit 1
fi

# Configure graceful shutdown timeout
DEFAULT_SHUTDOWN_TIMEOUT="30s"
SHUTDOWN_TIMEOUT="${FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT:-$DEFAULT_SHUTDOWN_TIMEOUT}"

# Validate timeout format (supports s/m/h units)
if ! echo "$SHUTDOWN_TIMEOUT" | grep -Eq '^[0-9]+(s|m|h)$'; then
    echo "ERROR: Invalid FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT format: $SHUTDOWN_TIMEOUT. Must be number followed by s/m/h (e.g. 10s, 1m)"
    exit 1
fi

START_ARGS="$START_ARGS --graceful-shutdown-timeout $SHUTDOWN_TIMEOUT"

# Function to forward signals to child process
forward_signal() {
    local signal=$1
    echo "Received $signal, forwarding to flagd process..."
    kill -$signal $FLAGD_PID 2>/dev/null
}

# Set up signal handlers
trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT

# Start flagd as child process
echo "Starting flagd with args: $START_ARGS"
flagd $START_ARGS &
FLAGD_PID=$!

# Wait for flagd to exit
wait $FLAGD_PID
EXIT_CODE=$?

echo "flagd process exited with code $EXIT_CODE"
exit $EXIT_CODE
