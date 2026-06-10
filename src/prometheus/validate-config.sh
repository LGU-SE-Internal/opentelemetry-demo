#!/bin/sh

# Default values
DEFAULT_RETENTION_TIME="15d"
DEFAULT_RETENTION_SIZE="0"
DEFAULT_SCRAPE_INTERVAL="15s"

# Use provided values or defaults
RETENTION_TIME=${PROMETHEUS_RETENTION_TIME:-$DEFAULT_RETENTION_TIME}
RETENTION_SIZE=${PROMETHEUS_RETENTION_SIZE:-$DEFAULT_RETENTION_SIZE}
SCRAPE_INTERVAL=${PROMETHEUS_SCRAPE_INTERVAL:-$DEFAULT_SCRAPE_INTERVAL}

# Validate retention time
echo "Validating PROMETHEUS_RETENTION_TIME: $RETENTION_TIME"
if ! promtool tsdb validate-duration "$RETENTION_TIME" 2>/dev/null; then
  echo "Invalid PROMETHEUS_RETENTION_TIME value: $RETENTION_TIME. Must be valid Prometheus duration (e.g. 15d, 24h)"
  exit 1
fi

# Validate retention size if non-zero
if [ "$RETENTION_SIZE" != "0" ]; then
  echo "Validating PROMETHEUS_RETENTION_SIZE: $RETENTION_SIZE"
  if ! promtool tsdb validate-size "$RETENTION_SIZE" 2>/dev/null; then
    echo "Invalid PROMETHEUS_RETENTION_SIZE value: $RETENTION_SIZE. Must be valid Prometheus size (e.g. 10GB, 512MB) or 0 to disable"
    exit 1
  fi
fi

# Validate scrape interval
echo "Validating PROMETHEUS_SCRAPE_INTERVAL: $SCRAPE_INTERVAL"
if ! promtool tsdb validate-duration "$SCRAPE_INTERVAL" 2>/dev/null; then
  echo "Invalid PROMETHEUS_SCRAPE_INTERVAL value: $SCRAPE_INTERVAL. Must be valid Prometheus duration (e.g. 15s, 1m)"
  exit 1
fi

# Validate Prometheus config syntax
echo "🔍 Checking Prometheus config syntax..."
promtool check config prometheus-config.yaml
if [ $? -ne 0 ]; then
  echo "❌ Prometheus config syntax is invalid."
  exit 1
fi
echo "✅ Prometheus config syntax is valid."

# Validate alert rules syntax
echo "🔍 Checking alert rules syntax..."
promtool check rules alerts.yaml
if [ $? -ne 0 ]; then
  echo "❌ Alert rules syntax is invalid."
  exit 1
fi
echo "✅ Alert rules syntax is valid."

echo "All configuration parameters are valid."
exit 0
