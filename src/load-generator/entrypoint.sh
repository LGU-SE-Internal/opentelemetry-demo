#!/bin/bash
set -euo pipefail

# Check for help flag
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    echo "OpenTelemetry Demo Load Generator Entrypoint"
    echo "Usage: $0 [LOCUST_ARGUMENTS...]"
    echo
    echo "Supported Environment Variables:"
    echo "  LOCUST_WEB_PORT                  Port to expose the Locust web UI on (default: 8089)"
    echo "  LOCUST_USERS                     Peak number of concurrent Locust users (default: 10)"
    echo "  LOCUST_RUN_TIME                  Duration to run the load test for (format: 300s, 20m, 1h, 1h30m)"
    echo "  LOCUST_SPAWN_RATE                Rate to spawn new users at (users per second, default: 1)"
    echo "  LOCUST_HOST                      Host URL of the frontend service to test (default: http://frontend:8080)"
    echo "  LOCUST_HEADLESS                  Run Locust in headless mode without web UI (set to 'true' to enable, default: false)"
    echo "  LOCUST_AUTOSTART                 Automatically start load test on start (set to 'true' to enable, default: false)"
    echo "  LOCUST_BROWSER_TRAFFIC_ENABLED   Enable browser-based traffic generation using Playwright (default: true)"
    echo "  LOCUST_WEB_HOST                  Host address to bind the Locust web UI to (default: 0.0.0.0)"
    echo "  FLAGD_HOST                       Hostname of the FlagD feature flag service (default: localhost)"
    echo "  FLAGD_PORT                       Port of the FlagD service (default: 8013)"
    echo "  FLAGD_OFREP_PORT                 Port of the FlagD OFREP API (default: 8016)"
    echo "  OTEL_EXPORTER_OTLP_ENDPOINT      OTLP endpoint for sending telemetry data (default: http://otelcol:4317)"
    echo "  OTEL_SERVICE_NAME                Service name for OpenTelemetry telemetry (default: load-generator)"
    echo
    echo "Example Commands:"
    echo
    echo "1. Run with web UI (default mode):"
    echo "   LOCUST_HOST=http://localhost:8080 $0"
    echo "   Access UI at http://localhost:8089"
    echo
    echo "2. Run headless mode with 50 users for 10 minutes:"
    echo "   LOCUST_HOST=http://frontend:8080 LOCUST_HEADLESS=true LOCUST_AUTOSTART=true \\"
    echo "   LOCUST_USERS=50 LOCUST_RUN_TIME=10m $0"
    echo
    echo "3. Run with custom runtime configuration, disable browser traffic:"
    echo "   LOCUST_HOST=http://localhost:8080 LOCUST_BROWSER_TRAFFIC_ENABLED=false \\"
    echo "   LOCUST_SPAWN_RATE=2 LOCUST_USERS=20 $0"
    echo
    echo "All additional arguments are passed directly to Locust."
    exit 0
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
