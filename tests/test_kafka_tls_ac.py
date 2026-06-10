import yaml
import time
import subprocess
from kafka import KafkaProducer, KafkaConsumer
import pytest
import os
import tempfile
from pathlib import Path

KAFKA_DEPLOYMENT_PATH = "./k8s/kafka-deployment.yaml"
KAFKA_NAMESPACE = "default"
KAFKA_SERVICE = "kafka-service"
KAFKA_DOCS_PATH = "./docs/services/kafka.md"
TEST_TOPIC = "test-tls-topic-12345"
TEST_MESSAGE = b"test-tls-message"
# Dummy test cert paths (these are placeholders for actual test certs used in test runs)
TEST_KEYSTORE_PATH = "/tmp/test-keystore.p12"
TEST_TRUSTSTORE_PATH = "/tmp/test-truststore.p12"
TEST_KEYSTORE_PASSWORD = "test-keystore-pass"
TEST_TRUSTSTORE_PASSWORD = "test-truststore-pass"
TEST_CLIENT_KEYSTORE_PATH = "/tmp/test-client-keystore.p12"
TEST_INVALID_CLIENT_KEYSTORE_PATH = "/tmp/test-invalid-client-keystore.p12"


def load_kubernetes_deployment(file_path):
    with open(file_path, 'r') as f:
        return list(yaml.safe_load_all(f))


def set_kafka_env_vars(deployment_docs, env_vars):
    for doc in deployment_docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "kafka":
            containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            for c in containers:
                if c.get("name") == "kafka":
                    # Update env vars
                    existing_env = c.get("env", [])
                    # Remove any existing TLS env vars first
                    new_env = [e for e in existing_env if not e.get("name", "").startswith("KAFKA_TLS_") and not e.get("name", "").startswith("KAFKA_MTLS_")]
                    # Add new env vars
                    for key, value in env_vars.items():
                        new_env.append({"name": key, "value": str(value)})
                    c["env"] = new_env
    return deployment_docs


def deploy_kafka(deployment_docs):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.safe_dump_all(deployment_docs, f)
        temp_file = f.name
    try:
        subprocess.run(f"kubectl apply -f {temp_file} -n {KAFKA_NAMESPACE}", shell=True, check=True, capture_output=True)
        # Wait for deployment to rollout
        subprocess.run(f"kubectl rollout status deployment/kafka -n {KAFKA_NAMESPACE} --timeout=60s", shell=True, check=True, capture_output=True)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr.decode()
    finally:
        os.unlink(temp_file)


def get_running_kafka_pod_name():
    cmd = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].metadata.name}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to get running kafka pod: {result.stderr}"
    return result.stdout.strip()


def test_ac1_tls_disabled_default_plaintext():
    """AC-1: When KAFKA_TLS_ENABLED is not set or false, Kafka starts with PLAINTEXT listeners only, backward compatible"""
    base_deployment = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    # Set TLS disabled explicitly
    updated_deployment = set_kafka_env_vars(base_deployment, {
        "KAFKA_TLS_ENABLED": "false"
    })
    deploy_success, deploy_error = deploy_kafka(updated_deployment)
    assert deploy_success, f"Kafka failed to start with TLS disabled: {deploy_error}"
    
    # Verify internal listener works on PLAINTEXT port 9092
    producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9092")
    producer.send(TEST_TOPIC, value=TEST_MESSAGE)
    producer.flush()
    
    consumer = KafkaConsumer(TEST_TOPIC, bootstrap_servers=f"{KAFKA_SERVICE}:9092", auto_offset_reset='earliest', consumer_timeout_ms=5000)
    messages = [msg.value for msg in consumer]
    assert len(messages) >= 1 and TEST_MESSAGE in messages, "Plaintext connection failed when TLS is disabled"
    
    # Verify no SSL listener on port 9093 (should not respond to TLS connections)
    with pytest.raises(Exception):
        KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9093", security_protocol="SSL", request_timeout_ms=3000)


def test_ac2_tls_enabled_no_mtls_ssl_listener_works():
    """AC-2: When KAFKA_TLS_ENABLED=true with valid keystore, SSL listener on 9093 accepts TLS connections without client auth"""
    base_deployment = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    updated_deployment = set_kafka_env_vars(base_deployment, {
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_TLS_KEYSTORE_PATH": TEST_KEYSTORE_PATH,
        "KAFKA_TLS_KEYSTORE_PASSWORD": TEST_KEYSTORE_PASSWORD,
        "KAFKA_MTLS_ENABLED": "false"
    })
    deploy_success, deploy_error = deploy_kafka(updated_deployment)
    assert deploy_success, f"Kafka failed to start with TLS enabled: {deploy_error}"
    
    # Verify SSL connection works without client cert
    producer = KafkaProducer(
        bootstrap_servers=f"{KAFKA_SERVICE}:9093",
        security_protocol="SSL",
        ssl_truststore_location=TEST_TRUSTSTORE_PATH,
        ssl_truststore_password=TEST_TRUSTSTORE_PASSWORD
    )
    producer.send(TEST_TOPIC, value=TEST_MESSAGE)
    producer.flush()
    
    consumer = KafkaConsumer(
        TEST_TOPIC,
        bootstrap_servers=f"{KAFKA_SERVICE}:9093",
        security_protocol="SSL",
        ssl_truststore_location=TEST_TRUSTSTORE_PATH,
        ssl_truststore_password=TEST_TRUSTSTORE_PASSWORD,
        auto_offset_reset='earliest',
        consumer_timeout_ms=5000
    )
    messages = [msg.value for msg in consumer]
    assert len(messages) >= 1 and TEST_MESSAGE in messages, "SSL connection failed when TLS is enabled without mTLS"


def test_ac3_mtls_enabled_client_auth_enforced():
    """AC-3: When mTLS enabled, only clients with valid trusted certificates can connect"""
    base_deployment = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    updated_deployment = set_kafka_env_vars(base_deployment, {
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_TLS_KEYSTORE_PATH": TEST_KEYSTORE_PATH,
        "KAFKA_TLS_KEYSTORE_PASSWORD": TEST_KEYSTORE_PASSWORD,
        "KAFKA_MTLS_ENABLED": "true",
        "KAFKA_TLS_TRUSTSTORE_PATH": TEST_TRUSTSTORE_PATH,
        "KAFKA_TLS_TRUSTSTORE_PASSWORD": TEST_TRUSTSTORE_PASSWORD,
        "KAFKA_TLS_CLIENT_AUTH": "required"
    })
    deploy_success, deploy_error = deploy_kafka(updated_deployment)
    assert deploy_success, f"Kafka failed to start with mTLS enabled: {deploy_error}"
    
    # Test 1: Valid client cert works
    producer = KafkaProducer(
        bootstrap_servers=f"{KAFKA_SERVICE}:9093",
        security_protocol="SSL",
        ssl_keystore_location=TEST_CLIENT_KEYSTORE_PATH,
        ssl_keystore_password=TEST_KEYSTORE_PASSWORD,
        ssl_truststore_location=TEST_TRUSTSTORE_PATH,
        ssl_truststore_password=TEST_TRUSTSTORE_PASSWORD
    )
    producer.send(TEST_TOPIC, value=TEST_MESSAGE)
    producer.flush()
    
    # Test 2: No client cert fails
    with pytest.raises(Exception):
        KafkaProducer(
            bootstrap_servers=f"{KAFKA_SERVICE}:9093",
            security_protocol="SSL",
            ssl_truststore_location=TEST_TRUSTSTORE_PATH,
            ssl_truststore_password=TEST_TRUSTSTORE_PASSWORD,
            request_timeout_ms=5000
        )
    
    # Test 3: Invalid/untrusted client cert fails
    with pytest.raises(Exception):
        KafkaProducer(
            bootstrap_servers=f"{KAFKA_SERVICE}:9093",
            security_protocol="SSL",
            ssl_keystore_location=TEST_INVALID_CLIENT_KEYSTORE_PATH,
            ssl_keystore_password=TEST_KEYSTORE_PASSWORD,
            ssl_truststore_location=TEST_TRUSTSTORE_PATH,
            ssl_truststore_password=TEST_TRUSTSTORE_PASSWORD,
            request_timeout_ms=5000
        )


def test_ac4_tls_enabled_missing_keystore_fails():
    """AC-4: When KAFKA_TLS_ENABLED=true but keystore path or password missing, Kafka fails to start with clear error"""
    base_deployment = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    # Test case 1: Missing keystore path
    updated_deployment = set_kafka_env_vars(base_deployment, {
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_TLS_KEYSTORE_PASSWORD": TEST_KEYSTORE_PASSWORD
    })
    deploy_success, deploy_error = deploy_kafka(updated_deployment)
    assert not deploy_success, "Kafka should fail to start when TLS enabled but keystore path missing"
    assert "KAFKA_TLS_KEYSTORE_PATH" in deploy_error, "Error message should indicate missing KAFKA_TLS_KEYSTORE_PATH"
    
    # Test case 2: Missing keystore password
    updated_deployment = set_kafka_env_vars(base_deployment, {
        "KAFKA_TLS_ENABLED": "true",
        "KAFKA_TLS_KEYSTORE_PATH": TEST_KEYSTORE_PATH
    })
    deploy_success, deploy_error = deploy_kafka(updated_deployment)
    assert not deploy_success, "Kafka should fail to start when TLS enabled but keystore password missing"
    assert "KAFKA_TLS_KEYSTORE_PASSWORD" in deploy_error, "Error message should indicate missing KAFKA_TLS_KEYSTORE_PASSWORD"


def test_ac5_mtls_enabled_without_tls_fails():
    """AC-5: When KAFKA_MTLS_ENABLED=true but KAFKA_TLS_ENABLED=false, Kafka fails to start with clear error"""
    base_deployment = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    updated_deployment = set_kafka_env_vars(base_deployment, {
        "KAFKA_TLS_ENABLED": "false",
        "KAFKA_MTLS_ENABLED": "true"
    })
    deploy_success, deploy_error = deploy_kafka(updated_deployment)
    assert not deploy_success, "Kafka should fail to start when mTLS enabled but TLS is disabled"
    assert "KAFKA_TLS_ENABLED must be true" in deploy_error or "TLS must be enabled for mTLS" in deploy_error, "Error message should indicate TLS must be enabled for mTLS"


def test_ac6_kafka_tls_documentation_exists():
    """AC-6: Documentation exists at ./docs/services/kafka.md covering TLS/mTLS setup"""
    docs_path = Path(KAFKA_DOCS_PATH)
    assert docs_path.exists(), f"Kafka documentation missing at {KAFKA_DOCS_PATH}"
    
    docs_content = docs_path.read_text()
    # Check for required content sections
    required_sections = [
        "TLS certificates",
        "KAFKA_TLS_ENABLED",
        "KAFKA_MTLS_ENABLED",
        "docker-compose",
        "Kubernetes",
        "keystore"
    ]
    for section in required_sections:
        assert section.lower() in docs_content.lower(), f"Documentation missing required section: {section}"


def test_ac7_default_config_no_breaking_changes():
    """AC-7: Default configuration (no TLS variables set) behaves exactly as before, no breaking changes"""
    base_deployment = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    # No TLS env vars set at all
    updated_deployment = set_kafka_env_vars(base_deployment, {})
    deploy_success, deploy_error = deploy_kafka(updated_deployment)
    assert deploy_success, f"Kafka failed to start with default configuration: {deploy_error}"
    
    # Verify same behavior as before: PLAINTEXT works on port 9092
    producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9092")
    producer.send(TEST_TOPIC, value=TEST_MESSAGE)
    producer.flush()
    
    consumer = KafkaConsumer(TEST_TOPIC, bootstrap_servers=f"{KAFKA_SERVICE}:9092", auto_offset_reset='earliest', consumer_timeout_ms=5000)
    messages = [msg.value for msg in consumer]
    assert len(messages) >= 1 and TEST_MESSAGE in messages, "Default plaintext connection failed"
