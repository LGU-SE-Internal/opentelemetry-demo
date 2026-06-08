import os
import json
import subprocess
import time
from kafka import KafkaAdminClient
from flask import Flask, jsonify, request

app = Flask(__name__)

KAFKA_BOOTSTRAP = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
HEALTH_PORT = int(os.getenv('KAFKA_HEALTH_PORT', 8080))

def is_broker_process_running():
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        return 'kafka.Kafka' in result.stdout
    except Exception:
        return False

def check_cluster_connectivity():
    try:
        admin_client = KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP, request_timeout_ms=300)
        brokers = admin_client.describe_cluster()['brokers']
        admin_client.close()
        return len(brokers) > 0, None
    except Exception as e:
        return False, str(e)

def check_partition_isr_health():
    try:
        admin_client = KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP, request_timeout_ms=300)
        topics = admin_client.list_topics()
        if not topics:
            admin_client.close()
            return True, []
        
        out_of_sync = []
        for topic in topics:
            partitions = admin_client.describe_topics([topic])[0]['partitions']
            for partition in partitions:
                isr = partition['isr']
                replicas = partition['replicas']
                if len(isr) < len(replicas) * 0.5:
                    out_of_sync.append(f"{topic}-{partition['partition']} (ISR: {len(isr)}, Replicas: {len(replicas)})")
        admin_client.close()
        return len(out_of_sync) == 0, out_of_sync
    except Exception as e:
        return False, [str(e)]

@app.route('/health/liveness', methods=['GET'])
def liveness():
    if is_broker_process_running():
        return jsonify({
            "status": "ok",
            "message": "Kafka broker process is running"
        }), 200
    else:
        return jsonify({
            "status": "unhealthy",
            "message": "Kafka broker process not found or unresponsive",
            "error": "process_not_running"
        }), 503

@app.route('/health/readiness', methods=['GET'])
def readiness():
    conn_ok, conn_err = check_cluster_connectivity()
    if not conn_ok:
        return jsonify({
            "status": "unhealthy",
            "message": f"Failed to connect to Kafka cluster: {conn_err}",
            "error": "cluster_connection_failed"
        }), 503
    
    isr_ok, out_of_sync = check_partition_isr_health()
    if not isr_ok:
        return jsonify({
            "status": "unhealthy",
            "message": f"Partitions out of sync: {', '.join(out_of_sync)}",
            "error": "partitions_out_of_sync"
        }), 503
    
    return jsonify({
        "status": "ok",
        "message": "Kafka broker is ready to accept requests"
    }), 200

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "status": "error",
        "message": "Endpoint not found"
    }), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        "status": "error",
        "message": "Method not allowed"
    }), 405

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=HEALTH_PORT)
