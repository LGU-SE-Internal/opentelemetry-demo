#!/bin/bash
set -eo pipefail

# Default configuration
OPENSEARCH_HOST=${OPENSEARCH_HOST:-localhost}
OPENSEARCH_PORT=${OPENSEARCH_PORT:-9200}
OPENSEARCH_TLS_ENABLED=${OPENSEARCH_TLS_ENABLED:-false}
OPENSEARCH_CA_CERT_PATH=${OPENSEARCH_CA_CERT_PATH:-""}
OPENSEARCH_CLIENT_CERT_PATH=${OPENSEARCH_CLIENT_CERT_PATH:-""}
OPENSEARCH_CLIENT_KEY_PATH=${OPENSEARCH_CLIENT_KEY_PATH:-""}
PROBE_TIMEOUT_SECONDS=${PROBE_TIMEOUT_SECONDS:-5}
OPENSEARCH_USERNAME=${OPENSEARCH_USERNAME:-""}
OPENSEARCH_PASSWORD=${OPENSEARCH_PASSWORD:-""}

# Check if opensearch process is running
if ! pidof java | xargs ps -fp 2>/dev/null | grep -q opensearch; then
  echo "ERROR: Opensearch java process is not running" >&2
  exit 1
fi

# Build base URL
PROTOCOL="http"
if [ "$OPENSEARCH_TLS_ENABLED" = "true" ]; then
  PROTOCOL="https"
fi
BASE_URL="${PROTOCOL}://${OPENSEARCH_HOST}:${OPENSEARCH_PORT}"

# Build curl command
CURL_CMD="curl -s -w \"%{http_code}\" --max-time ${PROBE_TIMEOUT_SECONDS}"

# Add TLS options
if [ "$OPENSEARCH_TLS_ENABLED" = "true" ]; then
  if [ -n "$OPENSEARCH_CA_CERT_PATH" ]; then
    CURL_CMD="$CURL_CMD --cacert ${OPENSEARCH_CA_CERT_PATH}"
  fi
  if [ -n "$OPENSEARCH_CLIENT_CERT_PATH" ] && [ -n "$OPENSEARCH_CLIENT_KEY_PATH" ]; then
    CURL_CMD="$CURL_CMD --cert ${OPENSEARCH_CLIENT_CERT_PATH} --key ${OPENSEARCH_CLIENT_KEY_PATH}"
  fi
fi

# Add auth options
if [ -n "$OPENSEARCH_USERNAME" ] && [ -n "$OPENSEARCH_PASSWORD" ]; then
  CURL_CMD="$CURL_CMD -u ${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}"
fi

# Execute API request
RESPONSE=$(eval $CURL_CMD $BASE_URL/)
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" != "200" ]; then
  echo "ERROR: Opensearch API request to / returned HTTP status $HTTP_CODE" >&2
  exit 1
fi

TAGLINE=$(echo "$RESPONSE_BODY" | jq -r '.tagline' 2>/dev/null || echo "")
if [ "$TAGLINE" != "You Know, for Search" ]; then
  echo "ERROR: Opensearch API response does not contain expected tagline" >&2
  exit 1
fi

exit 0
