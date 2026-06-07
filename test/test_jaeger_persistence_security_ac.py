#!/usr/bin/env python3
"""Integration tests for Jaeger persistence and security ACs for issue #1222"""
import subprocess
import json
import requests
import time
import pytest

NAMESPACE = "default"
JAEGER_LABEL = "app.kubernetes.io/name=jaeger"
JAEGER_API_PORT = 16686
JAEGER_HEALTH_PORT = 14269

def get_jaeger_pod_name():
    """Helper to get current Jaeger pod name"""
    cmd = f"kubectl get pods -l {JAEGER_LABEL} -n {NAMESPACE} -o jsonpath='{{.items[0].metadata.name}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("No Jaeger pod found")
    return result.stdout.strip()

def get_jaeger_deployment():
    """Helper to get Jaeger deployment spec"""
    cmd = f"kubectl get deployment jaeger -n {NAMESPACE} -o json"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("No Jaeger deployment found")
    return json.loads(result.stdout)

def test_ac1_traces_persist_after_pod_restart():
    """AC-1: Traces stored before pod restart are retrievable after restart"""
    # Get pre-restart trace IDs (or create test trace)
    pre_restart_pod = get_jaeger_pod_name()
    # Port forward to get traces
    pf = subprocess.Popen(f"kubectl port-forward pod/{pre_restart_pod} {JAEGER_API_PORT}:{JAEGER_API_PORT}", shell=True)
    time.sleep(2)
    
    # Get sample trace IDs (for test we check that traces endpoint returns data)
    resp = requests.get(f"http://localhost:{JAEGER_API_PORT}/api/traces?service=frontend&limit=10", timeout=10)
    assert resp.status_code == 200
    pre_traces = resp.json().get("data", [])
    assert len(pre_traces) > 0, "No pre-restart traces found to test persistence"
    test_trace_id = pre_traces[0]["traceID"]
    
    # Delete pod to trigger restart
    subprocess.run(f"kubectl delete pod {pre_restart_pod} -n {NAMESPACE}", shell=True, check=True)
    
    # Wait for new pod to come up
    time.sleep(30)
    new_pod = get_jaeger_pod_name()
    assert new_pod != pre_restart_pod, "Pod did not restart"
    
    # Check trace still exists
    pf2 = subprocess.Popen(f"kubectl port-forward pod/{new_pod} {JAEGER_API_PORT}:{JAEGER_API_PORT}", shell=True)
    time.sleep(2)
    resp = requests.get(f"http://localhost:{JAEGER_API_PORT}/api/traces/{test_trace_id}", timeout=10)
    assert resp.status_code == 200, f"Trace {test_trace_id} not found after restart"
    assert resp.json().get("data") is not None
    
    # Cleanup port forwards
    pf.terminate()
    pf2.terminate()

def test_ac2_liveness_probe_configured():
    """AC-2: Liveness probe configured correctly on /health port 14269"""
    deploy = get_jaeger_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    liveness = container.get("livenessProbe", {})
    assert liveness, "No livenessProbe configured"
    assert liveness["httpGet"]["path"] == "/health"
    assert liveness["httpGet"]["port"] == JAEGER_HEALTH_PORT
    assert liveness["initialDelaySeconds"] == 5
    assert liveness["periodSeconds"] == 10
    assert liveness["failureThreshold"] == 3

def test_ac3_readiness_probe_configured():
    """AC-3: Readiness probe configured correctly on /health port 14269"""
    deploy = get_jaeger_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    readiness = container.get("readinessProbe", {})
    assert readiness, "No readinessProbe configured"
    assert readiness["httpGet"]["path"] == "/health"
    assert readiness["httpGet"]["port"] == JAEGER_HEALTH_PORT
    assert readiness["initialDelaySeconds"] == 2
    assert readiness["periodSeconds"] == 5
    assert readiness["failureThreshold"] == 3

def test_ac4_security_context_non_root_least_privilege():
    """AC-4: Pod security context follows least privilege non-root config"""
    deploy = get_jaeger_deployment()
    pod_spec = deploy["spec"]["template"]["spec"]
    security_context = pod_spec.get("securityContext", {})
    assert security_context, "No pod securityContext configured"
    assert security_context["runAsNonRoot"] is True
    assert security_context["runAsUser"] == 1001
    assert security_context["allowPrivilegeEscalation"] is False
    assert security_context["readOnlyRootFilesystem"] is True
    assert "capabilities" in security_context
    assert "ALL" in security_context["capabilities"]["drop"]

def test_ac5_resource_requests_limits_set():
    """AC-5: Resource requests and limits set correctly"""
    deploy = get_jaeger_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert resources, "No resources configured"
    assert "requests" in resources
    assert resources["requests"]["cpu"] == "100m"
    assert resources["requests"]["memory"] == "256Mi"
    assert "limits" in resources
    assert resources["limits"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "1Gi"

def test_ac6_pvc_jaeger_storage_exists():
    """AC-6: PersistentVolumeClaim jaeger-storage exists with correct config"""
    cmd = f"kubectl get pvc jaeger-storage -n {NAMESPACE} -o json"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, "PVC jaeger-storage not found"
    pvc = json.loads(result.stdout)
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"]
    storage_req = pvc["spec"]["resources"]["requests"]["storage"]
    # Convert to Gi
    if "Gi" in storage_req:
        assert int(storage_req.replace("Gi", "")) >= 10
    elif "Mi" in storage_req:
        assert int(storage_req.replace("Mi", "")) >= 10240
    else:
        raise AssertionError(f"Storage request {storage_req} is less than 10Gi")

def test_ac7_pvc_mounted_to_container():
    """AC-7: jaeger-storage PVC mounted to /tmp/jaeger with read/write access"""
    deploy = get_jaeger_deployment()
    volumes = deploy["spec"]["template"]["spec"].get("volumes", [])
    pvc_volume = [v for v in volumes if v.get("persistentVolumeClaim", {}).get("claimName") == "jaeger-storage"]
    assert len(pvc_volume) == 1, "PVC jaeger-storage not referenced in deployment volumes"
    volume_name = pvc_volume[0]["name"]
    
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    mount = [m for m in volume_mounts if m["name"] == volume_name]
    assert len(mount) == 1, "PVC volume not mounted to container"
    assert mount[0]["mountPath"] == "/tmp/jaeger"
    assert mount[0].get("readOnly", False) is False, "Volume mount is read-only"

def test_ac8_jaeger_uses_badger_persistent_storage():
    """AC-8: Jaeger configured to use Badger persistent storage instead of in-memory"""
    deploy = get_jaeger_deployment()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    args = container.get("args", [])
    # Check for badger storage argument
    badger_arg_found = any("--storage.type=badger" in arg for arg in args)
    assert badger_arg_found, "Jaeger not configured to use badger storage"
    # Ensure in-memory is not used
    in_mem_found = any("--storage.type=memory" in arg for arg in args)
    assert not in_mem_found, "Jaeger still configured to use in-memory storage"

def test_ac9_traces_persist_after_node_reschedule():
    """AC-9: Traces remain accessible after pod is rescheduled to different node"""
    # Get current pod and node
    pre_pod = get_jaeger_pod_name()
    cmd = f"kubectl get pod {pre_pod} -n {NAMESPACE} -o jsonpath='{{.spec.nodeName}}'"
    pre_node = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
    
    # Get test trace ID
    pf = subprocess.Popen(f"kubectl port-forward pod/{pre_pod} {JAEGER_API_PORT}:{JAEGER_API_PORT}", shell=True)
    time.sleep(2)
    resp = requests.get(f"http://localhost:{JAEGER_API_PORT}/api/traces?service=frontend&limit=1", timeout=10)
    assert resp.status_code == 200
    test_trace_id = resp.json()["data"][0]["traceID"]
    pf.terminate()
    
    # Cordon current node and delete pod to force reschedule
    subprocess.run(f"kubectl cordon {pre_node}", shell=True, check=True)
    subprocess.run(f"kubectl delete pod {pre_pod} -n {NAMESPACE}", shell=True, check=True)
    time.sleep(60)
    
    # Get new pod and node
    new_pod = get_jaeger_pod_name()
    cmd = f"kubectl get pod {new_pod} -n {NAMESPACE} -o jsonpath='{{.spec.nodeName}}'"
    new_node = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
    
    # Uncordon original node
    subprocess.run(f"kubectl uncordon {pre_node}", shell=True, check=True)
    
    # Verify pod moved to new node
    assert new_node != pre_node, "Pod was not rescheduled to a new node"
    
    # Check trace still exists
    pf2 = subprocess.Popen(f"kubectl port-forward pod/{new_pod} {JAEGER_API_PORT}:{JAEGER_API_PORT}", shell=True)
    time.sleep(2)
    resp = requests.get(f"http://localhost:{JAEGER_API_PORT}/api/traces/{test_trace_id}", timeout=10)
    assert resp.status_code == 200, f"Trace {test_trace_id} not found after reschedule"
    assert resp.json().get("data") is not None
    pf2.terminate()
