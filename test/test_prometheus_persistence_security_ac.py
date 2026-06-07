#!/usr/bin/env python3
"""Integration tests for Prometheus persistence, health probes, security and resources ACs for issue #1224"""
import subprocess
import json
import requests
import time
import pytest

NAMESPACE = "default"
PROMETHEUS_LABEL = "app.kubernetes.io/name=prometheus"
PROMETHEUS_PORT = 9090
PROMETHEUS_STORAGE_PATH = "/prometheus"
PROMETHEUS_UID = 65534

def get_prometheus_pod_name():
    """Helper to get current Prometheus pod name"""
    cmd = f"kubectl get pods -l {PROMETHEUS_LABEL} -n {NAMESPACE} -o jsonpath='{{.items[0].metadata.name}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("No Prometheus pod found")
    return result.stdout.strip()

def get_prometheus_deployment():
    """Helper to get Prometheus deployment spec"""
    cmd = f"kubectl get deployment prometheus -n {NAMESPACE} -o json"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("No Prometheus deployment found")
    return json.loads(result.stdout)

def test_ac1_metrics_persist_after_pod_restart():
    """AC-1: Metrics collected before restart are preserved and queryable after pod restart"""
    pre_restart_pod = get_prometheus_pod_name()
    
    # Port forward to Prometheus API
    pf = subprocess.Popen(f"kubectl port-forward pod/{pre_restart_pod} {PROMETHEUS_PORT}:{PROMETHEUS_PORT}", shell=True)
    time.sleep(2)
    
    # Get sample metric value before restart (e.g. up metric count)
    resp = requests.get(f"http://localhost:{PROMETHEUS_PORT}/api/v1/query", params={"query": "count(up)"}, timeout=10)
    assert resp.status_code == 200
    pre_result = resp.json()
    assert pre_result["status"] == "success"
    pre_metric_count = int(pre_result["data"]["result"][0]["value"][1])
    assert pre_metric_count > 0, "No metrics found before restart to test persistence"
    
    # Also query a specific metric timestamp to verify later
    resp = requests.get(f"http://localhost:{PROMETHEUS_PORT}/api/v1/query", params={"query": "prometheus_tsdb_head_min_time_seconds"}, timeout=10)
    assert resp.status_code == 200
    pre_min_time = float(resp.json()["data"]["result"][0]["value"][1])
    
    pf.terminate()
    
    # Delete pod to trigger restart
    subprocess.run(f"kubectl delete pod {pre_restart_pod} -n {NAMESPACE}", shell=True, check=True)
    
    # Wait for new pod to become ready
    time.sleep(60)
    new_pod = get_prometheus_pod_name()
    assert new_pod != pre_restart_pod, "Prometheus pod did not restart"
    
    # Port forward to new pod
    pf2 = subprocess.Popen(f"kubectl port-forward pod/{new_pod} {PROMETHEUS_PORT}:{PROMETHEUS_PORT}", shell=True)
    time.sleep(2)
    
    # Verify we have metrics from before the restart
    resp = requests.get(f"http://localhost:{PROMETHEUS_PORT}/api/v1/query", params={"query": f"count(up) @ {pre_min_time + 1}"}, timeout=10)
    assert resp.status_code == 200
    post_result = resp.json()
    assert post_result["status"] == "success"
    post_metric_count = int(post_result["data"]["result"][0]["value"][1])
    assert post_metric_count == pre_metric_count, f"Metrics count mismatch after restart: pre={pre_metric_count}, post={post_metric_count}"
    
    pf2.terminate()

def test_ac2_readiness_probe_failure_marks_pod_notready():
    """AC-2: Kubernetes marks pod as NotReady when /-/ready returns non-200 for 3 consecutive checks"""
    deploy = get_prometheus_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    readiness = container.get("readinessProbe", {})
    assert readiness, "No readinessProbe configured on Prometheus container"
    assert readiness["httpGet"]["path"] == "/-/ready"
    assert readiness["httpGet"]["port"] == PROMETHEUS_PORT
    assert readiness["initialDelaySeconds"] == 10
    assert readiness["periodSeconds"] == 5
    assert readiness["failureThreshold"] == 3
    
    # Now test actual behavior: block /-/ready endpoint and check pod status
    pod = get_prometheus_pod_name()
    # Add iptables rule to block readiness endpoint
    subprocess.run(f"kubectl exec -n {NAMESPACE} {pod} -- iptables -A INPUT -p tcp --dport {PROMETHEUS_PORT} -m string --string '/-/ready' --algo bm -j DROP", shell=True, check=True)
    
    # Wait for failure threshold to trigger
    time.sleep(15) # 3 * 5s period
    
    # Check pod status is NotReady
    cmd = f"kubectl get pod {pod} -n {NAMESPACE} -o jsonpath='{{.status.conditions[?(@.type==\"Ready\")].status}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.stdout.strip() == "False", "Pod should be NotReady when readiness probe fails"
    
    # Cleanup iptables rule
    subprocess.run(f"kubectl exec -n {NAMESPACE} {pod} -- iptables -D INPUT -p tcp --dport {PROMETHEUS_PORT} -m string --string '/-/ready' --algo bm -j DROP", shell=True, check=True)
    
    # Wait for pod to become ready again
    time.sleep(10)

def test_ac3_liveness_probe_failure_triggers_restart():
    """AC-3: Kubernetes automatically restarts pod when /-/healthy returns non-200 for 3 consecutive checks"""
    deploy = get_prometheus_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    liveness = container.get("livenessProbe", {})
    assert liveness, "No livenessProbe configured on Prometheus container"
    assert liveness["httpGet"]["path"] == "/-/healthy"
    assert liveness["httpGet"]["port"] == PROMETHEUS_PORT
    assert liveness["initialDelaySeconds"] == 30
    assert liveness["periodSeconds"] == 10
    assert liveness["failureThreshold"] == 3
    
    pod = get_prometheus_pod_name()
    # Get initial restart count
    cmd = f"kubectl get pod {pod} -n {NAMESPACE} -o jsonpath='{{.status.containerStatuses[0].restartCount}}'"
    initial_restarts = int(subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip())
    
    # Block liveness endpoint
    subprocess.run(f"kubectl exec -n {NAMESPACE} {pod} -- iptables -A INPUT -p tcp --dport {PROMETHEUS_PORT} -m string --string '/-/healthy' --algo bm -j DROP", shell=True, check=True)
    
    # Wait for liveness failure threshold to trigger restart
    time.sleep(35) # 3 * 10s period + 5s buffer
    
    # Get new pod name (it should have restarted)
    new_pod = get_prometheus_pod_name()
    
    if new_pod == pod:
        # Same pod name, check restart count
        cmd = f"kubectl get pod {pod} -n {NAMESPACE} -o jsonpath='{{.status.containerStatuses[0].restartCount}}'"
        new_restarts = int(subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip())
        assert new_restarts > initial_restarts, "Pod should have restarted when liveness probe failed"
    else:
        # New pod name, restart happened
        assert True
    
    # Cleanup: no need to remove iptables rule as pod restarted

def test_ac4_non_root_user_configured():
    """AC-4: No processes run as root; Prometheus runs with UID 65534 (nobody)"""
    deploy = get_prometheus_deployment()
    pod_spec = deploy["spec"]["template"]["spec"]
    security_context = pod_spec.get("securityContext", {})
    assert security_context, "No pod securityContext configured"
    assert security_context["runAsNonRoot"] is True
    assert security_context["runAsUser"] == PROMETHEUS_UID
    assert security_context["fsGroup"] == PROMETHEUS_UID
    
    # Verify actual running process UID
    pod = get_prometheus_pod_name()
    cmd = f"kubectl exec -n {NAMESPACE} {pod} -- ps -o uid= -C prometheus"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, "Failed to get prometheus process UID"
    process_uid = int(result.stdout.strip())
    assert process_uid == PROMETHEUS_UID, f"Prometheus process running as UID {process_uid}, expected {PROMETHEUS_UID}"
    
    # Verify no processes running as root (UID 0)
    cmd = f"kubectl exec -n {NAMESPACE} {pod} -- ps -o uid= | grep '^ *0$'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode != 0, "Found processes running as root user in pod"

def test_ac5_resource_requests_configured():
    """AC-5: Pod is only scheduled on nodes with at least 100m CPU and 256Mi memory free"""
    deploy = get_prometheus_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert resources, "No resources configured on Prometheus container"
    assert "requests" in resources
    assert resources["requests"]["cpu"] == "100m"
    assert resources["requests"]["memory"] == "256Mi"
    
    # Verify scheduling requirement
    cmd = f"kubectl describe deployment prometheus -n {NAMESPACE} | grep -A 2 'Requests:'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert "cpu: 100m" in result.stdout
    assert "memory: 256Mi" in result.stdout

def test_ac6_resource_limits_configured():
    """AC-6: Container is throttled at CPU >500m, OOM killed at memory >512Mi"""
    deploy = get_prometheus_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert resources, "No resources configured on Prometheus container"
    assert "limits" in resources
    assert resources["limits"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "512Mi"

def test_ac7_pvc_created_and_bound():
    """AC-7: 10Gi PersistentVolumeClaim prometheus-storage is created and bound to PV"""
    cmd = f"kubectl get pvc prometheus-storage -n {NAMESPACE} -o json"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, "PVC prometheus-storage not found"
    pvc = json.loads(result.stdout)
    
    # Verify PVC spec
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"]
    storage_req = pvc["spec"]["resources"]["requests"]["storage"]
    if "Gi" in storage_req:
        assert int(storage_req.replace("Gi", "")) >= 10, f"PVC storage {storage_req} is less than 10Gi"
    elif "Mi" in storage_req:
        assert int(storage_req.replace("Mi", "")) >= 10240, f"PVC storage {storage_req} is less than 10Gi"
    
    # Verify PVC is bound
    assert pvc["status"]["phase"] == "Bound", "PVC prometheus-storage is not bound to a PersistentVolume"
    
    # Verify PVC is mounted to deployment
    deploy = get_prometheus_deployment()
    volumes = deploy["spec"]["template"]["spec"].get("volumes", [])
    pvc_volume = [v for v in volumes if v.get("persistentVolumeClaim", {}).get("claimName") == "prometheus-storage"]
    assert len(pvc_volume) == 1, "PVC prometheus-storage not referenced in deployment volumes"
    volume_name = pvc_volume[0]["name"]
    
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    mount = [m for m in volume_mounts if m["name"] == volume_name]
    assert len(mount) == 1, "PVC volume not mounted to Prometheus container"
    assert mount[0]["mountPath"] == PROMETHEUS_STORAGE_PATH, f"PVC mounted to {mount[0]['mountPath']}, expected {PROMETHEUS_STORAGE_PATH}"
    assert mount[0].get("readOnly", False) is False, "PVC mount is read-only"
