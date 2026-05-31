#!/bin/bash
set -euo pipefail

# Validate LOCUST_WEB_PORT only when not running in headless mode
if [ "${LOCUST_HEADLESS,,}" != "true" ]; then
    # Check if LOCUST_WEB_PORT is set
    if [ -z "${LOCUST_WEB_PORT:-}" ]; then
        echo "ERROR: LOCUST_WEB_PORT environment variable is required when LOCUST_HEADLESS is false" >&2
        exit 1
    fi
    
    # Check if it's a valid integer
    if ! [[ "${LOCUST_WEB_PORT}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: LOCUST_WEB_PORT must be a valid positive integer, got '${LOCUST_WEB_PORT}'" >&2
        exit 1
    fi
    
    # Check port range
    if [ "${LOCUST_WEB_PORT}" -lt 1024 ] || [ "${LOCUST_WEB_PORT}" -gt 65535 ]; then
        echo "ERROR: LOCUST_WEB_PORT must be between 1024 and 65535, got ${LOCUST_WEB_PORT}" >&2
        exit 1
    fi
fi

exec locust --skip-log-setup "$@"
