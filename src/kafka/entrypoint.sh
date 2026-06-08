#!/bin/bash
set -euo pipefail

# Default values
KAFKA_TLS_ENABLED=${KAFKA_TLS_ENABLED:-false}
KAFKA_MTLS_ENABLED=${KAFKA_MTLS_ENABLED:-false}
KAFKA_KEYSTORE_PATH=${KAFKA_KEYSTORE_PATH:-/etc/kafka/secrets/kafka.keystore.jks}
KAFKA_KEYSTORE_PASSWORD=${KAFKA_KEYSTORE_PASSWORD:-}
KAFKA_TRUSTSTORE_PATH=${KAFKA_TRUSTSTORE_PATH:-/etc/kafka/secrets/kafka.truststore.jks}
KAFKA_TRUSTSTORE_PASSWORD=${KAFKA_TRUSTSTORE_PASSWORD:-}

# Validate TLS configuration if enabled
if [ "$KAFKA_TLS_ENABLED" = "true" ]; then
    if [ -z "$KAFKA_KEYSTORE_PASSWORD" ]; then
        echo "ERROR: missing keystore configuration: KAFKA_KEYSTORE_PASSWORD is required when KAFKA_TLS_ENABLED=true"
        exit 1
    fi
    if [ ! -f "$KAFKA_KEYSTORE_PATH" ]; then
        echo "ERROR: missing keystore configuration: KAFKA_KEYSTORE_PATH $KAFKA_KEYSTORE_PATH does not exist"
        exit 1
    fi

    # Configure TLS settings
    export KAFKA_SSL_KEYSTORE_LOCATION="$KAFKA_KEYSTORE_PATH"
    export KAFKA_SSL_KEYSTORE_PASSWORD="$KAFKA_KEYSTORE_PASSWORD"
    export KAFKA_SSL_KEY_PASSWORD="$KAFKA_KEYSTORE_PASSWORD"
    export KAFKA_SSL_ENABLED_PROTOCOLS="TLSv1.2,TLSv1.3"
    export KAFKA_SSL_PROTOCOL="TLSv1.3"

    # Update listener security protocol map
    if [ -z "${KAFKA_LISTENER_SECURITY_PROTOCOL_MAP:-}" ]; then
        export KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,SSL:SSL"
    else
        export KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="${KAFKA_LISTENER_SECURITY_PROTOCOL_MAP},SSL:SSL"
    fi

    # Configure listeners and advertised listeners
    if [ "$KAFKA_MTLS_ENABLED" = "true" ]; then
        # Validate mTLS configuration
        if [ -z "$KAFKA_TRUSTSTORE_PASSWORD" ]; then
            echo "ERROR: missing truststore configuration: KAFKA_TRUSTSTORE_PASSWORD is required when KAFKA_MTLS_ENABLED=true"
            exit 1
        fi
        if [ ! -f "$KAFKA_TRUSTSTORE_PATH" ]; then
            echo "ERROR: missing truststore configuration: KAFKA_TRUSTSTORE_PATH $KAFKA_TRUSTSTORE_PATH does not exist"
            exit 1
        fi

        # Configure mTLS settings
        export KAFKA_SSL_TRUSTSTORE_LOCATION="$KAFKA_TRUSTSTORE_PATH"
        export KAFKA_SSL_TRUSTSTORE_PASSWORD="$KAFKA_TRUSTSTORE_PASSWORD"
        export KAFKA_SSL_CLIENT_AUTH="required"

        # Only expose SSL listener
        export KAFKA_LISTENERS="SSL://0.0.0.0:9093,CONTROLLER://0.0.0.0:9094"
        export KAFKA_ADVERTISED_LISTENERS="SSL://${KAFKA_HOST}:9093"
        export KAFKA_CONTROLLER_QUORUM_VOTERS="1@${KAFKA_HOST}:9094"
    else
        # No mTLS, client auth optional
        export KAFKA_SSL_CLIENT_AUTH="none"

        # Only expose SSL listener
        export KAFKA_LISTENERS="SSL://0.0.0.0:9093,CONTROLLER://0.0.0.0:9094"
        export KAFKA_ADVERTISED_LISTENERS="SSL://${KAFKA_HOST}:9093"
        export KAFKA_CONTROLLER_QUORUM_VOTERS="1@${KAFKA_HOST}:9094"
    fi
else
    # Default plaintext mode
    export KAFKA_LISTENERS="PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093"
    export KAFKA_ADVERTISED_LISTENERS="PLAINTEXT://${KAFKA_HOST}:9092"
    export KAFKA_CONTROLLER_QUORUM_VOTERS="1@${KAFKA_HOST}:9093"
fi

# Execute the original Kafka entrypoint
exec /etc/kafka/docker/run.sh "$@"
