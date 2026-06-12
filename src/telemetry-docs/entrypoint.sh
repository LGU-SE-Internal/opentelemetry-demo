#!/bin/sh
set -e

# Set default graceful shutdown timeout
export NGINX_GRACEFUL_SHUTDOWN_TIMEOUT=${NGINX_GRACEFUL_SHUTDOWN_TIMEOUT:-30}

# Set default TLS/mTLS environment variables
export TELEMETRY_DOCS_TLS_ENABLED=${TELEMETRY_DOCS_TLS_ENABLED:-false}
export TELEMETRY_DOCS_TLS_CERT_PATH=${TELEMETRY_DOCS_TLS_CERT_PATH:-""}
export TELEMETRY_DOCS_TLS_KEY_PATH=${TELEMETRY_DOCS_TLS_KEY_PATH:-""}
export TELEMETRY_DOCS_MTLS_ENABLED=${TELEMETRY_DOCS_MTLS_ENABLED:-false}
export TELEMETRY_DOCS_MTLS_CA_CERT_PATH=${TELEMETRY_DOCS_MTLS_CA_CERT_PATH:-""}

# Validate TLS/mTLS configuration
if [ "$TELEMETRY_DOCS_TLS_ENABLED" = "true" ]; then
    if [ -z "$TELEMETRY_DOCS_TLS_CERT_PATH" ] || [ ! -f "$TELEMETRY_DOCS_TLS_CERT_PATH" ] || [ ! -r "$TELEMETRY_DOCS_TLS_CERT_PATH" ]; then
        echo "ERROR: TELEMETRY_DOCS_TLS_ENABLED is true but TELEMETRY_DOCS_TLS_CERT_PATH is missing, empty, or points to an unreadable file"
        exit 1
    fi
    if [ -z "$TELEMETRY_DOCS_TLS_KEY_PATH" ] || [ ! -f "$TELEMETRY_DOCS_TLS_KEY_PATH" ] || [ ! -r "$TELEMETRY_DOCS_TLS_KEY_PATH" ]; then
        echo "ERROR: TELEMETRY_DOCS_TLS_ENABLED is true but TELEMETRY_DOCS_TLS_KEY_PATH is missing, empty, or points to an unreadable file"
        exit 1
    fi

    if [ "$TELEMETRY_DOCS_MTLS_ENABLED" = "true" ]; then
        if [ -z "$TELEMETRY_DOCS_MTLS_CA_CERT_PATH" ] || [ ! -f "$TELEMETRY_DOCS_MTLS_CA_CERT_PATH" ] || [ ! -r "$TELEMETRY_DOCS_MTLS_CA_CERT_PATH" ]; then
            echo "ERROR: TELEMETRY_DOCS_MTLS_ENABLED is true but TELEMETRY_DOCS_MTLS_CA_CERT_PATH is missing, empty, or points to an unreadable file"
            exit 1
        fi
    fi
else
    if [ "$TELEMETRY_DOCS_MTLS_ENABLED" = "true" ]; then
        echo "ERROR: TELEMETRY_DOCS_MTLS_ENABLED is true but TELEMETRY_DOCS_TLS_ENABLED is false. mTLS requires TLS to be enabled."
        exit 1
    fi
fi

# Substitute environment variables in nginx config
# First generate the appropriate server blocks
NGINX_TEMPLATE="/nginx.conf.template"
TEMP_TEMPLATE="/tmp/nginx.conf.tmp"
cp $NGINX_TEMPLATE $TEMP_TEMPLATE

if [ "$TELEMETRY_DOCS_TLS_ENABLED" = "true" ]; then
    # Generate server blocks for TLS enabled
    SERVER_BLOCKS="
    # HTTP server redirecting to HTTPS when TLS is enabled
    server {
        listen 80;
        listen [::]:80;

        server_name _;
        root /static;

        # Keep health and status endpoints accessible over HTTP for backward compatibility
        location /status {
            otel_trace off;
            stub_status on;
            access_log  on;
            allow all;
        }

        location /health {
            otel_trace off;
            add_header Content-Type application/json always;
            return 200 '{\"status\": \"healthy\", \"service\": \"telemetry-docs\"}';
        }

        location /ready {
            otel_trace off;
            add_header Content-Type application/json always;
            if (-f \$document_root/index.html) {
                return 200 '{\"status\": \"ready\", \"service\": \"telemetry-docs\", \"content_available\": true}';
            }
            return 503 '{\"status\": \"not_ready\", \"service\": \"telemetry-docs\", \"content_available\": false}';
        }

        # Redirect all other traffic to HTTPS
        location / {
            return 301 https://\$host\$request_uri;
        }
    }

    # HTTPS server with TLS configuration
    server {
        listen 443 ssl;
        listen [::]:443 ssl;

        resolver 127.0.0.11;
        autoindex off;

        server_name _;
        server_tokens off;

        root /static;
        gzip_static on;

        # TLS Configuration
        ssl_certificate $TELEMETRY_DOCS_TLS_CERT_PATH;
        ssl_certificate_key $TELEMETRY_DOCS_TLS_KEY_PATH;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;"

    if [ "$TELEMETRY_DOCS_MTLS_ENABLED" = "true" ]; then
        SERVER_BLOCKS="$SERVER_BLOCKS
        # mTLS Configuration
        ssl_client_certificate $TELEMETRY_DOCS_MTLS_CA_CERT_PATH;
        ssl_verify_client on;
        ssl_verify_depth 2;"
    fi

    SERVER_BLOCKS="$SERVER_BLOCKS
        # Don't trace static assets
        location ~* \.(css|js|png|jpg|jpeg|svg|ico|woff|woff2|ttf|eot|map|json|xml|gz)$ {
            otel_trace off;
            expires 1y;
            add_header Cache-Control \"public, immutable\";
        }

        location /assets/ {
            otel_trace off;
            expires 1y;
            add_header Cache-Control \"public, immutable\";
        }

        location /search/ {
            otel_trace off;
        }

        location /status {
            otel_trace off;
            stub_status on;
            access_log  on;
            allow all;
        }

        location /health {
            otel_trace off;
            add_header Content-Type application/json always;
            return 200 '{\"status\": \"healthy\", \"service\": \"telemetry-docs\"}';
        }

        location /ready {
            otel_trace off;
            add_header Content-Type application/json always;
            if (-f \$document_root/index.html) {
                return 200 '{\"status\": \"ready\", \"service\": \"telemetry-docs\", \"content_available\": true}';
            }
            return 503 '{\"status\": \"not_ready\", \"service\": \"telemetry-docs\", \"content_available\": false}';
        }
    }"
else
    # Generate plain HTTP server block
    SERVER_BLOCKS="
    # Plain HTTP server when TLS is disabled
    server {
        listen ${TELEMETRY_DOCS_PORT};
        listen [::]:${TELEMETRY_DOCS_PORT};

        resolver 127.0.0.11;
        autoindex off;

        server_name _;
        server_tokens off;

        root /static;
        gzip_static on;

        # Don't trace static assets
        location ~* \.(css|js|png|jpg|jpeg|svg|ico|woff|woff2|ttf|eot|map|json|xml|gz)$ {
            otel_trace off;
            expires 1y;
            add_header Cache-Control \"public, immutable\";
        }

        location /assets/ {
            otel_trace off;
            expires 1y;
            add_header Cache-Control \"public, immutable\";
        }

        location /search/ {
            otel_trace off;
        }

        location /status {
            otel_trace off;
            stub_status on;
            access_log  on;
            allow all;
        }

        location /health {
            otel_trace off;
            add_header Content-Type application/json always;
            return 200 '{\"status\": \"healthy\", \"service\": \"telemetry-docs\"}';
        }

        location /ready {
            otel_trace off;
            add_header Content-Type application/json always;
            if (-f \$document_root/index.html) {
                return 200 '{\"status\": \"ready\", \"service\": \"telemetry-docs\", \"content_available\": true}';
            }
            return 503 '{\"status\": \"not_ready\", \"service\": \"telemetry-docs\", \"content_available\": false}';
        }
    }"
fi

# Replace the #SERVER_BLOCKS# placeholder with the generated blocks
sed -i "s/#SERVER_BLOCKS#/$SERVER_BLOCKS/g" $TEMP_TEMPLATE

# Substitute remaining environment variables in nginx config
envsubst '$OTEL_COLLECTOR_HOST $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME $TELEMETRY_DOCS_PORT $NGINX_GRACEFUL_SHUTDOWN_TIMEOUT' < $TEMP_TEMPLATE > /etc/nginx/nginx.conf

# Clean up temporary file
rm $TEMP_TEMPLATE

# Graceful shutdown handler
graceful_shutdown() {
    echo "[shutdown] Initiating graceful shutdown: waiting up to ${NGINX_GRACEFUL_SHUTDOWN_TIMEOUT}s for in-flight requests to complete"
    # Send SIGQUIT to nginx master process to trigger graceful shutdown
    NGINX_PID=$(cat /tmp/nginx.pid)
    kill -QUIT $NGINX_PID
    
    # Wait for nginx to exit or timeout
    WAIT_START=$(date +%s)
    while kill -0 $NGINX_PID 2>/dev/null; do
        CURRENT_TIME=$(date +%s)
        ELAPSED=$((CURRENT_TIME - WAIT_START))
        if [ $ELAPSED -ge $NGINX_GRACEFUL_SHUTDOWN_TIMEOUT ]; then
            echo "[shutdown] Grace period expired, forcing termination of remaining connections"
            kill -TERM $NGINX_PID
            wait $NGINX_PID
            exit 0
        fi
        sleep 0.5
    done
    
    echo "[shutdown] All in-flight requests completed, exiting normally"
    exit 0
}

# Trap shutdown signals
trap graceful_shutdown SIGTERM SIGQUIT SIGINT

# Start nginx in background
nginx -g 'daemon off;' &
NGINX_PID=$!

# Wait for nginx process
wait $NGINX_PID
