#!/bin/sh

DEFAULT_CONFIG="/etc/flagd/demo_flags.json"
CUSTOM_CONFIG="${FLAGD_CUSTOM_CONFIG_PATH:-/var/lib/flagd/config/flags.json}"

if [ -f "$CUSTOM_CONFIG" ] && [ -r "$CUSTOM_CONFIG" ]; then
    echo "INFO: Using custom flag configuration from $CUSTOM_CONFIG"
    CONFIG_PATH="$CUSTOM_CONFIG"
else
    if [ -f "$CUSTOM_CONFIG" ]; then
        echo "WARNING: Custom flag file $CUSTOM_CONFIG exists but is not readable. Falling back to default config at $DEFAULT_CONFIG"
    else
        echo "INFO: No custom flag configuration found at $CUSTOM_CONFIG. Using default config at $DEFAULT_CONFIG"
    fi
    CONFIG_PATH="$DEFAULT_CONFIG"
fi

exec flagd start --config "$CONFIG_PATH"
