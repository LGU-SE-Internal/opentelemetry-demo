#!/bin/bash
set -euo pipefail

# Validate LOCUST_RUN_TIME environment variable if set
if [[ -n "${LOCUST_RUN_TIME:-}" ]]; then
    # Valid format: sequence of numbers followed by s/m/h units, e.g. 300s, 20m, 1h, 1h30m
    if ! [[ "$LOCUST_RUN_TIME" =~ ^([0-9]+[hms])+$ ]]; then
        echo "ERROR: Invalid LOCUST_RUN_TIME value: '$LOCUST_RUN_TIME'" >&2
        echo "Expected duration format using units s (seconds), m (minutes), h (hours). Examples: 300s, 20m, 1h, 1h30m" >&2
        exit 1
    fi
fi

# Validate LOCUST_AUTOSTART environment variable if set
if [[ -n "${LOCUST_AUTOSTART:-}" ]]; then
    shopt -s nocasematch
    if ! [[ "$LOCUST_AUTOSTART" =~ ^(true|false)$ ]]; then
        echo "ERROR: Invalid LOCUST_AUTOSTART value: '$LOCUST_AUTOSTART'" >&2
        echo "Expected boolean value (case-insensitive): 'true' or 'false'" >&2
        exit 1
    fi
    shopt -u nocasematch
fi

# Validate LOCUST_BROWSER_TRAFFIC_ENABLED environment variable if set
if [[ -n "${LOCUST_BROWSER_TRAFFIC_ENABLED:-}" ]]; then
    shopt -s nocasematch
    if ! [[ "$LOCUST_BROWSER_TRAFFIC_ENABLED" =~ ^(true|false)$ ]]; then
        echo "ERROR: Invalid LOCUST_BROWSER_TRAFFIC_ENABLED value: '$LOCUST_BROWSER_TRAFFIC_ENABLED'" >&2
        echo "Expected boolean value (case-insensitive): 'true' or 'false'" >&2
        exit 1
    fi
    shopt -u nocasematch
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
