#!/usr/bin/env python3
"""
Integration tests for Kafka health checks, probes and resource limits as per issue #1212 spec.
"""
import os
import subprocess
import time
import pytest
from kubernetes import client, config

# Constants from spec interface
DOCKERFILE_PATH = "src/kafka/Dockerfile"
HEALTHCHECK_CMD = "kafka-broker-api-versions.sh --bootstrap-server localhost:9092"
HEALTHCHECK_START_PERIOD = 30
HEALTHCHECK_INTERVAL = 10
HEALTHCHECK_TIMEOUT = 5
HEALTHCHECK_RETRIES = 3

LIVENESS_PROBE_INITIAL_DELAY = 60
LIVENESS_PROBE_PERIOD = 30
LIVENESS_PROBE_TIMEOUT = 5
LIVENESS_PROBE_FAILURE_THRESHOLD = 3

READINESS_PROBE_INITIAL_DELAY = 30
READINESS_PROBE_PERIOD = 10
READINESS_PROBE_TIMEOUT = 5
READINESS_PROBE_FAILURE_THRESHOLD = 2

CPU_REQUEST = "500m"
CPU_LIMIT = "1000m"
MEMORY_REQUEST = "1Gi"
MEMORY_LIMIT = "2Gi"


def test_ac1_kafka_dockerfile_has_valid_healthcheck():
    """AC-1: Kafka Dockerfile includes a valid HEALTHCHECK instruction with defined command and parameters"""
    assert os.path.exists(DOCKERFILE_PATH), f"Kafka Dockerfile not found at {DOCKERFILE_PATH}"
    
    with open(DOCKERFILE_PATH, "r") as f:
        content = f.read()
    
    # Verify HEALTHCHECK instruction exists
    assert "HEALTHCHECK" in content, "HEALTHCHECK instruction missing from Kafka Dockerfile"
    
    # Verify command matches spec
    assert HEALTHCHECK_CMD in content, f"HEALTHCHECK command does not match expected: {HEALTHCHECK_CMD}"
    
    # Verify parameters match spec
    assert f"--start-period={HEALTHCHECK_START_PERIOD}s" in content, f"HEALTHCHECK start period missing or incorrect, expected {HEALTHCHECK_START_PERIOD}s"
    assert f"--interval={HEALTHCHECK_INTERVAL}s" in content, f"HEALTHCHECK interval missing or incorrect, expected {HEALTHCHECK_INTERVAL}s"
    assert f"--timeout={HEALTHCHECK_TIMEOUT}s" in content, f"HEALTHCHECK timeout missing or incorrect, expected {HEALTHCHECK_TIMEOUT}s"
    assert f"--retries={HEALTHCHECK_RETRIES}" in content, f"HEALTHCHECK retries missing or incorrect, expected {HEALTHCHECK_RETRIES}"


def test_ac1_healthcheck_returns_correct_exit_codes():
    """AC-1: Healthcheck command returns 0 on healthy broker, 1 on unresponsive broker"""
    # First build the kafka image
    build_result = subprocess.run(
        ["docker", "build", "-t", "test-kafka-healthcheck", "src/kafka/"],
        capture_output=True,
        text=True
    )
    assert build_result.returncode == 0, f"Failed to build kafka image: {build_result.stderr}"
    
    # Start a kafka container in unhealthy state (no zookeeper/kraft, so broker won't start properly)
    unhealthy_container = subprocess.run(
        ["docker", "run", "-d", "--name", "test-kafka-unhealthy", "-e", "KAFKA_CFG_NODE_ID=1", "test-kafka-healthcheck"],
        capture_output=True,
        text=True
    )
    assert unhealthy_container.returncode == 0, f"Failed to start unhealthy kafka container: {unhealthy_container.stderr}"
    
    # Wait a bit, then run healthcheck command inside
    time.sleep(10)
    healthcheck_unhealthy = subprocess.run(
        ["docker", "exec", "test-kafka-unhealthy", "bash", "-c", HEALTHCHECK_CMD],
        capture_output=True
    )
    assert healthcheck_unhealthy.returncode == 1, f"Expected exit code 1 for unhealthy broker, got {healthcheck_unhealthy.returncode}"
    
    # Clean up unhealthy container
    subprocess.run(["docker", "rm", "-f", "test-kafka-unhealthy"], capture_output=True)
    
    # TODO: Add test for healthy broker scenario when implementation is done
    # (requires properly configured kafka container with storage and cluster setup)


def test_ac2_k8s_deployment_has_liveness_probe():
    """AC-2: Kubernetes Kafka deployment has livenessProbe with defined action and parameters"""
    # Load k8s config
    try:
        config.load_kube_config()
    except:
        pytest.skip("Kubernetes config not available, skipping cluster integration test")
    
    apps_v1 = client.AppsV1Api()
    deployments = apps_v1.list_namespaced_deployment(namespace="default", field_selector="metadata.name=kafka")
    
    assert len(deployments.items) > 0, "Kafka deployment not found in default namespace"
    kafka_deploy = deployments.items[0]
    
    liveness_probe = kafka_deploy.spec.template.spec.containers[0].liveness_probe
    assert liveness_probe is not None, "Liveness probe missing from Kafka deployment"
    
    # Verify exec action
    assert liveness_probe.exec is not None, "Liveness probe is not exec type"
    assert HEALTHCHECK_CMD in " ".join(liveness_probe.exec.command), f"Liveness probe command does not match expected {HEALTHCHECK_CMD}"
    
    # Verify parameters
    assert liveness_probe.initial_delay_seconds == LIVENESS_PROBE_INITIAL_DELAY, f"Liveness probe initial delay wrong, expected {LIVENESS_PROBE_INITIAL_DELAY}"
    assert liveness_probe.period_seconds == LIVENESS_PROBE_PERIOD, f"Liveness probe period wrong, expected {LIVENESS_PROBE_PERIOD}"
    assert liveness_probe.timeout_seconds == LIVENESS_PROBE_TIMEOUT, f"Liveness probe timeout wrong, expected {LIVENESS_PROBE_TIMEOUT}"
    assert liveness_probe.failure_threshold == LIVENESS_PROBE_FAILURE_THRESHOLD, f"Liveness probe failure threshold wrong, expected {LIVENESS_PROBE_FAILURE_THRESHOLD}"


def test_ac3_k8s_deployment_has_readiness_probe():
    """AC-3: Kubernetes Kafka deployment has readinessProbe with defined action and parameters"""
    try:
        config.load_kube_config()
    except:
        pytest.skip("Kubernetes config not available, skipping cluster integration test")
    
    apps_v1 = client.AppsV1Api()
    deployments = apps_v1.list_namespaced_deployment(namespace="default", field_selector="metadata.name=kafka")
    
    assert len(deployments.items) > 0, "Kafka deployment not found in default namespace"
    kafka_deploy = deployments.items[0]
    
    readiness_probe = kafka_deploy.spec.template.spec.containers[0].readiness_probe
    assert readiness_probe is not None, "Readiness probe missing from Kafka deployment"
    
    # Verify exec action
    assert readiness_probe.exec is not None, "Readiness probe is not exec type"
    assert HEALTHCHECK_CMD in " ".join(readiness_probe.exec.command), f"Readiness probe command does not match expected {HEALTHCHECK_CMD}"
    
    # Verify parameters
    assert readiness_probe.initial_delay_seconds == READINESS_PROBE_INITIAL_DELAY, f"Readiness probe initial delay wrong, expected {READINESS_PROBE_INITIAL_DELAY}"
    assert readiness_probe.period_seconds == READINESS_PROBE_PERIOD, f"Readiness probe period wrong, expected {READINESS_PROBE_PERIOD}"
    assert readiness_probe.timeout_seconds == READINESS_PROBE_TIMEOUT, f"Readiness probe timeout wrong, expected {READINESS_PROBE_TIMEOUT}"
    assert readiness_probe.failure_threshold == READINESS_PROBE_FAILURE_THRESHOLD, f"Readiness probe failure threshold wrong, expected {READINESS_PROBE_FAILURE_THRESHOLD}"


def test_ac4_k8s_deployment_has_resource_requests_limits():
    """AC-4: Kubernetes Kafka deployment has defined CPU and memory resource requests/limits"""
    try:
        config.load_kube_config()
    except:
        pytest.skip("Kubernetes config not available, skipping cluster integration test")
    
    apps_v1 = client.AppsV1Api()
    deployments = apps_v1.list_namespaced_deployment(namespace="default", field_selector="metadata.name=kafka")
    
    assert len(deployments.items) > 0, "Kafka deployment not found in default namespace"
    kafka_deploy = deployments.items[0]
    
    resources = kafka_deploy.spec.template.spec.containers[0].resources
    assert resources is not None, "Resources section missing from Kafka deployment container"
    
    # Verify requests
    assert resources.requests is not None, "Resource requests missing"
    assert resources.requests["cpu"] == CPU_REQUEST, f"CPU request wrong, expected {CPU_REQUEST}"
    assert resources.requests["memory"] == MEMORY_REQUEST, f"Memory request wrong, expected {MEMORY_REQUEST}"
    
    # Verify limits
    assert resources.limits is not None, "Resource limits missing"
    assert resources.limits["cpu"] == CPU_LIMIT, f"CPU limit wrong, expected {CPU_LIMIT}"
    assert resources.limits["memory"] == MEMORY_LIMIT, f"Memory limit wrong, expected {MEMORY_LIMIT}"


def test_ac5_no_false_positive_probe_failures_during_startup():
    """AC-5: No false positive probe failures during normal Kafka broker startup"""
    try:
        config.load_kube_config()
    except:
        pytest.skip("Kubernetes config not available, skipping cluster integration test")
    
    core_v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    
    # Restart kafka deployment to trigger fresh startup
    patch_result = apps_v1.patch_namespaced_deployment(
        name="kafka",
        namespace="default",
        body={"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ")}}}}}
    )
    assert patch_result is not None, "Failed to restart Kafka deployment"
    
    # Wait for pod to start and monitor events for probe failures
    start_time = time.time()
    failure_found = False
    
    while time.time() - start_time < 120:
        events = core_v1.list_namespaced_event(namespace="default", field_selector="involvedObject.kind=Pod,reason=Unhealthy")
        for event in events.items:
            if "kafka" in event.involved_object.name and ("Liveness probe failed" in event.message or "Readiness probe failed" in event.message):
                # Check if this event is after the restart time
                if event.last_timestamp.timestamp() > start_time:
                    failure_found = True
                    break
        if failure_found:
            break
        time.sleep(5)
    
    assert not failure_found, "False positive probe failure detected during Kafka startup"
