#!/bin/sh

# Custom error type for TLS configuration issues
tls_config_error() {
    echo "TLSConfigurationError: $1" >&2
    exit 1
}

# Map spec environment variables to nginx template variables
CERT_PATH="${IMAGE_PROVIDER_TLS_CERT_PATH}"
KEY_PATH="${IMAGE_PROVIDER_TLS_KEY_PATH}"
CA_CERT_PATH="${IMAGE_PROVIDER_TLS_CA_CERT_PATH}"

# Validate TLS configuration
if [ -n "$CERT_PATH" ] || [ -n "$KEY_PATH" ]; then
    # Check both cert and key are provided
    if [ -z "$CERT_PATH" ] || [ -z "$KEY_PATH" ]; then
        tls_config_error "Both IMAGE_PROVIDER_TLS_CERT_PATH and IMAGE_PROVIDER_TLS_KEY_PATH must be provided when TLS is enabled"
    fi

    # Check cert file exists and is readable
    if [ ! -f "$CERT_PATH" ] || [ ! -r "$CERT_PATH" ]; then
        tls_config_error "TLS certificate file $CERT_PATH is missing or unreadable"
    fi

    # Check key file exists and is readable
    if [ ! -f "$KEY_PATH" ] || [ ! -r "$KEY_PATH" ]; then
        tls_config_error "TLS private key file $KEY_PATH is missing or unreadable"
    fi

    # Validate cert and key match
    if ! openssl x509 -noout -modulus -in "$CERT_PATH" > /tmp/cert_modulus 2>&1; then
        tls_config_error "Invalid TLS certificate file: $CERT_PATH"
    fi
    if ! openssl rsa -noout -modulus -in "$KEY_PATH" > /tmp/key_modulus 2>&1; then
        tls_config_error "Invalid TLS private key file: $KEY_PATH"
    fi
    if ! diff /tmp/cert_modulus /tmp/key_modulus > /dev/null 2>&1; then
        tls_config_error "TLS certificate and private key are mismatched"
    fi
    rm -f /tmp/cert_modulus /tmp/key_modulus

    # Validate certificate is not expired
    if ! openssl x509 -checkend 0 -noout -in "$CERT_PATH" > /dev/null 2>&1; then
        tls_config_error "TLS certificate is expired"
    fi

    # Set nginx TLS variables
    export TLS_ENABLED="true"
    export TLS_CERT_PATH="$CERT_PATH"
    export TLS_KEY_PATH="$KEY_PATH"
    export TLS_MIN_VERSION="TLSv1.2"

    # Handle mTLS if CA cert is provided
    if [ -n "$CA_CERT_PATH" ]; then
        # Check CA cert exists and is readable
        if [ ! -f "$CA_CERT_PATH" ] || [ ! -r "$CA_CERT_PATH" ]; then
            tls_config_error "CA certificate file $CA_CERT_PATH is missing or unreadable"
        fi

        # Validate CA cert is valid
        if ! openssl x509 -noout -in "$CA_CERT_PATH" > /dev/null 2>&1; then
            tls_config_error "Invalid CA certificate file: $CA_CERT_PATH"
        fi

        export MTLS_ENABLED="true"
        export MTLS_CA_CERT_PATH="$CA_CERT_PATH"
    else
        export MTLS_ENABLED="false"
    fi
else
    # No TLS configured, use plaintext only
    export TLS_ENABLED="false"
    export MTLS_ENABLED="false"
fi

# Set initial configuration
envsubst '$OTEL_COLLECTOR_HOST $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME $NGINX_RATE_LIMIT_RPS $NGINX_PER_IMAGE_RATE_LIMIT_RPS $TLS_ENABLED $TLS_CERT_PATH $TLS_KEY_PATH $TLS_MIN_VERSION $TLS_CIPHER_SUITES $MTLS_ENABLED $MTLS_CA_CERT_PATH' < /nginx.conf.template > /etc/nginx/nginx.conf

# Test nginx configuration is valid
if ! nginx -t > /dev/null 2>&1; then
    tls_config_error "Invalid nginx configuration generated from TLS settings"
fi

# Function to handle shutdown signals
handle_shutdown() {
    echo "Received shutdown signal, entering 30s grace period..."
    # Add shutdown_time variable to nginx config to trigger shutdown state
    sed -i '1i set $shutdown_time "'"$(date +%s)"'";' /etc/nginx/nginx.conf
    # Reload nginx to apply the shutdown state
    nginx -s reload
    # Wait for worker_shutdown_timeout (30s) before exiting
    sleep 30
    # Stop nginx gracefully
    nginx -s stop
    exit 0
}

# Trap SIGTERM and SIGINT signals
trap handle_shutdown SIGTERM SIGINT

# Start nginx in foreground
nginx -g "daemon off;" &
NGINX_PID=$!

# Wait for nginx process
wait $NGINX_PID

