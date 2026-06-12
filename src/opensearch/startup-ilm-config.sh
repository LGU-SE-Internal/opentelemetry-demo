#!/bin/bash

set -euo pipefail

# Signal handler for graceful shutdown
handle_signal() {
    local signal=$1
    echo "Received $signal, forwarding to OpenSearch process (PID $OPENSEARCH_PID)"
    kill -"$signal" "$OPENSEARCH_PID"
    wait "$OPENSEARCH_PID"
    exit $?
}

# Register signal handlers
trap 'handle_signal SIGTERM' SIGTERM
trap 'handle_signal SIGINT' SIGINT
trap 'handle_signal SIGQUIT' SIGQUIT

# Default retention period if not set
DEFAULT_RETENTION_DAYS=7
RETENTION_DAYS=${OPENSEARCH_LOG_RETENTION_DAYS:-$DEFAULT_RETENTION_DAYS}

# TLS Configuration
OPENSEARCH_TLS_ENABLED=${OPENSEARCH_TLS_ENABLED:-false}
OPENSEARCH_TLS_SKIP_VERIFY=${OPENSEARCH_TLS_SKIP_VERIFY:-false}
OPENSEARCH_TLS_CA_CERT=${OPENSEARCH_TLS_CA_CERT:-}
OPENSEARCH_TLS_CLIENT_CERT=${OPENSEARCH_TLS_CLIENT_CERT:-}
OPENSEARCH_TLS_CLIENT_KEY=${OPENSEARCH_TLS_CLIENT_KEY:-}

# Error codes
E100=100 # TLS enabled but no CA and no skip verify
E101=101 # CA cert path invalid
E102=102 # Client cert provided without key
E103=103 # Client cert/key path invalid
E104=104 # TLS connection failure

# Validate retention period is a positive integer >= 1
if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || [ "$RETENTION_DAYS" -lt 1 ]; then
    echo "ERROR: OPENSEARCH_LOG_RETENTION_DAYS must be a positive integer >= 1, got '$RETENTION_DAYS'"
    exit 1
fi

# Validate TLS configuration
if [ "$OPENSEARCH_TLS_ENABLED" = "true" ]; then
    # Check E100 condition
    if [ -z "$OPENSEARCH_TLS_CA_CERT" ] && [ "$OPENSEARCH_TLS_SKIP_VERIFY" != "true" ]; then
        echo "ERROR E${E100}: OPENSEARCH_TLS_ENABLED=true but neither OPENSEARCH_TLS_CA_CERT is provided nor OPENSEARCH_TLS_SKIP_VERIFY=true"
        exit $E100
    fi

    # Validate CA cert if provided
    if [ -n "$OPENSEARCH_TLS_CA_CERT" ]; then
        if [ ! -f "$OPENSEARCH_TLS_CA_CERT" ] || [ ! -r "$OPENSEARCH_TLS_CA_CERT" ]; then
            echo "ERROR E${E101}: OPENSEARCH_TLS_CA_CERT file '$OPENSEARCH_TLS_CA_CERT' does not exist or is not readable"
            exit $E101
        fi
    fi

    # Validate client cert/key
    if [ -n "$OPENSEARCH_TLS_CLIENT_CERT" ]; then
        if [ -z "$OPENSEARCH_TLS_CLIENT_KEY" ]; then
            echo "ERROR E${E102}: OPENSEARCH_TLS_CLIENT_CERT provided but OPENSEARCH_TLS_CLIENT_KEY is not provided"
            exit $E102
        fi

        # Check client cert file
        if [ ! -f "$OPENSEARCH_TLS_CLIENT_CERT" ] || [ ! -r "$OPENSEARCH_TLS_CLIENT_CERT" ]; then
            echo "ERROR E${E103}: OPENSEARCH_TLS_CLIENT_CERT file '$OPENSEARCH_TLS_CLIENT_CERT' does not exist or is not readable"
            exit $E103
        fi

        # Check client key file
        if [ ! -f "$OPENSEARCH_TLS_CLIENT_KEY" ] || [ ! -r "$OPENSEARCH_TLS_CLIENT_KEY" ]; then
            echo "ERROR E${E103}: OPENSEARCH_TLS_CLIENT_KEY file '$OPENSEARCH_TLS_CLIENT_KEY' does not exist or is not readable"
            exit $E103
        fi
    elif [ -n "$OPENSEARCH_TLS_CLIENT_KEY" ]; then
        # Client key provided without cert
        echo "ERROR E${E102}: OPENSEARCH_TLS_CLIENT_KEY provided but OPENSEARCH_TLS_CLIENT_CERT is not provided"
        exit $E102
    fi
fi

# Build base URL and curl options
OPENSEARCH_PROTOCOL="http"
CURL_OPTS=()
if [ "$OPENSEARCH_TLS_ENABLED" = "true" ]; then
    OPENSEARCH_PROTOCOL="https"
    if [ -n "$OPENSEARCH_TLS_CA_CERT" ]; then
        CURL_OPTS+=("--cacert" "$OPENSEARCH_TLS_CA_CERT")
    fi
    if [ "$OPENSEARCH_TLS_SKIP_VERIFY" = "true" ]; then
        CURL_OPTS+=("--insecure")
    fi
    if [ -n "$OPENSEARCH_TLS_CLIENT_CERT" ] && [ -n "$OPENSEARCH_TLS_CLIENT_KEY" ]; then
        CURL_OPTS+=("--cert" "$OPENSEARCH_TLS_CLIENT_CERT" "--key" "$OPENSEARCH_TLS_CLIENT_KEY")
    fi
fi
OPENSEARCH_BASE_URL="${OPENSEARCH_PROTOCOL}://localhost:9200"

# Wrapper function for curl with TLS error handling
run_curl() {
    local output
    output=$(curl --fail-with-body -s "${CURL_OPTS[@]}" "$@" 2>&1)
    local curl_exit=$?
    if [ $curl_exit -ne 0 ]; then
        # Check if this is a TLS related error
        if echo "$output" | grep -qiE "(ssl|tls|certificate|handshake|secure connection)"; then
            echo "ERROR E${E104}: TLS connection failure: $output"
            exit $E104
        else
            echo "ERROR: Curl request failed: $output"
            exit $curl_exit
        fi
    fi
    echo "$output"
}

echo "Starting OpenSearch with log retention period of $RETENTION_DAYS days"

# Start OpenSearch in the background
/usr/share/opensearch/opensearch-docker-entrypoint.sh "$@" &
OPENSEARCH_PID=$!

# Wait for OpenSearch to become available
echo "Waiting for OpenSearch to start..."
until run_curl "${OPENSEARCH_BASE_URL}/_cluster/health" > /dev/null; do
    sleep 2
done
echo "OpenSearch is available"

# Create or update ILM policy
echo "Creating/updating ILM policy 'otel-logs-retention-policy' with $RETENTION_DAYS days retention"
run_curl -X PUT "${OPENSEARCH_BASE_URL}/_ilm/policy/otel-logs-retention-policy" \
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
run_curl -X PUT "${OPENSEARCH_BASE_URL}/_index_template/otel-logs-retention-template" \
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
indices=$(run_curl "${OPENSEARCH_BASE_URL}/_cat/indices/logs-*,otel-*-logs-*,otel-logs-*?h=index" || true)
for index in $indices; do
    if [ -n "$index" ]; then
        current_policy=$(run_curl "${OPENSEARCH_BASE_URL}/$index/_settings/index.lifecycle.name" | jq -r '.[].settings.index.lifecycle.name // empty')
        if [ -z "$current_policy" ]; then
            echo "Applying policy to existing index $index"
            run_curl -X PUT "${OPENSEARCH_BASE_URL}/$index/_settings" \
                -H "Content-Type: application/json" \
                -d '{"index.lifecycle.name": "otel-logs-retention-policy"}' > /dev/null
        fi
    fi
done

echo "ILM configuration completed successfully"

# Wait for OpenSearch process to exit
wait $OPENSEARCH_PID
