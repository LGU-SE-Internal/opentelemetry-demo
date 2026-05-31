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

# Validate LOCUST_LOG_LEVEL environment variable if set
if [[ -n "${LOCUST_LOG_LEVEL:-}" ]]; then
    # Convert to uppercase for case-insensitive comparison
    log_level_upper=$(echo "$LOCUST_LOG_LEVEL" | tr '[:lower:]' '[:upper:]')
    valid_levels=("DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL")
    if ! [[ " ${valid_levels[*]} " =~ " $log_level_upper " ]]; then
        echo "ERROR: Invalid LOCUST_LOG_LEVEL value: '$LOCUST_LOG_LEVEL'" >&2
        echo "Allowed values are: DEBUG, INFO, WARNING, ERROR, CRITICAL (case insensitive)" >&2
        exit 1
    fi
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
