import yaml
import time
import subprocess
import pytest
import re

KAFKA_DEPLOYMENT_PATH = "/workspace/k8s/kafka-deployment.yaml"
KAFKA_DOCKERFILE_PATH = "/workspace/src/kafka/Dockerfile"
KAFKA_NAMESPACE = "default"
KAFKA_SERVICE = "kafka-service"


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


def test_ac1_kafka_process_running_non_root():
    """AC-1: ps aux | grep kafka inside pod returns process running under UID != 0"""
    pod_name = get_running_kafka_pod_name()
    cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- ps aux | grep kafka.Kafka | grep -v grep"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to list kafka processes: {result.stderr}"
    process_line = result.stdout.strip()
    uid_field = process_line.split()[0]
    assert uid_field != "root" and uid_field != "0", f"Kafka process running as root user/UID 0: {process_line}"


def test_ac2_resource_limits_and_requests_configured():
    """AC-2: Kafka deployment resources meet minimum requests and maximum limits thresholds"""
    deployments = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    kafka_container = get_kafka_container_spec(deployments)
    resources = kafka_container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})

    # Check requests
    assert "cpu" in requests, "resources.requests.cpu not configured"
    cpu_request_val = requests["cpu"]
    cpu_request_m = int(re.sub(r"[^0-9]", "", cpu_request_val)) if "m" in cpu_request_val else int(float(cpu_request_val)*1000)
    assert cpu_request_m >= 500, f"CPU request {cpu_request_val} is less than required minimum 500m"

    assert "memory" in requests, "resources.requests.memory not configured"
    mem_request_val = requests["memory"]
    mem_request_gib = float(re.sub(r"[^0-9.]", "", mem_request_val)) if "Gi" in mem_request_val else float(re.sub(r"[^0-9.]", "", mem_request_val))/1024
    assert mem_request_gib >= 1, f"Memory request {mem_request_val} is less than required minimum 1Gi"

    # Check limits
    assert "cpu" in limits, "resources.limits.cpu not configured"
    cpu_limit_val = limits["cpu"]
    cpu_limit_cores = float(re.sub(r"[^0-9.]", "", cpu_limit_val)) if "m" not in cpu_limit_val else float(re.sub(r"[^0-9.]", "", cpu_limit_val))/1000
    assert cpu_limit_cores <= 2, f"CPU limit {cpu_limit_val} exceeds maximum allowed 2 cores"

    assert "memory" in limits, "resources.limits.memory not configured"
    mem_limit_val = limits["memory"]
    mem_limit_gib = float(re.sub(r"[^0-9.]", "", mem_limit_val)) if "Gi" in mem_limit_val else float(re.sub(r"[^0-9.]", "", mem_limit_val))/1024
    assert mem_limit_gib <=4, f"Memory limit {mem_limit_val} exceeds maximum allowed 4Gi"


def test_ac3_liveness_probe_configured_correctly():
    """AC-3: LivenessProbe configured for TCP port 9092 with correct parameters"""
    deployments = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    kafka_container = get_kafka_container_spec(deployments)
    liveness_probe = kafka_container.get("livenessProbe", {})
    assert liveness_probe, "LivenessProbe not configured for kafka container"
    assert "tcpSocket" in liveness_probe, "LivenessProbe does not use TCP socket check"
    assert liveness_probe["tcpSocket"].get("port") == 9092, "LivenessProbe not targeting port 9092"
    assert liveness_probe.get("initialDelaySeconds") == 30, f"initialDelaySeconds should be 30, got {liveness_probe.get('initialDelaySeconds')}"
    assert liveness_probe.get("periodSeconds") == 10, f"periodSeconds should be 10, got {liveness_probe.get('periodSeconds')}"
    assert liveness_probe.get("failureThreshold") == 3, f"failureThreshold should be 3, got {liveness_probe.get('failureThreshold')}"


def test_ac4_readiness_probe_configured_correctly():
    """AC-4: ReadinessProbe configured with kafka-broker-api-versions.sh command and correct parameters"""
    deployments = load_kubernetes_deployment(KAFKA_DEPLOYMENT_PATH)
    kafka_container = get_kafka_container_spec(deployments)
    readiness_probe = kafka_container.get("readinessProbe", {})
    assert readiness_probe, "ReadinessProbe not configured for kafka container"
    assert "exec" in readiness_probe, "ReadinessProbe does not use exec command check"
    command = readiness_probe["exec"].get("command", [])
    assert "kafka-broker-api-versions.sh" in " ".join(command), "ReadinessProbe command does not include kafka-broker-api-versions.sh"
    assert "--bootstrap-server localhost:9092" in " ".join(command), "ReadinessProbe command not targeting localhost:9092"
    assert readiness_probe.get("initialDelaySeconds") == 20, f"initialDelaySeconds should be 20, got {readiness_probe.get('initialDelaySeconds')}"
    assert readiness_probe.get("periodSeconds") == 5, f"periodSeconds should be 5, got {readiness_probe.get('periodSeconds')}"
    assert readiness_probe.get("failureThreshold") == 3, f"failureThreshold should be 3, got {readiness_probe.get('failureThreshold')}"


def test_ac5_readiness_probe_success_within_60s():
    """AC-5: Kafka pod transitions to Ready status within 60 seconds of container start"""
    # Restart deployment to test fresh startup
    subprocess.run(f"kubectl rollout restart deployment/kafka -n {KAFKA_NAMESPACE}", shell=True, check=True)
    start_time = time.time()
    while time.time() - start_time < 60:
        cmd = f"kubectl get pods -n {KAFKA_NAMESPACE} -l app=kafka -o jsonpath='{{.items[0].status.containerStatuses[0].ready}}'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() == "true":
            return
        time.sleep(2)
    assert False, "Kafka pod did not become Ready within 60 seconds of startup"


def test_ac6_liveness_probe_failure_triggers_restart():
    """AC-6: Blocking port 9092 causes livenessProbe failure and pod restart within 30s"""
    pod_name = get_running_kafka_pod_name()
    # Get initial restart count
    initial_restart_count = subprocess.run(
        f"kubectl get pod {pod_name} -n {KAFKA_NAMESPACE} -o jsonpath='{{.status.containerStatuses[0].restartCount}}'",
        shell=True, capture_output=True, text=True
    ).stdout.strip()

    # Block port 9092 using iptables inside pod
    block_cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- iptables -A INPUT -p tcp --dport 9092 -j DROP"
    subprocess.run(block_cmd, shell=True, check=True)

    # Wait for restart
    start_time = time.time()
    while time.time() - start_time < 30:
        current_restart_count = subprocess.run(
            f"kubectl get pod {pod_name} -n {KAFKA_NAMESPACE} -o jsonpath='{{.status.containerStatuses[0].restartCount}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        if current_restart_count > initial_restart_count:
            return
        time.sleep(2)
    assert False, "Kafka pod did not restart within 30 seconds of livenessProbe failure"


def test_ac7_readiness_probe_failure_removes_from_endpoints():
    """AC-7: When readinessProbe fails, pod is removed from service endpoints"""
    # First wait for pod to be ready and present in endpoints
    start_time = time.time()
    while time.time() - start_time < 60:
        endpoints_cmd = f"kubectl get endpoints {KAFKA_SERVICE} -n {KAFKA_NAMESPACE} -o jsonpath='{{.subsets[0].addresses}}'"
        result = subprocess.run(endpoints_cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and len(result.stdout.strip()) > 0:
            break
        time.sleep(2)

    # Simulate broken broker by stopping kafka process
    pod_name = get_running_kafka_pod_name()
    stop_kafka_cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- pkill -STOP java"
    subprocess.run(stop_kafka_cmd, shell=True, check=True)

    # Wait for readiness probe to fail and remove from endpoints
    start_time = time.time()
    while time.time() - start_time < 30:
        endpoints_cmd = f"kubectl get endpoints {KAFKA_SERVICE} -n {KAFKA_NAMESPACE} -o jsonpath='{{.subsets[0].notReadyAddresses}}'"
        result = subprocess.run(endpoints_cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and len(result.stdout.strip()) > 0:
            # Resume kafka process to restore functionality
            resume_kafka_cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- pkill -CONT java"
            subprocess.run(resume_kafka_cmd, shell=True, check=True)
            return
        time.sleep(2)

    # Restore kafka process even if test fails
    resume_kafka_cmd = f"kubectl exec {pod_name} -n {KAFKA_NAMESPACE} -- pkill -CONT java"
    subprocess.run(resume_kafka_cmd, shell=True, check=True)
    assert False, "Kafka pod not removed from service endpoints after readinessProbe failure"


def test_ac8_dockerfile_non_root_user_configured():
    """AC-8: Dockerfile creates UID 1000 user, sets permissions, runs as USER 1000"""
    with open(KAFKA_DOCKERFILE_PATH, 'r') as f:
        dockerfile_content = f.read()
    
    # Check for user creation with UID 1000
    assert re.search(r"RUN.*useradd.*-u 1000", dockerfile_content) or re.search(r"USER 1000", dockerfile_content), "Dockerfile does not set USER 1000"
    # Check for chown/permission set on data/log directories
    assert re.search(r"RUN.*chown.*1000.*(/kafka/data|/var/lib/kafka|/var/log/kafka)", dockerfile_content, re.IGNORECASE), "Dockerfile does not set permissions on kafka data/log directories for UID 1000"
    # Verify final USER directive is 1000
    lines = dockerfile_content.split("\n")
    last_user_line = None
    for line in lines:
        if line.strip().startswith("USER"):
            last_user_line = line.strip()
    assert last_user_line == "USER 1000", f"Final USER directive in Dockerfile is {last_user_line}, expected USER 1000"
