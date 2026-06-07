#!/bin/bash

set -euo pipefail

# Default retention period if not set
DEFAULT_RETENTION_DAYS=7
RETENTION_DAYS=${OPENSEARCH_LOG_RETENTION_DAYS:-$DEFAULT_RETENTION_DAYS}

# Validate retention period is a positive integer >= 1
if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || [ "$RETENTION_DAYS" -lt 1 ]; then
    echo "ERROR: OPENSEARCH_LOG_RETENTION_DAYS must be a positive integer >= 1, got '$RETENTION_DAYS'"
    exit 1
fi

echo "Starting OpenSearch with log retention period of $RETENTION_DAYS days"

# Start OpenSearch in the background
/usr/share/opensearch/opensearch-docker-entrypoint.sh "$@" &
OPENSEARCH_PID=$!

# Wait for OpenSearch to become available
echo "Waiting for OpenSearch to start..."
until curl -s http://localhost:9200/_cluster/health > /dev/null; do
    sleep 2
done
echo "OpenSearch is available"

# Create or update ILM policy
echo "Creating/updating ILM policy 'otel-logs-retention-policy' with $RETENTION_DAYS days retention"
curl -s -X PUT "http://localhost:9200/_ilm/policy/otel-logs-retention-policy" \
    -H "Content-Type: application/json" \
    -d @- <<JSON
{
    "policy": {
        "phases": {
            "hot": {
                "actions": {}
            },
            "delete": {
                "min_age": "${RETENTION_DAYS}d",
                "actions": {
                    "delete": {}
                }
            }
        }
    }
}
JSON

echo ""

# Create or update index template to associate policy with log indices
echo "Creating/updating index template for log indices"
curl -s -X PUT "http://localhost:9200/_index_template/otel-logs-retention-template" \
    -H "Content-Type: application/json" \
    -d @- <<JSON
{
    "index_patterns": ["logs-*", "otel-*-logs-*", "otel-logs-*"],
    "template": {
        "settings": {
            "index.lifecycle.name": "otel-logs-retention-policy"
        }
    },
    "priority": 100,
    "_meta": {
        "description": "Template to apply OTel logs retention policy to log indices"
    }
}
JSON

echo ""

# Apply policy to all existing matching indices that don't already have it
echo "Applying policy to existing log indices"
indices=$(curl -s "http://localhost:9200/_cat/indices/logs-*,otel-*-logs-*,otel-logs-*?h=index" || true)
for index in $indices; do
    if [ -n "$index" ]; then
        current_policy=$(curl -s "http://localhost:9200/$index/_settings/index.lifecycle.name" | jq -r '.[].settings.index.lifecycle.name // empty')
        if [ -z "$current_policy" ]; then
            echo "Applying policy to existing index $index"
            curl -s -X PUT "http://localhost:9200/$index/_settings" \
                -H "Content-Type: application/json" \
                -d '{"index.lifecycle.name": "otel-logs-retention-policy"}' > /dev/null
        fi
    fi
done

echo "ILM configuration completed successfully"

# Wait for OpenSearch process to exit
wait $OPENSEARCH_PID
