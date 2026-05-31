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

# Validate LOCUST_WEB_PORT environment variable if set
if [[ -n "${LOCUST_WEB_PORT:-}" ]]; then
    # Valid format: integer between 1 and 65535
    if ! [[ "$LOCUST_WEB_PORT" =~ ^[0-9]+$ ]] || (( LOCUST_WEB_PORT < 1 || LOCUST_WEB_PORT > 65535 )); then
        echo "ERROR: Invalid LOCUST_WEB_PORT value: '$LOCUST_WEB_PORT'" >&2
        echo "Expected integer value between 1 and 65535 (valid TCP port number)." >&2
        exit 1
    fi
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
