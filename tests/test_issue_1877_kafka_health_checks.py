#!/usr/bin/env python3
import pytest
import subprocess
import os
import yaml

KAFKA_LIVENESS_SCRIPT = "/opt/kafka/bin/kafka-liveness-probe.sh"
KAFKA_READINESS_SCRIPT = "/opt/kafka/bin/kafka-readiness-probe.sh"
K8S_MANIFEST_PATH = "./kubernetes/kafka/deployment.yaml"

@pytest.mark.ac1
def test_ac1_liveness_success_when_broker_running_responding():
    """AC-1: Liveness probe exits 0 when broker running and responds to metadata within 5s"""
    # Precondition: Kafka broker is running healthy
    result = subprocess.run([KAFKA_LIVENESS_SCRIPT], capture_output=True, text=True)
    assert result.returncode == 0, f"Liveness probe failed with code {result.returncode}: {result.stderr}"

@pytest.mark.ac2
def test_ac2_liveness_failure_when_broker_not_running():
    """AC-2: Liveness probe exits non-zero when broker process not running"""
    # Precondition: Kafka broker process is stopped
    result = subprocess.run([KAFKA_LIVENESS_SCRIPT], capture_output=True, text=True)
    assert result.returncode != 0, f"Liveness probe succeeded unexpectedly when broker not running"

@pytest.mark.ac3
def test_ac3_liveness_failure_when_broker_unresponsive():
    """AC-3: Liveness probe exits non-zero when broker running but unresponsive to metadata >10s"""
    # Precondition: Kafka broker process exists but is hanging/unresponsive
    result = subprocess.run([KAFKA_LIVENESS_SCRIPT], capture_output=True, text=True, timeout=15)
    assert result.returncode != 0, f"Liveness probe succeeded unexpectedly when broker unresponsive"

@pytest.mark.ac4
def test_ac4_readiness_success_when_broker_healthy():
    """AC-4: Readiness probe exits 0 when broker joined cluster, partitions in-sync, accepts produce/consume"""
    # Precondition: Kafka broker is fully healthy and part of cluster
    result = subprocess.run([KAFKA_READINESS_SCRIPT], capture_output=True, text=True)
    assert result.returncode == 0, f"Readiness probe failed with code {result.returncode}: {result.stderr}"

@pytest.mark.ac5
def test_ac5_readiness_failure_when_broker_not_in_cluster():
    """AC-5: Readiness probe exits non-zero when broker ID not found in cluster metadata"""
    # Precondition: Broker process running but not yet joined cluster
    result = subprocess.run([KAFKA_READINESS_SCRIPT], capture_output=True, text=True)
    assert result.returncode != 0, f"Readiness probe succeeded unexpectedly when broker not in cluster"

@pytest.mark.ac6
def test_ac6_readiness_failure_when_partitions_out_of_sync():
    """AC-6: Readiness probe exits non-zero when any assigned partition out of sync"""
    # Precondition: Broker is in cluster but has out-of-sync partitions
    result = subprocess.run([KAFKA_READINESS_SCRIPT], capture_output=True, text=True)
    assert result.returncode != 0, f"Readiness probe succeeded unexpectedly with out-of-sync partitions"

@pytest.mark.ac7
def test_ac7_readiness_failure_when_produce_consume_rejected():
    """AC-7: Readiness probe exits non-zero when produce/consume requests are rejected"""
    # Precondition: Broker is in cluster, partitions synced but rejects traffic
    result = subprocess.run([KAFKA_READINESS_SCRIPT], capture_output=True, text=True)
    assert result.returncode != 0, f"Readiness probe succeeded unexpectedly when produce/consume rejected"

@pytest.mark.ac8
def test_ac8_scripts_executable_no_external_deps():
    """AC-8: Health check scripts are executable and run without additional dependencies"""
    # Check scripts are executable
    assert os.access(KAFKA_LIVENESS_SCRIPT, os.X_OK), f"Liveness script {KAFKA_LIVENESS_SCRIPT} is not executable"
    assert os.access(KAFKA_READINESS_SCRIPT, os.X_OK), f"Readiness script {KAFKA_READINESS_SCRIPT} is not executable"
    
    # Check they run without missing dependencies (exit code 3 is config error, which is expected in test env, not missing deps)
    live_result = subprocess.run([KAFKA_LIVENESS_SCRIPT], capture_output=True, text=True)
    assert "command not found" not in live_result.stderr, f"Liveness script missing dependencies: {live_result.stderr}"
    assert live_result.returncode != 127, f"Liveness script failed with missing command (code 127)"
    
    ready_result = subprocess.run([KAFKA_READINESS_SCRIPT], capture_output=True, text=True)
    assert "command not found" not in ready_result.stderr, f"Readiness script missing dependencies: {ready_result.stderr}"
    assert ready_result.returncode != 127, f"Readiness script failed with missing command (code 127)"

@pytest.mark.ac9
def test_ac9_k8s_manifest_includes_probes():
    """AC-9: Kubernetes deployment manifest includes configured liveness/readiness exec probes"""
    assert os.path.exists(K8S_MANIFEST_PATH), f"Kafka deployment manifest not found at {K8S_MANIFEST_PATH}"
    
    with open(K8S_MANIFEST_PATH, 'r') as f:
        manifests = list(yaml.safe_load_all(f))
    
    deployment = None
    for manifest in manifests:
        if manifest and manifest.get('kind') == 'Deployment' and 'kafka' in manifest.get('metadata', {}).get('name', ''):
            deployment = manifest
            break
    
    assert deployment is not None, "Kafka Deployment not found in manifest"
    
    containers = deployment['spec']['template']['spec']['containers']
    kafka_container = None
    for container in containers:
        if 'kafka' in container.get('name', ''):
            kafka_container = container
            break
    
    assert kafka_container is not None, "Kafka container not found in Deployment"
    
    # Check liveness probe
    assert 'livenessProbe' in kafka_container, "Liveness probe missing from Kafka container"
    liveness = kafka_container['livenessProbe']
    assert 'exec' in liveness, "Liveness probe is not exec type"
    assert KAFKA_LIVENESS_SCRIPT in liveness['exec']['command'], "Liveness probe does not use correct script"
    assert liveness.get('initialDelaySeconds') == 30, f"Liveness initial delay expected 30, got {liveness.get('initialDelaySeconds')}"
    assert liveness.get('periodSeconds') == 10, f"Liveness period expected 10, got {liveness.get('periodSeconds')}"
    assert liveness.get('timeoutSeconds') == 5, f"Liveness timeout expected 5, got {liveness.get('timeoutSeconds')}"
    assert liveness.get('failureThreshold') == 3, f"Liveness failure threshold expected 3, got {liveness.get('failureThreshold')}"
    
    # Check readiness probe
    assert 'readinessProbe' in kafka_container, "Readiness probe missing from Kafka container"
    readiness = kafka_container['readinessProbe']
    assert 'exec' in readiness, "Readiness probe is not exec type"
    assert KAFKA_READINESS_SCRIPT in readiness['exec']['command'], "Readiness probe does not use correct script"
    assert readiness.get('initialDelaySeconds') == 60, f"Readiness initial delay expected 60, got {readiness.get('initialDelaySeconds')}"
    assert readiness.get('periodSeconds') == 5, f"Readiness period expected 5, got {readiness.get('periodSeconds')}"
    assert readiness.get('timeoutSeconds') == 10, f"Readiness timeout expected 10, got {readiness.get('timeoutSeconds')}"
    assert readiness.get('failureThreshold') == 3, f"Readiness failure threshold expected 3, got {readiness.get('failureThreshold')}"

@pytest.mark.ac10
def test_ac10_health_checks_enabled_by_default():
    """AC-10: Health checks are active by default, no manual config required"""
    # Check probes are not commented out and no feature flags disable them
    with open(K8S_MANIFEST_PATH, 'r') as f:
        content = f.read()
    
    assert "livenessProbe:" in content, "Liveness probe commented out or missing from manifest"
    assert "readinessProbe:" in content, "Readiness probe commented out or missing from manifest"
    # Verify no conditional enablement requiring user config
    assert "{{" not in content or "if .Values.kafka.enableHealthChecks" not in content, "Health checks are behind a feature flag, not enabled by default"
