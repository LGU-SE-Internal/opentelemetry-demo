#!/bin/sh

# Set initial configuration
envsubst '$OTEL_COLLECTOR_HOST $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME $NGINX_RATE_LIMIT_RPS $NGINX_PER_IMAGE_RATE_LIMIT_RPS $TLS_ENABLED $TLS_CERT_PATH $TLS_KEY_PATH $TLS_MIN_VERSION $TLS_CIPHER_SUITES $MTLS_ENABLED $MTLS_CA_CERT_PATH' < /nginx.conf.template > /etc/nginx/nginx.conf

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
