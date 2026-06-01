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

# Validate LOCUST_EXIT_CODE_ON_FAILURE environment variable if set
if [[ -n "${LOCUST_EXIT_CODE_ON_FAILURE:-}" ]]; then
    # Convert to lowercase for case-insensitive check
    lower_val="${LOCUST_EXIT_CODE_ON_FAILURE,,}"
    if ! [[ "$lower_val" =~ ^(0|1|true|false)$ ]]; then
        echo "ERROR: Invalid LOCUST_EXIT_CODE_ON_FAILURE value: '$LOCUST_EXIT_CODE_ON_FAILURE'" >&2
        echo "Valid values are: 0, 1, true, false (case-insensitive)" >&2
        exit 1
    fi
fi
echo Validation passed
