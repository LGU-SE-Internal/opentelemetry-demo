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

# Validate port number environment variables if set
validate_port() {
    local var_name="$1"
    local port_value="${!var_name}"
    
    # Check if port is a positive integer
    if ! [[ "$port_value" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Invalid $var_name value: '$port_value'" >&2
        echo "Expected a valid integer TCP port number between 1 and 65535 inclusive." >&2
        exit 1
    fi
    
    # Check port range validity
    if [ "$port_value" -lt 1 ] || [ "$port_value" -gt 65535 ]; then
        echo "ERROR: Invalid $var_name value: '$port_value'" >&2
        echo "TCP port number must be between 1 and 65535 inclusive." >&2
        exit 1
    fi
}

if [[ -n "${LOCUST_WEB_PORT:-}" ]]; then
    validate_port "LOCUST_WEB_PORT"
fi

if [[ -n "${FLAGD_PORT:-}" ]]; then
    validate_port "FLAGD_PORT"
fi

if [[ -n "${FLAGD_OFREP_PORT:-}" ]]; then
    validate_port "FLAGD_OFREP_PORT"
fi

# Execute the original locust command with all passed arguments
exec locust --skip-log-setup "$@"
