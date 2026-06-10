#!/bin/bash

set -euo pipefail

# Default values for environment variables
KAFKA_TLS_ENABLED=${KAFKA_TLS_ENABLED:-false}
KAFKA_MTLS_ENABLED=${KAFKA_MTLS_ENABLED:-false}
KAFKA_TLS_KEYSTORE_PATH=${KAFKA_TLS_KEYSTORE_PATH:-}
KAFKA_TLS_KEYSTORE_PASSWORD=${KAFKA_TLS_KEYSTORE_PASSWORD:-}
KAFKA_TLS_TRUSTSTORE_PATH=${KAFKA_TLS_TRUSTSTORE_PATH:-}
KAFKA_TLS_TRUSTSTORE_PASSWORD=${KAFKA_TLS_TRUSTSTORE_PASSWORD:-}
KAFKA_TLS_CLIENT_AUTH=${KAFKA_TLS_CLIENT_AUTH:-required}

# Validate configuration
if [ "$KAFKA_MTLS_ENABLED" = "true" ] && [ "$KAFKA_TLS_ENABLED" != "true" ]; then
    echo "ERROR: KAFKA_MTLS_ENABLED requires KAFKA_TLS_ENABLED to be set to true" >&2
    exit 1
fi

if [ "$KAFKA_TLS_ENABLED" = "true" ]; then
    if [ -z "$KAFKA_TLS_KEYSTORE_PATH" ] || [ -z "$KAFKA_TLS_KEYSTORE_PASSWORD" ]; then
        echo "ERROR: KAFKA_TLS_KEYSTORE_PATH and KAFKA_TLS_KEYSTORE_PASSWORD are required when KAFKA_TLS_ENABLED is true" >&2
        exit 1
    fi

    if [ ! -f "$KAFKA_TLS_KEYSTORE_PATH" ]; then
        echo "ERROR: Keystore file not found at path: $KAFKA_TLS_KEYSTORE_PATH" >&2
        exit 1
    fi

    if [ "$KAFKA_MTLS_ENABLED" = "true" ]; then
        if [ -z "$KAFKA_TLS_TRUSTSTORE_PATH" ] || [ -z "$KAFKA_TLS_TRUSTSTORE_PASSWORD" ]; then
            echo "ERROR: KAFKA_TLS_TRUSTSTORE_PATH and KAFKA_TLS_TRUSTSTORE_PASSWORD are required when KAFKA_MTLS_ENABLED is true" >&2
            exit 1
        fi

        if [ ! -f "$KAFKA_TLS_TRUSTSTORE_PATH" ]; then
            echo "ERROR: Truststore file not found at path: $KAFKA_TLS_TRUSTSTORE_PATH" >&2
            exit 1
        fi
    fi
fi

# Base listener configuration
KAFKA_LISTENERS=${KAFKA_LISTENERS:-PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093}
KAFKA_ADVERTISED_LISTENERS=${KAFKA_ADVERTISED_LISTENERS:-PLAINTEXT://kafka:9092}
KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT"

# Add TLS listener configuration if enabled
if [ "$KAFKA_TLS_ENABLED" = "true" ]; then
    # Update listeners to include SSL listener on port 9093, move controller to 9094
    KAFKA_LISTENERS="PLAINTEXT://0.0.0.0:9092,SSL://0.0.0.0:9093,CONTROLLER://0.0.0.0:9094"
    # Update advertised listeners to include SSL listener
    KAFKA_ADVERTISED_LISTENERS="PLAINTEXT://kafka:9092,SSL://kafka:9093"
    # Update security protocol map
    KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,SSL:SSL"
    KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER

    # Add TLS configuration to server.properties
    echo "ssl.keystore.location=$KAFKA_TLS_KEYSTORE_PATH" >> /opt/kafka/config/server.properties
    echo "ssl.keystore.password=$KAFKA_TLS_KEYSTORE_PASSWORD" >> /opt/kafka/config/server.properties
    echo "ssl.key.password=$KAFKA_TLS_KEYSTORE_PASSWORD" >> /opt/kafka/config/server.properties
    echo "ssl.enabled.protocols=TLSv1.2,TLSv1.3" >> /opt/kafka/config/server.properties
    echo "ssl.client.auth=none" >> /opt/kafka/config/server.properties

    # Add mTLS configuration if enabled
    if [ "$KAFKA_MTLS_ENABLED" = "true" ]; then
        echo "ssl.truststore.location=$KAFKA_TLS_TRUSTSTORE_PATH" >> /opt/kafka/config/server.properties
        echo "ssl.truststore.password=$KAFKA_TLS_TRUSTSTORE_PASSWORD" >> /opt/kafka/config/server.properties
        echo "ssl.client.auth=$KAFKA_TLS_CLIENT_AUTH" >> /opt/kafka/config/server.properties
    fi
fi

# Export updated environment variables for Kafka
export KAFKA_LISTENERS
export KAFKA_ADVERTISED_LISTENERS
export KAFKA_LISTENER_SECURITY_PROTOCOL_MAP
export KAFKA_CONTROLLER_LISTENER_NAMES

# Execute the original Kafka entrypoint
exec /etc/kafka/docker/run "$@"
