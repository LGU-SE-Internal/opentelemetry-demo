#!/usr/bin/env python3
import pytest
import subprocess
import time
import os
import tempfile
from pathlib import Path
import kubernetes
from kubernetes.client import ApiClient, CoreV1Api, AppsV1Api

PROMETHEUS_IMAGE_NAME = "otel/prometheus:dev"
TEST_NAMESPACE = "test-prometheus-retention"

def run_prometheus_container(env_vars, wait_for_exit=True, timeout=30):
    """Helper to run prometheus container with given env vars"""
    cmd = ["docker", "run", "--rm"]
    for k, v in env_vars.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.append(PROMETHEUS_IMAGE_NAME)
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not wait_for_exit:
        time.sleep(10)
        return proc, None, None
    
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return proc, stdout, stderr

def get_prometheus_process_args(container_id):
    """Get command line arguments of running prometheus process inside container"""
    cmd = ["docker", "exec", container_id, "ps", "aux"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    for line in res.stdout.splitlines():
        if "/bin/prometheus" in line:
            return line
    return ""

@pytest.mark.docker
def test_ac1_docker_custom_retention_period():
    """AC-1: When PROMETHEUS_TSDB_RETENTION_PERIOD is set to 45d in Docker deployment, the running Prometheus process has --storage.tsdb.retention.time=45d in its command line arguments"""
    env = {
        "PROMETHEUS_TSDB_RETENTION_PERIOD": "45d"
    }
    proc, _, _ = run_prometheus_container(env, wait_for_exit=False)
    try:
        args = get_prometheus_process_args(proc.pid)
        assert "--storage.tsdb.retention.time=45d" in args
    finally:
        proc.kill()
        proc.communicate()

@pytest.mark.docker
def test_ac2_docker_default_retention_period():
    """AC-2: When PROMETHEUS_TSDB_RETENTION_PERIOD is not explicitly set in Docker deployment, the running Prometheus process has --storage.tsdb.retention.time=30d in its command line arguments"""
    env = {}
    proc, _, _ = run_prometheus_container(env, wait_for_exit=False)
    try:
        args = get_prometheus_process_args(proc.pid)
        assert "--storage.tsdb.retention.time=30d" in args
    finally:
        proc.kill()
        proc.communicate()

@pytest.mark.k8s
def test_ac3_k8s_custom_retention_period():
    """AC-3: When PROMETHEUS_TSDB_RETENTION_PERIOD is set to 60d in Kubernetes deployment, the running Prometheus process has --storage.tsdb.retention.time=60d in its command line arguments"""
    # First create test namespace
    subprocess.run(["kubectl", "create", "namespace", TEST_NAMESPACE], capture_output=True)
    
    # Deploy prometheus with custom retention
    helm_cmd = [
        "helm", "install", "prometheus", "./charts/prometheus",
        "--namespace", TEST_NAMESPACE,
        "--set", "prometheus.tsdbRetentionPeriod=60d"
    ]
    subprocess.run(helm_cmd, check=True, capture_output=True)
    
    # Wait for pod to start
    time.sleep(30)
    
    try:
        # Get pod name
        pod_cmd = ["kubectl", "get", "pods", "-n", TEST_NAMESPACE, "-l", "app.kubernetes.io/name=prometheus", "-o", "jsonpath={.items[0].metadata.name}"]
        pod_name = subprocess.run(pod_cmd, capture_output=True, text=True, check=True).stdout.strip()
        
        # Get process args
        exec_cmd = ["kubectl", "exec", "-n", TEST_NAMESPACE, pod_name, "--", "ps", "aux"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, check=True)
        for line in res.stdout.splitlines():
            if "/bin/prometheus" in line:
                assert "--storage.tsdb.retention.time=60d" in line
                break
        else:
            pytest.fail("Prometheus process not found")
    finally:
        # Clean up
        subprocess.run(["helm", "uninstall", "prometheus", "-n", TEST_NAMESPACE], capture_output=True)
        subprocess.run(["kubectl", "delete", "namespace", TEST_NAMESPACE], capture_output=True)

@pytest.mark.k8s
def test_ac4_k8s_default_retention_period():
    """AC-4: When PROMETHEUS_TSDB_RETENTION_PERIOD is not explicitly set in Kubernetes deployment, the running Prometheus process has --storage.tsdb.retention.time=30d in its command line arguments"""
    # First create test namespace
    subprocess.run(["kubectl", "create", "namespace", TEST_NAMESPACE], capture_output=True)
    
    # Deploy prometheus with default settings
    helm_cmd = [
        "helm", "install", "prometheus", "./charts/prometheus",
        "--namespace", TEST_NAMESPACE
    ]
    subprocess.run(helm_cmd, check=True, capture_output=True)
    
    # Wait for pod to start
    time.sleep(30)
    
    try:
        # Get pod name
        pod_cmd = ["kubectl", "get", "pods", "-n", TEST_NAMESPACE, "-l", "app.kubernetes.io/name=prometheus", "-o", "jsonpath={.items[0].metadata.name}"]
        pod_name = subprocess.run(pod_cmd, capture_output=True, text=True, check=True).stdout.strip()
        
        # Get process args
        exec_cmd = ["kubectl", "exec", "-n", TEST_NAMESPACE, pod_name, "--", "ps", "aux"]
        res = subprocess.run(exec_cmd, capture_output=True, text=True, check=True)
        for line in res.stdout.splitlines():
            if "/bin/prometheus" in line:
                assert "--storage.tsdb.retention.time=30d" in line
                break
        else:
            pytest.fail("Prometheus process not found")
    finally:
        # Clean up
        subprocess.run(["helm", "uninstall", "prometheus", "-n", TEST_NAMESPACE], capture_output=True)
        subprocess.run(["kubectl", "delete", "namespace", TEST_NAMESPACE], capture_output=True)

@pytest.mark.k8s
def test_ac5_pvc_retain_policy():
    """AC-5: Prometheus Kubernetes PersistentVolumeClaim has persistentVolumeReclaimPolicy: Retain set, so deleting the Prometheus StatefulSet does not delete the underlying PersistentVolume or its stored metrics data"""
    # First create test namespace
    subprocess.run(["kubectl", "create", "namespace", TEST_NAMESPACE], capture_output=True)
    
    # Deploy prometheus
    helm_cmd = [
        "helm", "install", "prometheus", "./charts/prometheus",
        "--namespace", TEST_NAMESPACE
    ]
    subprocess.run(helm_cmd, check=True, capture_output=True)
    
    # Wait for PVC to be created
    time.sleep(10)
    
    try:
        # Get PVC name
        pvc_cmd = ["kubectl", "get", "pvc", "-n", TEST_NAMESPACE, "-l", "app.kubernetes.io/name=prometheus", "-o", "jsonpath={.items[0].metadata.name}"]
        pvc_name = subprocess.run(pvc_cmd, capture_output=True, text=True, check=True).stdout.strip()
        
        # Get PV name bound to this PVC
        pv_cmd = ["kubectl", "get", "pvc", pvc_name, "-n", TEST_NAMESPACE, "-o", "jsonpath={.spec.volumeName}"]
        pv_name = subprocess.run(pv_cmd, capture_output=True, text=True, check=True).stdout.strip()
        
        # Check PVC annotation for retain policy
        pvc_describe_cmd = ["kubectl", "get", "pvc", pvc_name, "-n", TEST_NAMESPACE, "-o", "jsonpath={.metadata.annotations.persistentVolumeReclaimPolicy}"]
        pvc_policy = subprocess.run(pvc_describe_cmd, capture_output=True, text=True, check=True).stdout.strip()
        assert pvc_policy == "Retain"
        
        # Check PV reclaim policy
        pv_describe_cmd = ["kubectl", "get", "pv", pv_name, "-o", "jsonpath={.spec.persistentVolumeReclaimPolicy}"]
        pv_policy = subprocess.run(pv_describe_cmd, capture_output=True, text=True, check=True).stdout.strip()
        assert pv_policy == "Retain"
        
        # Delete StatefulSet
        subprocess.run(["kubectl", "delete", "statefulset", "prometheus", "-n", TEST_NAMESPACE], check=True, capture_output=True)
        time.sleep(10)
        
        # Verify PV still exists
        pv_exists_cmd = ["kubectl", "get", "pv", pv_name]
        res = subprocess.run(pv_exists_cmd, capture_output=True)
        assert res.returncode == 0, "Persistent Volume was deleted after StatefulSet deletion"
        
    finally:
        # Clean up
        subprocess.run(["helm", "uninstall", "prometheus", "-n", TEST_NAMESPACE], capture_output=True)
        subprocess.run(["kubectl", "patch", "pv", pv_name, "-p", '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'], capture_output=True)
        subprocess.run(["kubectl", "delete", "namespace", TEST_NAMESPACE], capture_output=True)

@pytest.mark.docker
def test_ac6_invalid_retention_value_fails_startup():
    """AC-6: Setting PROMETHEUS_TSDB_RETENTION_PERIOD to an invalid value (e.g. abc, 20x) causes the Prometheus container to exit with non-zero exit code on startup"""
    # Test with invalid string value
    env = {
        "PROMETHEUS_TSDB_RETENTION_PERIOD": "abc"
    }
    proc, stdout, stderr = run_prometheus_container(env, wait_for_exit=True, timeout=20)
    assert proc.returncode != 0, "Prometheus should exit with non-zero code for invalid retention value 'abc'"
    
    # Test with invalid unit
    env = {
        "PROMETHEUS_TSDB_RETENTION_PERIOD": "20x"
    }
    proc, stdout, stderr = run_prometheus_container(env, wait_for_exit=True, timeout=20)
    assert proc.returncode != 0, "Prometheus should exit with non-zero code for invalid retention value '20x'"

@pytest.mark.k8s
def test_ac7_data_preserved_after_upgrade():
    """AC-7: Existing metrics data stored in Prometheus PV is preserved after upgrading to the new configuration with Retain policy and default 30d retention period"""
    # First create test namespace
    subprocess.run(["kubectl", "create", "namespace", TEST_NAMESPACE], capture_output=True)
    
    # Deploy old version of prometheus (without the retention changes)
    old_helm_cmd = [
        "helm", "install", "prometheus", "./charts/prometheus",
        "--namespace", TEST_NAMESPACE,
        "--version", "0.1.0" # Previous version without retention changes
    ]
    subprocess.run(old_helm_cmd, check=True, capture_output=True)
    
    # Wait for pod to start
    time.sleep(30)
    
    try:
        # Get pod name
        pod_cmd = ["kubectl", "get", "pods", "-n", TEST_NAMESPACE, "-l", "app.kubernetes.io/name=prometheus", "-o", "jsonpath={.items[0].metadata.name}"]
        old_pod_name = subprocess.run(pod_cmd, capture_output=True, text=True, check=True).stdout.strip()
        
        # Write test metric data
        exec_cmd = [
            "kubectl", "exec", "-n", TEST_NAMESPACE, old_pod_name, "--",
            "curl", "-X", "POST", "http://localhost:9090/api/v1/admin/tsdb/import",
            "--data-binary", "@/testdata/sample_metrics.pb"
        ]
        subprocess.run(exec_cmd, capture_output=True)
        
        # Upgrade to new version with retention changes
        upgrade_cmd = [
            "helm", "upgrade", "prometheus", "./charts/prometheus",
            "--namespace", TEST_NAMESPACE
        ]
        subprocess.run(upgrade_cmd, check=True, capture_output=True)
        
        # Wait for new pod to start
        time.sleep(30)
        
        # Get new pod name
        new_pod_name = subprocess.run(pod_cmd, capture_output=True, text=True, check=True).stdout.strip()
        
        # Verify test metric still exists
        query_cmd = [
            "kubectl", "exec", "-n", TEST_NAMESPACE, new_pod_name, "--",
            "curl", "-s", "http://localhost:9090/api/v1/query?query=test_metric_total"
        ]
        res = subprocess.run(query_cmd, capture_output=True, text=True, check=True)
        assert '"test_metric_total"' in res.stdout
        assert len(res.json()["data"]["result"]) > 0, "Test metric not found after upgrade, data was lost"
        
    finally:
        # Clean up
        subprocess.run(["helm", "uninstall", "prometheus", "-n", TEST_NAMESPACE], capture_output=True)
        subprocess.run(["kubectl", "delete", "namespace", TEST_NAMESPACE], capture_output=True)
