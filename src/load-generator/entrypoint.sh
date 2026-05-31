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

# Validate LOCUST_HOST environment variable if set
if [[ -n "${LOCUST_HOST:-}" ]]; then
    # Must start with http:// or https://
    if ! [[ "$LOCUST_HOST" =~ ^https?:// ]]; then
        echo "ERROR: Invalid LOCUST_HOST value: '$LOCUST_HOST'" >&2
        echo "Expected URL starting with http:// or https://" >&2
        exit 1
    fi
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
