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

# Validate LOCUST_HEADLESS environment variable if set
if [[ -n "${LOCUST_HEADLESS:-}" ]]; then
    shopt -s nocasematch
    if ! [[ "$LOCUST_HEADLESS" =~ ^(true|false)$ ]]; then
        echo "ERROR: Invalid LOCUST_HEADLESS value: '$LOCUST_HEADLESS'" >&2
        echo "Expected value is either 'true' or 'false' (case-insensitive)" >&2
        exit 1
    fi
    shopt -u nocasematch
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
