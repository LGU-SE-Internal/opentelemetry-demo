import yaml
import time
import subprocess
from kafka import KafkaProducer, KafkaConsumer
from kafka.admin import KafkaAdminClient, NewTopic
import pytest
import uuid

KAFKA_DEPLOYMENT_PATH = "/workspace/k8s/kafka-deployment.yaml"
KAFKA_NAMESPACE = "default"
KAFKA_SERVICE = "kafka-service"
TEST_TOPIC = f"test-security-topic-{uuid.uuid4().hex[:8]}"
TEST_MESSAGE = b"test-kafka-security-message-123"


def load_kubernetes_deployment(file_path):
    with open(file_path, 'r') as f:
        return list(yaml.safe_load_all(f))


def get_kafka_container_spec(deployment_docs):
    for doc in deployment_docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "kafka":
            containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            for c in containers:
                if c.get("name") == "kafka":
                    return c
    raise ValueError("Kafka container spec not found in deployment file")


def get_running_kafka_pod_name():
    cmd = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].metadata.name}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to get running kafka pod: {result.stderr}"
    return result.stdout.strip()


def test_ac1_run_as_non_root_explicitly_set():
    """AC-1: Kafka container explicitly sets runAsNonRoot: true"""
    deployments = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    kafka_container = get_kafka_container_spec(deployments)
    security_context = kafka_container.get("securityContext", {})
    assert security_context.get("runAsNonRoot") is True, "runAsNonRoot is not set to true in kafka container securityContext"


def test_ac2_disallow_privilege_escalation():
    """AC-2: Kafka container explicitly sets allowPrivilegeEscalation: false"""
    deployments = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    kafka_container = get_kafka_container_spec(deployments)
    security_context = kafka_container.get("securityContext", {})
    assert security_context.get("allowPrivilegeEscalation") is False, "allowPrivilegeEscalation is not set to false in kafka container securityContext"


def test_ac3_read_only_root_filesystem():
    """AC-3: Kafka container explicitly sets readOnlyRootFilesystem: true"""
    deployments = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    kafka_container = get_kafka_container_spec(deployments)
    security_context = kafka_container.get("securityContext", {})
    assert security_context.get("readOnlyRootFilesystem") is True, "readOnlyRootFilesystem is not set to true in kafka container securityContext"


def test_ac4_drop_all_capabilities_no_additions():
    """AC-4: Kafka container sets capabilities.drop: ["ALL"] with no capabilities.add entries"""
    deployments = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    kafka_container = get_kafka_container_spec(deployments)
    security_context = kafka_container.get("securityContext", {})
    capabilities = security_context.get("capabilities", {})
    assert "drop" in capabilities, "capabilities.drop not found in securityContext"
    assert capabilities["drop"] == ["ALL"], "capabilities.drop is not set to ['ALL']"
    assert "add" not in capabilities or len(capabilities["add"]) == 0, "capabilities.add has entries, should be empty"


def test_ac5_kafka_pod_running_no_crashloop():
    """AC-5: Kafka pods transition to Running status within 60 seconds, no CrashLoopBackOff events"""
    # First restart the deployment to test fresh startup
    subprocess.run(f"kubectl rollout restart deployment/kafka -n {KAFKA_NAMESPACE}", shell=True, check=True)
    # Wait up to 60 seconds for pods to be running
    start_time = time.time()
    while time.time() - start_time < 60:
        cmd = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].status.phase}}'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() == "Running":
            # Check for no CrashLoopBackOff
            cmd_status = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].status.containerStatuses[0].state.waiting.reason}}'"
            status_result = subprocess.run(cmd_status, shell=True, capture_output=True, text=True)
            if status_result.returncode != 0 or status_result.stdout.strip() != "CrashLoopBackOff":
                return
        time.sleep(5)
    assert False, "Kafka pod did not reach Running status within 60 seconds or is in CrashLoopBackOff"


def test_ac6a_kafka_produce_consume_success():
    """AC-6a: Test message produced to new topic is successfully consumed"""
    # Create test topic
    admin_client = KafkaAdminClient(bootstrap_servers=f"{KAFKA_SERVICE}:9092", client_id='test-security-admin')
    topic_list = [NewTopic(name=TEST_TOPIC, num_partitions=1, replication_factor=1)]
    admin_client.create_topics(new_topics=topic_list, validate_only=False)
    # Produce message
    producer = KafkaProducer(bootstrap_servers=f"{KAFKA_SERVICE}:9092")
    producer.send(TEST_TOPIC, value=TEST_MESSAGE)
    producer.flush()
    # Consume message
    consumer = KafkaConsumer(TEST_TOPIC, bootstrap_servers=f"{KAFKA_SERVICE}:9092", auto_offset_reset='earliest', consumer_timeout_ms=5000)
    messages = []
    for msg in consumer:
        messages.append(msg.value)
        break
    assert len(messages) == 1, "No message consumed from test topic"
    assert messages[0] == TEST_MESSAGE, "Consumed message does not match produced message"


def test_ac6b_message_persists_after_pod_restart():
    """AC-6b: Test message remains available after Kafka pod restart"""
    # Restart Kafka pod
    pod_name = get_running_kafka_pod_name()
    subprocess.run(f"kubectl delete pod {pod_name} -n {KAFKA_NAMESPACE}", shell=True, check=True)
    # Wait for new pod to be running
    start_time = time.time()
    new_pod_name = ""
    while time.time() - start_time < 60:
        cmd = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].metadata.name}}'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() != pod_name:
            # Check new pod is running
            phase_cmd = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].status.phase}}'"
            phase_result = subprocess.run(phase_cmd, shell=True, capture_output=True, text=True)
            if phase_result.returncode == 0 and phase_result.stdout.strip() == "Running":
                new_pod_name = result.stdout.strip()
                break
        time.sleep(5)
    assert new_pod_name != "", "New Kafka pod not running after restart"
    # Consume message again
    consumer = KafkaConsumer(TEST_TOPIC, bootstrap_servers=f"{KAFKA_SERVICE}:9092", auto_offset_reset='earliest', consumer_timeout_ms=5000)
    messages = []
    for msg in consumer:
        messages.append(msg.value)
        break
    assert len(messages) == 1, "No message consumed after pod restart"
    assert messages[0] == TEST_MESSAGE, "Consumed message after restart does not match original message"


def test_ac7_non_root_user_exec_check():
    """AC-7: kubectl exec whoami returns non-root, id -u is not 0"""
    pod_name = get_running_kafka_pod_name()
    # Check whoami
    whoami_cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- whoami"
    whoami_result = subprocess.run(whoami_cmd, shell=True, capture_output=True, text=True)
    assert whoami_result.returncode == 0, f"whoami exec failed: {whoami_result.stderr}"
    assert whoami_result.stdout.strip() != "root", "Kafka container is running as root user"
    # Check UID
    id_cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- id -u"
    id_result = subprocess.run(id_cmd, shell=True, capture_output=True, text=True)
    assert id_result.returncode == 0, f"id -u exec failed: {id_result.stderr}"
    assert id_result.stdout.strip() != "0", "Kafka container running with UID 0 (root)"


def test_ac8_no_new_privs_enabled():
    """AC-8: /proc/1/status shows NoNewPrivs: 1 confirming privilege escalation disabled"""
    pod_name = get_running_kafka_pod_name()
    cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- cat /proc/1/status | grep NoNewPrivs"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to read /proc/1/status: {result.stderr}"
    assert "NoNewPrivs:\t1" in result.stdout, "NoNewPrivs is not set to 1, privilege escalation not disabled"
