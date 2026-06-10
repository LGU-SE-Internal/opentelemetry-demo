#!/bin/bash
set -eo pipefail

CONFIG_FILE="/opt/kafka/config/server.properties"

# Exit code 3: Configuration error
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Configuration file $CONFIG_FILE not found"
    exit 3
fi

# Get broker port from config
LISTENERS=$(grep "^listeners=" "$CONFIG_FILE" | cut -d'=' -f2)
if [ -z "$LISTENERS" ]; then
    echo "ERROR: No listeners configured in server.properties"
    exit 3
fi

# Extract first listener port
FIRST_LISTENER=$(echo "$LISTENERS" | cut -d',' -f1)
PORT=$(echo "$FIRST_LISTENER" | cut -d':' -f3)
if [ -z "$PORT" ]; then
    echo "ERROR: Could not extract broker port from listeners"
    exit 3
fi

# Check if Kafka process is running
# Exit code 1: Process not running
if ! pgrep -f "kafka.Kafka" > /dev/null 2>&1; then
    echo "ERROR: Kafka broker process not running"
    exit 1
fi

# Check metadata request response with 10s timeout
# Exit code 2: No response
if ! timeout 10 /opt/kafka/bin/kafka-metadata-shell.sh --bootstrap-server "localhost:$PORT" --command-config <(echo "request.timeout.ms=5000") "ls /brokers/ids" > /dev/null 2>&1; then
    echo "ERROR: Kafka broker does not respond to metadata requests within 10 seconds"
    exit 2
fi

# All checks passed
echo "OK: Kafka broker is live and responsive"
exit 0
