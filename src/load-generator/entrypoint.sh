#!/bin/bash

# Validate LOCUST_HOST when running in headless mode
if [ "${LOCUST_HEADLESS,,}" = "true" ]; then
  if [ -z "${LOCUST_HOST}" ]; then
    echo "ERROR: LOCUST_HOST environment variable is required when running locust in headless mode (LOCUST_HEADLESS=true)" >&2
    exit 1
  fi
fi

# Execute locust with all provided arguments
exec locust --skip-log-setup "$@"
