#!/bin/sh
set -e

DEFAULT_CONFIG_PATH="/etc/flagd/demo_flags.json"
CUSTOM_CONFIG_PATH=${FLAGD_CUSTOM_CONFIG_PATH:-"/var/lib/flagd/config/flags.json"}

if [ -f "$CUSTOM_CONFIG_PATH" ] && [ -r "$CUSTOM_CONFIG_PATH" ]; then
    echo "Using custom flag configuration from $CUSTOM_CONFIG_PATH"
    CONFIG_PATH="$CUSTOM_CONFIG_PATH"
else
    if [ -f "$CUSTOM_CONFIG_PATH" ]; then
        echo "WARNING: Custom flag configuration file at $CUSTOM_CONFIG_PATH exists but is not readable. Falling back to default config."
    else
        echo "No custom flag configuration found at $CUSTOM_CONFIG_PATH, using default config at $DEFAULT_CONFIG_PATH"
    fi
    CONFIG_PATH="$DEFAULT_CONFIG_PATH"
fi

exec flagd start --config "$CONFIG_PATH" --uri grpc://0.0.0.0:50051
