#!/bin/sh
set -e

# Set default graceful shutdown timeout
export NGINX_GRACEFUL_SHUTDOWN_TIMEOUT=${NGINX_GRACEFUL_SHUTDOWN_TIMEOUT:-30}

# Substitute environment variables in nginx config
envsubst '$OTEL_COLLECTOR_HOST $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME $TELEMETRY_DOCS_PORT $NGINX_GRACEFUL_SHUTDOWN_TIMEOUT' < /nginx.conf.template > /etc/nginx/nginx.conf

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
