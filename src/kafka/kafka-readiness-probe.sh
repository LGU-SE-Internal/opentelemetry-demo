#!/bin/bash
set -eo pipefail

CONFIG_FILE="/opt/kafka/config/server.properties"
HEALTH_CHECK_TOPIC="_kafka_health_check"

# Exit code 4: Configuration error
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Configuration file $CONFIG_FILE not found"
    exit 4
fi

# Get broker ID
BROKER_ID=$(grep "^broker.id=" "$CONFIG_FILE" | cut -d'=' -f2)
if [ -z "$BROKER_ID" ]; then
    echo "ERROR: broker.id not configured in server.properties"
    exit 4
fi

# Get bootstrap server
LISTENERS=$(grep "^listeners=" "$CONFIG_FILE" | cut -d'=' -f2)
if [ -z "$LISTENERS" ]; then
    echo "ERROR: No listeners configured in server.properties"
    exit 4
fi
FIRST_LISTENER=$(echo "$LISTENERS" | cut -d',' -f1)
PORT=$(echo "$FIRST_LISTENER" | cut -d':' -f3)
BOOTSTRAP_SERVER="localhost:$PORT"

# Check if broker is in cluster metadata
# Exit code 1: Broker ID not found in cluster
BROKERS=$(/opt/kafka/bin/kafka-metadata-shell.sh --bootstrap-server "$BOOTSTRAP_SERVER" --command-config <(echo "request.timeout.ms=5000") "ls /brokers/ids" 2>/dev/null | tr '\n' ' ')
if ! echo "$BROKERS" | grep -qw "$BROKER_ID"; then
    echo "ERROR: Broker ID $BROKER_ID not found in cluster metadata"
    exit 1
fi

# Check all partitions assigned to broker are in-sync
# Exit code 2: Out-of-sync partitions
ASSIGNED_PARTITIONS=$(/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP_SERVER" --describe 2>/dev/null | grep "Replica.*$BROKER_ID" | awk '{print $1, $3}')
while read -r TOPIC PARTITION; do
    if [ -z "$TOPIC" ] || [ -z "$PARTITION" ]; then
        continue
    fi
    ISR=$(/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP_SERVER" --describe --topic "$TOPIC" --partition "$PARTITION" 2>/dev/null | grep -oP 'Isr: \K[0-9,]+')
    if ! echo "$ISR" | grep -qw "$BROKER_ID"; then
        echo "ERROR: Partition $TOPIC:$PARTITION is out of sync for broker $BROKER_ID"
        exit 2
    fi
done <<< "$ASSIGNED_PARTITIONS"

# Create health check topic if it doesn't exist
BROKER_COUNT=$(echo "$BROKERS" | wc -w)
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP_SERVER" --create --topic "$HEALTH_CHECK_TOPIC" --partitions 1 --replication-factor "$BROKER_COUNT" --if-not-exists > /dev/null 2>&1 || true

# Test produce/consume
# Exit code 3: Produce/consume rejected
TEST_MESSAGE="health_check_$(date +%s)"
if ! echo "$TEST_MESSAGE" | /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server "$BOOTSTRAP_SERVER" --topic "$HEALTH_CHECK_TOPIC" --request-timeout-ms 5000 > /dev/null 2>&1; then
    echo "ERROR: Broker rejects produce requests"
    exit 3
fi

RECEIVED_MESSAGE=$(/opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server "$BOOTSTRAP_SERVER" --topic "$HEALTH_CHECK_TOPIC" --from-beginning --max-messages 1 --timeout-ms 5000 2>/dev/null)
if [ "$RECEIVED_MESSAGE" != "$TEST_MESSAGE" ]; then
    echo "ERROR: Broker rejects consume requests or message mismatch"
    exit 3
fi

# All checks passed
echo "OK: Kafka broker is ready"
exit 0
