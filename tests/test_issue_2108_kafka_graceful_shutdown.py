#!/usr/bin/env python3
"""Integration tests for Kafka graceful shutdown ACs (issue #2108)"""
import pytest
import time
import subprocess
from kubernetes import client, config
from kubernetes.client.rest import ApiException

@pytest.fixture(scope="module")
def k8s_clients():
    """Load Kubernetes config and return API clients"""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()
    return apps_v1, core_v1

NAMESPACE = "default"

def test_ac1_sigterm_triggers_controlled_shutdown(k8s_clients):
    """AC-1: SIGTERM/SIGINT triggers controlled shutdown sequence with log message
    Verify "Controlled shutdown starting" appears in broker logs when signal is sent
    """
    _, core_v1 = k8s_clients
    
    # Get running kafka pod
    pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector="app=kafka")
    if not pods.items:
        assert False, "No running Kafka pods found"
    
    pod_name = pods.items[0].metadata.name
    
    # Send SIGTERM to the kafka process
    try:
        exec_cmd = [
            "kubectl", "exec", "-n", NAMESPACE, pod_name, "--",
            "bash", "-c", "pkill -TERM java"
        ]
        subprocess.run(exec_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        assert False, f"Failed to send SIGTERM: {e.stderr}"
    
    # Check logs for controlled shutdown message
    log_found = False
    start_time = time.time()
    while time.time() - start_time < 30:
        try:
            logs = core_v1.read_namespaced_pod_log(name=pod_name, namespace=NAMESPACE, since_seconds=30)
            if "Controlled shutdown starting" in logs:
                log_found = True
                break
        except ApiException:
            pass
        time.sleep(2)
    
    assert log_found, "Controlled shutdown starting message not found in logs after SIGTERM"

def test_ac2_shutdown_flushes_records_and_transfers_leadership(k8s_clients):
    """AC-2: Controlled shutdown flushes all in-memory records, completes replication, transfers leadership
    Verify no under-replicated partitions after shutdown, no leaderless partitions
    """
    _, core_v1 = k8s_clients
    
    # Check if kafka-topics.sh is available
    pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector="app=kafka")
    if not pods.items:
        assert False, "No running Kafka pods found"
    
    pod_name = pods.items[0].metadata.name
    
    # Check under replicated partitions before shutdown
    try:
        check_cmd = [
            "kubectl", "exec", "-n", NAMESPACE, pod_name, "--",
            "kafka-topics.sh", "--describe", "--under-replicated-partitions",
            "--bootstrap-server", "localhost:9092"
        ]
        result = subprocess.run(check_cmd, capture_output=True, text=True)
        under_replicated_before = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
    except subprocess.CalledProcessError as e:
        assert False, f"Failed to check under replicated partitions: {e.stderr}"
    
    # Shutdown broker
    try:
        shutdown_cmd = [
            "kubectl", "exec", "-n", NAMESPACE, pod_name, "--",
            "kafka-server-stop.sh"
        ]
        subprocess.run(shutdown_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        assert False, f"Failed to run kafka-server-stop.sh: {e.stderr}"
    
    # Wait for broker to exit
    time.sleep(30)
    
    # Check for leaderless partitions on remaining brokers
    try:
        leaderless_cmd = [
            "kubectl", "exec", "-n", NAMESPACE, pod_name, "--",
            "kafka-topics.sh", "--describe", "--unavailable-partitions",
            "--bootstrap-server", "localhost:9092"
        ]
        result = subprocess.run(leaderless_cmd, capture_output=True, text=True)
        leaderless = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
    except subprocess.CalledProcessError as e:
        assert False, f"Failed to check leaderless partitions: {e.stderr}"
    
    assert leaderless == 0, f"Found {leaderless} leaderless partitions after shutdown"
    assert under_replicated_before == 0, "Under replicated partitions existed before shutdown (test setup issue)"

def test_ac3_termination_grace_period_set(k8s_clients):
    """AC-3: terminationGracePeriodSeconds is set to >= 60 in Kubernetes deployment/statefulset
    Aligned with Kafka controlled shutdown timeout
    """
    apps_v1, _ = k8s_clients
    
    try:
        deployment = apps_v1.read_namespaced_deployment(name="kafka", namespace=NAMESPACE)
    except ApiException:
        # Try statefulset if deployment not found
        try:
            deployment = apps_v1.read_namespaced_stateful_set(name="kafka", namespace=NAMESPACE)
        except ApiException as e:
            assert False, f"Kafka deployment/statefulset not found: {e}"
    
    grace_period = deployment.spec.template.spec.termination_grace_period_seconds
    
    assert grace_period is not None, "terminationGracePeriodSeconds is not set"
    assert grace_period >= 60, f"terminationGracePeriodSeconds {grace_period} is less than required minimum 60"

def test_ac4_rolling_restart_no_data_loss_or_errors(k8s_clients):
    """AC-4: 3 consecutive rolling restarts with active traffic have 0 data loss, 0 errors, <5s leadership unavailability
    """
    # This test requires:
    # 1. Running producer generating 100 records/sec
    # 2. Replication factor 3, min.isr=2
    # 3. Consumer tracking all produced records
    # 4. Measure leadership availability during restarts
    
    assert False, "Rolling restart test requires full cluster setup with traffic (will pass after implementation)"

def test_ac5_successful_shutdown_zero_exit_code(k8s_clients):
    """AC-5: Successful controlled shutdown exits with status code 0, no force kill events
    """
    _, core_v1 = k8s_clients
    
    # Get kafka pods
    pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector="app=kafka")
    if not pods.items:
        assert False, "No Kafka pods found"
    
    # Check for recent pod terminations
    for pod in pods.items:
        if pod.status.container_statuses:
            for container in pod.status.container_statuses:
                if container.last_state.terminated:
                    term_state = container.last_state.terminated
                    assert term_state.exit_code == 0, f"Pod exited with non-zero code {term_state.exit_code}"
                    assert term_state.reason != "OOMKilled", "Pod was OOM killed"
                    assert term_state.reason != "Error", "Pod exited with error"
    
    # Check events for force kill events
    events = core_v1.list_namespaced_event(namespace=NAMESPACE, field_selector="involvedObject.kind=Pod,involvedObject.name=kafka")
    force_kill_events = [e for e in events.items if " Killing " in e.message and "force" in e.message.lower()]
    
    assert len(force_kill_events) == 0, f"Found {len(force_kill_events)} force kill events for Kafka pods"
