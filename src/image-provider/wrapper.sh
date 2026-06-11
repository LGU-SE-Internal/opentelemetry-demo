#!/bin/sh
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

set -e

# Function to throw TLS configuration error
tls_config_error() {
    echo "TLSConfigurationError: $1" >&2
    exit 1
}

# Map spec environment variables to nginx template variables
CERT_PATH="${IMAGE_PROVIDER_TLS_CERT_PATH:-}"
KEY_PATH="${IMAGE_PROVIDER_TLS_KEY_PATH:-}"
CA_CERT_PATH="${IMAGE_PROVIDER_TLS_CA_CERT_PATH:-}"

# Validate TLS configuration
if [ -n "$CERT_PATH" ] || [ -n "$KEY_PATH" ]; then
    # Check both cert and key are provided
    if [ -z "$CERT_PATH" ] || [ -z "$KEY_PATH" ]; then
        tls_config_error "Missing required TLS parameter: both IMAGE_PROVIDER_TLS_CERT_PATH and IMAGE_PROVIDER_TLS_KEY_PATH must be provided when enabling TLS"
    fi

    # Check cert file exists and is readable
    if [ ! -f "$CERT_PATH" ] || [ ! -r "$CERT_PATH" ]; then
        tls_config_error "Certificate file not found or unreadable: $CERT_PATH"
    fi

    # Check key file exists and is readable
    if [ ! -f "$KEY_PATH" ] || [ ! -r "$KEY_PATH" ]; then
        tls_config_error "Private key file not found or unreadable: $KEY_PATH"
    fi

    # Validate cert and key match
    CERT_MODULUS=$(openssl x509 -noout -modulus -in "$CERT_PATH" 2>/dev/null || tls_config_error "Invalid TLS certificate format")
    KEY_MODULUS=$(openssl rsa -noout -modulus -in "$KEY_PATH" 2>/dev/null || tls_config_error "Invalid TLS private key format")
    
    if [ "$CERT_MODULUS" != "$KEY_MODULUS" ]; then
        tls_config_error "TLS certificate and private key are mismatched"
    fi

    # Check if certificate is expired
    if ! openssl x509 -checkend 0 -noout -in "$CERT_PATH" >/dev/null 2>&1; then
        tls_config_error "TLS certificate is expired"
    fi

    # Validate CA cert if provided
    if [ -n "$CA_CERT_PATH" ]; then
        if [ ! -f "$CA_CERT_PATH" ] || [ ! -r "$CA_CERT_PATH" ]; then
            tls_config_error "CA certificate file not found or unreadable: $CA_CERT_PATH"
        fi

        # Check CA cert is valid
        if ! openssl x509 -in "$CA_CERT_PATH" -noout >/dev/null 2>&1; then
            tls_config_error "Invalid or corrupted CA certificate file: $CA_CERT_PATH"
        fi
    fi
fi

# Generate nginx config from template
export ENABLE_TLS="false"
export SSL_CERTIFICATE=""
export SSL_CERTIFICATE_KEY=""
export SSL_CLIENT_CERTIFICATE=""
export SSL_VERIFY_CLIENT="off"
export SSL_PROTOCOLS=""

if [ -n "$CERT_PATH" ]; then
    export ENABLE_TLS="true"
    export SSL_CERTIFICATE="$CERT_PATH"
    export SSL_CERTIFICATE_KEY="$KEY_PATH"
    export SSL_PROTOCOLS="TLSv1.2 TLSv1.3;"

    if [ -n "$CA_CERT_PATH" ]; then
        export SSL_CLIENT_CERTIFICATE="$CA_CERT_PATH"
        export SSL_VERIFY_CLIENT="on;"
    fi
fi

# Set initial configuration
NGINX_TEMPLATE_PATH="/nginx.conf.template"
if [ -f "./nginx.conf.template" ]; then
    NGINX_TEMPLATE_PATH="./nginx.conf.template"
fi

envsubst '$OTEL_COLLECTOR_HOST $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME $NGINX_RATE_LIMIT_RPS $NGINX_PER_IMAGE_RATE_LIMIT_RPS $ENABLE_TLS $SSL_CERTIFICATE $SSL_CERTIFICATE_KEY $SSL_CLIENT_CERTIFICATE $SSL_VERIFY_CLIENT $SSL_PROTOCOLS' < "$NGINX_TEMPLATE_PATH" > /tmp/nginx.conf

# Test nginx configuration is valid
if ! nginx -t -c /tmp/nginx.conf > /dev/null 2>&1; then
    tls_config_error "Invalid nginx configuration generated from TLS settings"
fi

# Function to handle shutdown signals
handle_shutdown() {
    echo "Received shutdown signal, entering 30s grace period..."
    # Add shutdown_time variable to nginx config to trigger shutdown state
    sed -i '1i set $shutdown_time "'"$(date +%s)"'";' /tmp/nginx.conf
    # Reload nginx to apply the shutdown state
    nginx -s reload -c /tmp/nginx.conf
    # Wait for worker_shutdown_timeout (30s) before exiting
    sleep 30
    # Stop nginx gracefully
    nginx -s stop -c /tmp/nginx.conf
    exit 0
}

# Trap SIGTERM and SIGINT signals
trap handle_shutdown SIGTERM SIGINT

# Start nginx in foreground
nginx -g "daemon off;" -c /tmp/nginx.conf &
NGINX_PID=$!

# Wait for nginx process
wait $NGINX_PID
