#!/bin/bash
# Test only the validation part of entrypoint.sh
set -euo pipefail

# Validate LOCUST_HEADLESS if it is set
if [[ -n "${LOCUST_HEADLESS:-}" ]]; then
    # Convert to lowercase for case-insensitive matching
    lowercase_headless=$(echo "$LOCUST_HEADLESS" | tr '[:upper:]' '[:lower:]')
    case "$lowercase_headless" in
        true|false|1|0)
            # Valid value
            exit 0
            ;;
        *)
            echo "ERROR: Invalid value for LOCUST_HEADLESS: '$LOCUST_HEADLESS'" >&2
            echo "Allowed values are: true, false, 1, 0 (case insensitive)" >&2
            exit 1
            ;;
    esac
fi
exit 0
