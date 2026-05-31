#!/bin/bash
set -euo pipefail
# Enable verbose mode if VERBOSE environment variable is set to 1/true/yes
if [[ "${VERBOSE:-}" =~ ^(1|true|yes)$ ]]; then
    set -x
    echo "DEBUG: VERBOSE mode enabled in entrypoint.sh" >&2
    echo "DEBUG: LOCUST_RUN_TIME: ${LOCUST_RUN_TIME:-<unset>}" >&2
    echo "DEBUG: Command arguments: $*" >&2
fi
# Validate LOCUST_RUN_TIME environment variable if set
if [[ -n "${LOCUST_RUN_TIME:-}" ]]; then
    # Valid format: sequence of numbers followed by s/m/h units, e.g. 300s, 20m, 1h, 1h30m
    if ! [[ "$LOCUST_RUN_TIME" =~ ^([0-9]+[hms])+$ ]]; then
        echo "ERROR: Invalid LOCUST_RUN_TIME value: '$LOCUST_RUN_TIME'" >&2
        echo "Expected duration format using units s (seconds), m (minutes), h (hours). Examples: 300s, 20m, 1h, 1h30m" >&2
        exit 1
    fi
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
