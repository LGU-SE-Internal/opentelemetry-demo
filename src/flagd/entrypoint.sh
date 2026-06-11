#!/bin/sh

DEFAULT_CONFIG_PATH="/etc/flagd/demo_flags.json"
CUSTOM_CONFIG_PATH="${FLAGD_CUSTOM_CONFIG_PATH:-/var/lib/flagd/config/flags.json}"

# Check if custom config exists and is readable
if [ -f "$CUSTOM_CONFIG_PATH" ] && [ -r "$CUSTOM_CONFIG_PATH" ]; then
    echo "Using custom flag configuration from $CUSTOM_CONFIG_PATH"
    CONFIG_PATH="$CUSTOM_CONFIG_PATH"
elif [ -f "$CUSTOM_CONFIG_PATH" ]; then
    echo "WARNING: Custom flag configuration file $CUSTOM_CONFIG_PATH exists but is not readable. Falling back to default config."
    CONFIG_PATH="$DEFAULT_CONFIG_PATH"
else
    echo "No custom flag configuration found at $CUSTOM_CONFIG_PATH. Using default config from $DEFAULT_CONFIG_PATH"
    CONFIG_PATH="$DEFAULT_CONFIG_PATH"
fi

# Start flagd with the selected config
exec flagd start --config "$CONFIG_PATH" "$@"
