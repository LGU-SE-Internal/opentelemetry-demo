#!/bin/bash
set -euo pipefail
# Validate LOCUST_HOST environment variable if set
if [[ -n "${LOCUST_HOST:-}" ]]; then
    # Must start with http:// or https://
    if ! [[ "$LOCUST_HOST" =~ ^https?:// ]]; then
        echo "ERROR: Invalid LOCUST_HOST value: '$LOCUST_HOST'" >&2
        echo "Expected URL starting with http:// or https://" >&2
        exit 1
    fi
    echo "LOCUST_HOST is valid: $LOCUST_HOST"
fi
echo "Validation passed"

