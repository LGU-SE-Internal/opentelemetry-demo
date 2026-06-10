#!/bin/sh

set -e

DEFAULT_CONFIG_PATH="/etc/flagd/demo_flags.json"
CUSTOM_CONFIG_PATH="${FLAGD_CUSTOM_CONFIG_PATH:-/var/lib/flagd/config/flags.json}"

# Check if custom config exists and is readable
if [ -f "$CUSTOM_CONFIG_PATH" ] && [ -r "$CUSTOM_CONFIG_PATH" ]; then
    echo "Loading custom flag configuration from $CUSTOM_CONFIG_PATH"
    CONFIG_PATH="$CUSTOM_CONFIG_PATH"
else
    echo "WARNING: Custom config file $CUSTOM_CONFIG_PATH not found or not readable. Falling back to default config at $DEFAULT_CONFIG_PATH"
    CONFIG_PATH="$DEFAULT_CONFIG_PATH"
fi

# Start flagd with the selected config
exec flagd start --uri "file:$CONFIG_PATH"
set -e

# Initialize default config path
DEFAULT_CONFIG_PATH="/etc/flagd/demo.flagd.json"
# Default custom config path
FLAGD_CUSTOM_CONFIG_PATH="${FLAGD_CUSTOM_CONFIG_PATH:-/var/lib/flagd/config/flags.json}"

# Determine which config to use
CONFIG_PATH="$DEFAULT_CONFIG_PATH"
if [ -f "$FLAGD_CUSTOM_CONFIG_PATH" ]; then
    # Check if file is readable
    if [ -r "$FLAGD_CUSTOM_CONFIG_PATH" ]; then
        CONFIG_PATH="$FLAGD_CUSTOM_CONFIG_PATH"
        echo "Using custom flag configuration from $FLAGD_CUSTOM_CONFIG_PATH"
    else
        echo "WARNING: Custom flag configuration file $FLAGD_CUSTOM_CONFIG_PATH exists but is not readable. Falling back to default config."
    fi
else
    echo "No custom flag configuration found at $FLAGD_CUSTOM_CONFIG_PATH. Using default config."
fi

# Initialize start arguments
START_ARGS="start --uri file:$CONFIG_PATH"

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
