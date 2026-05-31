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

# Validate LOCUST_SPAWN_RATE environment variable if set
if [[ -n "${LOCUST_SPAWN_RATE:-}" ]]; then
    # Valid format: positive integer or float, e.g. 1, 10, 0.5, 100.25
    if ! [[ "$LOCUST_SPAWN_RATE" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        echo "ERROR: Invalid LOCUST_SPAWN_RATE value: '$LOCUST_SPAWN_RATE'" >&2
        echo "Expected positive numeric value (integer or float). Examples: 1, 10, 0.5, 100.25" >&2
        exit 1
    fi
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
