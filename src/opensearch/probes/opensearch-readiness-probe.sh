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
ALLOWED_CLUSTER_STATUS=${ALLOWED_CLUSTER_STATUS:-"green,yellow"}
EXPECTED_ILM_POLICIES=${EXPECTED_ILM_POLICIES:-""}

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

# Check cluster health endpoint
RESPONSE=$(eval $CURL_CMD $BASE_URL/_cluster/health)
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" != "200" ]; then
  echo "ERROR: Opensearch API request to /_cluster/health returned HTTP status $HTTP_CODE" >&2
  exit 1
fi

# Check cluster status
CLUSTER_STATUS=$(echo "$RESPONSE_BODY" | jq -r '.status' 2>/dev/null || echo "")
IFS=',' read -ra ALLOWED_STATUSES <<< "$ALLOWED_CLUSTER_STATUS"
STATUS_ALLOWED=0
for status in "${ALLOWED_STATUSES[@]}"; do
  if [ "$status" = "$CLUSTER_STATUS" ]; then
    STATUS_ALLOWED=1
    break
  fi
done
if [ $STATUS_ALLOWED -eq 0 ]; then
  echo "ERROR: Cluster status $CLUSTER_STATUS is not in allowed list: $ALLOWED_CLUSTER_STATUS" >&2
  exit 1
fi

# Check unassigned shards
UNASSIGNED_SHARDS=$(echo "$RESPONSE_BODY" | jq -r '.unassigned_shards' 2>/dev/null || echo "-1")
if [ "$UNASSIGNED_SHARDS" != "0" ]; then
  echo "ERROR: Cluster has $UNASSIGNED_SHARDS unassigned shards, expected 0" >&2
  exit 1
fi

# Check ILM policies if expected
if [ -n "$EXPECTED_ILM_POLICIES" ]; then
  RESPONSE=$(eval $CURL_CMD $BASE_URL/_ilm/policy)
  HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
  RESPONSE_BODY=$(echo "$RESPONSE" | head -n -1)

  if [ "$HTTP_CODE" != "200" ]; then
    echo "ERROR: Opensearch API request to /_ilm/policy returned HTTP status $HTTP_CODE" >&2
    exit 1
  fi

  IFS=',' read -ra EXPECTED_POLICIES <<< "$EXPECTED_ILM_POLICIES"
  for policy in "${EXPECTED_POLICIES[@]}"; do
    POLICY_EXISTS=$(echo "$RESPONSE_BODY" | jq -r "has(\"$policy\")" 2>/dev/null || echo "false")
    if [ "$POLICY_EXISTS" != "true" ]; then
      echo "ERROR: Expected ILM policy $policy not found in cluster" >&2
      exit 1
    fi
  done
fi

exit 0
