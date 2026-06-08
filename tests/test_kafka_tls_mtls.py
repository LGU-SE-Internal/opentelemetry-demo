import yaml
import time
import subprocess
from kafka import KafkaProducer, KafkaConsumer
import pytest
import os
import tempfile

KAFKA_DEPLOYMENT_PATH = "/workspace/k8s/kafka-deployment.yaml"
KAFKA_NAMESPACE = "default"
KAFKA_SERVICE = "kafka-service"
TEST_TOPIC = "test-kafka-tls-topic"
TEST_MESSAGE = b"test-kafka-tls-message-123"

# Dummy test certificates paths (these don't exist yet, for negative testing)
DUMMY_KEYSTORE_PATH = "/etc/kafka/secrets/invalid.keystore.jks"
DUMMY_TRUSTSTORE_PATH = "/etc/kafka/secrets/invalid.truststore.jks"


def load_kubernetes_deployment(file_path):
    with open(file_path, 'r') as f:
        return list(yaml.safe_load_all(f))


def update_kafka_env_vars(env_updates):
    """Update kafka deployment environment variables temporarily for test"""
    docs = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "kafka":
            containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            for c in containers:
                if c.get("name") == "kafka":
                    env = c.get("env", [])
                    # Update existing vars or add new
                    for key, value in env_updates.items():
                        found = False
                        for e in env:
                            if e["name"] == key:
                                e["value"] = str(value)
                                found = True
                                break
                        if not found:
                            env.append({"name": key, "value": str(value)})
                    c["env"] = env
    # Write back modified deployment
    with open(KAFKA_DEPLOYMENT_PATH, 'w') as f:
        yaml.dump_all(docs, f)
    # Apply the update
    subprocess.run(f"kubectl apply -f {KAFKA_DEPLOYMENT_PATH}", shell=True, check=True)
    # Wait for rollout to complete
    subprocess.run(f"kubectl rollout restart deployment/kafka -n {KAFKA_NAMESPACE}", shell=True, check=True)


def reset_kafka_env_vars():
    """Reset kafka deployment to original state (no TLS vars set)"""
    docs = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "kafka":
            containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            for c in containers:
                if c.get("name") == "kafka":
                    env = c.get("env", [])
                    # Remove all TLS related env vars
                    new_env = [e for e in env if not e["name"].startswith("KAFKA_TLS_") and not e["name"].startswith("KAFKA_MTLS_") and not e["name"].startswith("KAFKA_KEYSTORE_") and not e["name"].startswith("KAFKA_TRUSTSTORE_")]
                    c["env"] = new_env
    # Write back original deployment
    with open(KAFKA_DEPLOYMENT_PATH, 'w') as f:
        yaml.dump_all(docs, f)
    # Apply reset
    subprocess.run(f"kubectl apply -f {KAFKA_DEPLOYMENT_PATH}", shell=True, check=True)
    subprocess.run(f"kubectl rollout restart deployment/kafka -n {KAFKA_NAMESPACE}", shell=True, check=True)


def get_kafka_pod_status():
    """Return (phase, reason, message) for kafka pod"""
    cmd = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].status.phase}} {{.items[0].status.containerStatuses[0].state.waiting.reason}} {{.items[0].status.containerStatuses[0].state.waiting.message}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        return ("Unknown", "", "")
    parts = result.stdout.strip().split(" ", 2)
    phase = parts[0] if len(parts) > 0 else "Unknown"
    reason = parts[1] if len(parts) > 1 else ""
    message = parts[2] if len(parts) > 2 else ""
    return (phase, reason, message)


def test_ac1_tls_disabled_only_plaintext_listener():
    """AC-1: When KAFKA_TLS_ENABLED is unset/false, broker only listens on plaintext port 9092, clients connect without TLS"""
    # Reset to default state (no TLS vars)
    reset_kafka_env_vars()
    # Wait for pod to be running
    time.sleep(60)
    
    # Test connection to 9092 plaintext works
    try:
        producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9092", request_timeout_ms=5000)
        producer.close()
    except Exception as e:
        assert False, f"Plaintext connection to port 9092 failed when TLS disabled: {str(e)}"
    
    # Test connection to 9093 fails (should not be open)
    try:
        producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9093", request_timeout_ms=5000)
        producer.close()
        assert False, "Port 9093 is accessible when TLS is disabled, should not be listening"
    except Exception:
        # Expected failure
        pass


def test_ac2_tls_enabled_no_mtls():
    """AC-2: TLS enabled, mTLS disabled: listener on 9093 TLS 1.2+, no client cert needed, plaintext fails on 9093"""
    # Enable TLS only
    update_kafka_env_vars({
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_KEYSTORE_PATH": "/tmp/dummy.keystore.jks",  # For test, we expect this to fail first if implementation not present
        "KAFKA_KEYSTORE_PASSWORD": "testpass123",
        "KAFKA_MTLS_ENABLED": "false"
    })
    # Wait for pod to start
    time.sleep(60)
    
    # Test plaintext connection to 9093 fails
    try:
        producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9093", security_protocol="PLAINTEXT", request_timeout_ms=5000)
        producer.close()
        assert False, "Plaintext connection to port 9093 succeeded when TLS is enabled, should be rejected"
    except Exception:
        # Expected failure
        pass
    
    # Test TLS connection without client cert succeeds
    try:
        producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9093", security_protocol="SSL", ssl_check_hostname=False, request_timeout_ms=5000)
        producer.close()
    except Exception as e:
        assert False, f"TLS connection to port 9093 failed without client cert when mTLS is disabled: {str(e)}"


def test_ac3_tls_mtls_enabled():
    """AC-3: TLS + mTLS enabled: valid cert clients connect, invalid/no cert rejected"""
    # Enable TLS + mTLS
    update_kafka_env_vars({
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_KEYSTORE_PATH": "/tmp/dummy.keystore.jks",
        "KAFKA_KEYSTORE_PASSWORD": "testpass123",
        "KAFKA_MTLS_ENABLED": "true",
        "KAFKA_TRUSTSTORE_PATH": "/tmp/dummy.truststore.jks",
        "KAFKA_TRUSTSTORE_PASSWORD": "testpass456"
    })
    # Wait for pod to start
    time.sleep(60)
    
    # Test connection without client cert fails
    try:
        producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9093", security_protocol="SSL", ssl_check_hostname=False, request_timeout_ms=5000)
        producer.close()
        assert False, "TLS connection without client cert succeeded when mTLS is enabled, should be rejected"
    except Exception:
        # Expected failure
        pass
    
    # Test connection with invalid cert fails (we use dummy certs here)
    try:
        producer = KafkaProducer(
            bootstrap_servers=f"{KAFKA_SERVICE}:9093",
            security_protocol="SSL",
            ssl_check_hostname=False,
            ssl_cafile="/tmp/invalid-ca.crt",
            ssl_certfile="/tmp/invalid-client.crt",
            ssl_keyfile="/tmp/invalid-client.key",
            request_timeout_ms=5000
        )
        producer.close()
        assert False, "TLS connection with invalid client cert succeeded when mTLS is enabled, should be rejected"
    except Exception:
        # Expected failure
        pass


def test_ac4_tls_enabled_missing_keystore_fails_start():
    """AC-4: TLS enabled but missing keystore path or empty password → broker fails to start with 'missing keystore configuration' error"""
    # Enable TLS without providing keystore password
    update_kafka_env_vars({
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_KEYSTORE_PATH": DUMMY_KEYSTORE_PATH,
        "KAFKA_KEYSTORE_PASSWORD": "",
        "KAFKA_MTLS_ENABLED": "false"
    })
    # Wait for pod to fail starting
    time.sleep(30)
    
    phase, reason, message = get_kafka_pod_status()
    assert phase in ["Pending", "CrashLoopBackOff"], f"Kafka pod should not be running with missing keystore password, phase is {phase}"
    assert "missing keystore configuration" in message.lower(), f"Error message does not contain 'missing keystore configuration', message: {message}"


def test_ac5_mtls_enabled_missing_truststore_fails_start():
    """AC-5: mTLS enabled but missing truststore path or empty password → broker fails to start with 'missing truststore configuration' error"""
    # Enable mTLS without providing truststore password
    update_kafka_env_vars({
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_KEYSTORE_PATH": DUMMY_KEYSTORE_PATH,
        "KAFKA_KEYSTORE_PASSWORD": "testpass123",
        "KAFKA_MTLS_ENABLED": "true",
        "KAFKA_TRUSTSTORE_PATH": DUMMY_TRUSTSTORE_PATH,
        "KAFKA_TRUSTSTORE_PASSWORD": ""
    })
    # Wait for pod to fail starting
    time.sleep(30)
    
    phase, reason, message = get_kafka_pod_status()
    assert phase in ["Pending", "CrashLoopBackOff"], f"Kafka pod should not be running with missing truststore password, phase is {phase}"
    assert "missing truststore configuration" in message.lower(), f"Error message does not contain 'missing truststore configuration', message: {message}"


def test_ac6_readme_contains_tls_env_var_documentation():
    """AC-6: Kafka service README contains complete table of all new TLS/mTLS environment variables"""
    readme_path = "/workspace/src/kafka/README.md"
    with open(readme_path, 'r') as f:
        readme_content = f.read()
    
    required_vars = [
        "KAFKA_TLS_ENABLED",
        "KAFKA_MTLS_ENABLED",
        "KAFKA_KEYSTORE_PATH",
        "KAFKA_KEYSTORE_PASSWORD",
        "KAFKA_TRUSTSTORE_PATH",
        "KAFKA_TRUSTSTORE_PASSWORD"
    ]
    
    for var in required_vars:
        assert var in readme_content, f"Environment variable {var} is missing from Kafka README"
    assert "default value" in readme_content.lower(), "README does not mention default values for TLS variables"
    assert "usage" in readme_content.lower() or "instructions" in readme_content.lower(), "README does not contain usage instructions for TLS configuration"
