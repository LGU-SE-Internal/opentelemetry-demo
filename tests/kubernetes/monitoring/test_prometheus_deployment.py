"""
Integration tests for Kubernetes deployment of prometheus service
Verifies all acceptance criteria from issue #2183 spec
"""
import os
import subprocess
import yaml
import time
import pytest

PROMETHEUS_DIR = "./kubernetes/monitoring/prometheus/"
SERVICE_NAME = "prometheus"
SERVICE_ACCOUNT_NAME = "opentelemetry-demo-prometheus"
CLUSTER_ROLE_NAME = "opentelemetry-demo-prometheus"
PVC_NAME = "prometheus-storage"
TEST_NAMESPACE = os.getenv("TEST_NAMESPACE", "default")

def run_cmd(cmd, shell=True, check=True):
    """Run shell command and return output"""
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {cmd}\nStderr: {result.stderr}\nStdout: {result.stdout}")
    return result

def load_all_manifests():
    """Load all YAML manifests from prometheus directory"""
    manifests = []
    if not os.path.exists(PROMETHEUS_DIR):
        raise Exception(f"Prometheus manifest directory {PROMETHEUS_DIR} not found")
    for filename in os.listdir(PROMETHEUS_DIR):
        if not filename.endswith(".yaml") and not filename.endswith(".yml"):
            continue
        filepath = os.path.join(PROMETHEUS_DIR, filename)
        with open(filepath, 'r') as f:
            docs = list(yaml.safe_load_all(f))
            for doc in docs:
                if doc:
                    manifests.append(doc)
    return manifests

def get_manifest_by_kind(kind, name=None):
    """Get manifest of specified kind, optionally matching name"""
    manifests = load_all_manifests()
    for doc in manifests:
        if doc.get("kind") == kind:
            if name is None or doc.get("metadata", {}).get("name") == name:
                return doc
    raise Exception(f"Manifest of kind {kind} {'with name ' + name if name else ''} not found")

def get_running_pod_name():
    """Get name of running prometheus pod"""
    cmd = f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].metadata.name}}'"
    result = run_cmd(cmd, check=False)
    if result.returncode != 0:
        raise Exception("No prometheus pods found")
    return result.stdout.strip()

def test_ac1_resource_requests_limits_configured():
    """AC-1: Deployment defines resource requests: 100m CPU, 256Mi memory; resource limits: 500m CPU, 1Gi memory"""
    deploy = get_manifest_by_kind("Deployment", name=SERVICE_NAME)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "resources" in container, "Resources not configured for container"
    assert "requests" in container["resources"], "Resource requests not configured"
    assert "limits" in container["resources"], "Resource limits not configured"
    
    assert container["resources"]["requests"]["cpu"] == "100m", f"CPU request should be 100m, got {container['resources']['requests'].get('cpu')}"
    assert container["resources"]["requests"]["memory"] == "256Mi", f"Memory request should be 256Mi, got {container['resources']['requests'].get('memory')}"
    assert container["resources"]["limits"]["cpu"] == "500m", f"CPU limit should be 500m, got {container['resources']['limits'].get('cpu')}"
    assert container["resources"]["limits"]["memory"] == "1Gi", f"Memory limit should be 1Gi, got {container['resources']['limits'].get('memory')}"

def test_ac2_liveness_probe_configured():
    """AC-2: Deployment includes liveness probe targeting /-/healthy endpoint on port 9090 with specified parameters"""
    deploy = get_manifest_by_kind("Deployment", name=SERVICE_NAME)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "livenessProbe" in container, "Liveness probe not configured"
    lp = container["livenessProbe"]
    
    assert lp["httpGet"]["path"] == "/-/healthy", f"Liveness probe path should be /-/healthy, got {lp['httpGet'].get('path')}"
    assert lp["httpGet"]["port"] == 9090, f"Liveness probe port should be 9090, got {lp['httpGet'].get('port')}"
    assert lp["initialDelaySeconds"] == 30, f"Liveness probe initialDelaySeconds should be 30, got {lp.get('initialDelaySeconds')}"
    assert lp["periodSeconds"] == 15, f"Liveness probe periodSeconds should be 15, got {lp.get('periodSeconds')}"
    assert lp["timeoutSeconds"] == 10, f"Liveness probe timeoutSeconds should be 10, got {lp.get('timeoutSeconds')}"
    assert lp["failureThreshold"] == 3, f"Liveness probe failureThreshold should be 3, got {lp.get('failureThreshold')}"

def test_ac3_readiness_probe_configured():
    """AC-3: Deployment includes readiness probe targeting /-/ready endpoint on port 9090 with specified parameters"""
    deploy = get_manifest_by_kind("Deployment", name=SERVICE_NAME)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "readinessProbe" in container, "Readiness probe not configured"
    rp = container["readinessProbe"]
    
    assert rp["httpGet"]["path"] == "/-/ready", f"Readiness probe path should be /-/ready, got {rp['httpGet'].get('path')}"
    assert rp["httpGet"]["port"] == 9090, f"Readiness probe port should be 9090, got {rp['httpGet'].get('port')}"
    assert rp["initialDelaySeconds"] == 5, f"Readiness probe initialDelaySeconds should be 5, got {rp.get('initialDelaySeconds')}"
    assert rp["periodSeconds"] == 10, f"Readiness probe periodSeconds should be 10, got {rp.get('periodSeconds')}"
    assert rp["timeoutSeconds"] == 10, f"Readiness probe timeoutSeconds should be 10, got {rp.get('timeoutSeconds')}"
    assert rp["failureThreshold"] == 3, f"Readiness probe failureThreshold should be 3, got {rp.get('failureThreshold')}"

def test_ac4_security_context_configured():
    """AC-4: Deployment security context enforces least privilege with non-root user and no escalations"""
    deploy = get_manifest_by_kind("Deployment", name=SERVICE_NAME)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "securityContext" in container, "Security context not configured"
    sc = container["securityContext"]
    
    assert sc["runAsNonRoot"] == True, "Container must run as non-root user"
    assert sc["runAsUser"] == 65534, f"Container must run as user 65534 (nobody), got {sc.get('runAsUser')}"
    assert sc["allowPrivilegeEscalation"] == False, "Privilege escalation must be disabled"
    assert sc["readOnlyRootFilesystem"] == True, "Root filesystem must be read-only"
    assert "capabilities" in sc, "Capabilities must be configured"
    assert "drop" in sc["capabilities"], "Capabilities drop list must exist"
    assert "ALL" in sc["capabilities"]["drop"], "All capabilities must be dropped"
    assert "add" not in sc["capabilities"] or len(sc["capabilities"]["add"]) == 0, "No extra capabilities should be added"
    assert "privileged" not in sc or sc["privileged"] == False, "Container must not be privileged"

def test_ac5_pvc_configured_and_mounted():
    """AC-5: PersistentVolumeClaim is mounted to /prometheus path with 10Gi storage and ReadWriteOnce access mode"""
    # Check PVC manifest
    pvc = get_manifest_by_kind("PersistentVolumeClaim", name=PVC_NAME)
    assert "accessModes" in pvc["spec"], "PVC accessModes not configured"
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC must have ReadWriteOnce access mode"
    assert pvc["spec"]["resources"]["requests"]["storage"] >= "10Gi", f"PVC storage must be at least 10Gi, got {pvc['spec']['resources']['requests'].get('storage')}"
    
    # Check mount in deployment
    deploy = get_manifest_by_kind("Deployment", name=SERVICE_NAME)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "volumeMounts" in container, "Volume mounts not configured"
    prometheus_mount = next((m for m in container["volumeMounts"] if m["mountPath"] == "/prometheus"), None)
    assert prometheus_mount is not None, "No volume mounted to /prometheus path"
    
    volumes = deploy["spec"]["template"]["spec"]["volumes"]
    pvc_volume = next((v for v in volumes if v.get("persistentVolumeClaim", {}).get("claimName") == PVC_NAME), None)
    assert pvc_volume is not None, "PVC not referenced in deployment volumes"
    assert prometheus_mount["name"] == pvc_volume["name"], "Volume mount does not reference PVC volume"

def test_ac6_dedicated_service_account_used():
    """AC-6: Prometheus pod uses dedicated opentelemetry-demo-prometheus ServiceAccount"""
    deploy = get_manifest_by_kind("Deployment", name=SERVICE_NAME)
    sa = deploy["spec"]["template"]["spec"].get("serviceAccountName", None)
    assert sa is not None, "No serviceAccountName configured for pod"
    assert sa == SERVICE_ACCOUNT_NAME, f"Service account should be {SERVICE_ACCOUNT_NAME}, got {sa}"
    
    # Verify ServiceAccount manifest exists
    sa_manifest = get_manifest_by_kind("ServiceAccount", name=SERVICE_ACCOUNT_NAME)
    assert sa_manifest is not None, f"ServiceAccount {SERVICE_ACCOUNT_NAME} manifest not found"

def test_ac7_cluster_role_permissions():
    """AC-7: ClusterRole grants minimum required permissions for metric scraping"""
    cr = get_manifest_by_kind("ClusterRole", name=CLUSTER_ROLE_NAME)
    assert "rules" in cr, "ClusterRole has no rules configured"
    rules = cr["rules"]
    
    # Check first rule group
    rule1 = next((r for r in rules if "" in r["apiGroups"]), None)
    assert rule1 is not None, "No rule for core apiGroup"
    expected_resources1 = ["nodes", "nodes/metrics", "services", "endpoints", "pods", "configmaps"]
    for res in expected_resources1:
        assert res in rule1["resources"], f"Resource {res} missing from ClusterRole rules"
    assert set(rule1["verbs"]) == {"get", "list", "watch"}, f"Verbs for core resources should be get, list, watch, got {rule1['verbs']}"
    
    # Check second rule group
    rule2 = next((r for r in rules if "extensions" in r["apiGroups"] or "networking.k8s.io" in r["apiGroups"]), None)
    assert rule2 is not None, "No rule for extensions/networking.k8s.io apiGroups"
    assert "ingresses" in rule2["resources"], "Ingresses resource missing from ClusterRole rules"
    assert set(rule2["verbs"]) == {"get", "list", "watch"}, f"Verbs for ingresses should be get, list, watch, got {rule2['verbs']}"

def test_ac8_cluster_role_binding_configured():
    """AC-8: ClusterRoleBinding maps service account to ClusterRole with cluster-wide scope"""
    crb = get_manifest_by_kind("ClusterRoleBinding")
    assert crb is not None, "ClusterRoleBinding manifest not found"
    
    # Check roleRef
    assert crb["roleRef"]["kind"] == "ClusterRole", "roleRef should point to ClusterRole"
    assert crb["roleRef"]["name"] == CLUSTER_ROLE_NAME, f"roleRef should reference {CLUSTER_ROLE_NAME}"
    
    # Check subjects
    subjects = crb["subjects"]
    sa_subject = next((s for s in subjects if s["kind"] == "ServiceAccount" and s["name"] == SERVICE_ACCOUNT_NAME), None)
    assert sa_subject is not None, f"ServiceAccount {SERVICE_ACCOUNT_NAME} not found in ClusterRoleBinding subjects"

def test_ac9_kubectl_apply_succeeds():
    """AC-9: All manifests apply without errors on Kubernetes v1.24+"""
    # Check if dir exists first
    if not os.path.exists(PROMETHEUS_DIR):
        pytest.skip("Prometheus manifest directory not present")
    
    result = run_cmd(f"kubectl apply -f {PROMETHEUS_DIR} -n {TEST_NAMESPACE} --dry-run=client", check=False)
    assert result.returncode == 0, f"kubectl apply dry run failed: {result.stderr}"

def test_ac10_pod_runs_without_restarts():
    """AC-10: Prometheus pod enters Running state within 2 minutes, 0 restarts after 5 minutes"""
    if not os.path.exists(PROMETHEUS_DIR):
        pytest.skip("Prometheus manifest directory not present")
    
    try:
        # Apply manifests
        run_cmd(f"kubectl apply -f {PROMETHEUS_DIR} -n {TEST_NAMESPACE}")
        
        # Wait for pod to be running
        start_time = time.time()
        pod_running = False
        pod_name = ""
        while time.time() - start_time < 120:
            result = run_cmd(f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].status.phase}} {{.items[0].metadata.name}}'", check=False)
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2 and parts[0] == "Running":
                    pod_running = True
                    pod_name = parts[1]
                    break
            time.sleep(5)
        assert pod_running, "Pod did not enter Running state within 2 minutes"
        
        # Wait 5 minutes and check restarts
        time.sleep(300)
        result = run_cmd(f"kubectl get pod {pod_name} -n {TEST_NAMESPACE} -o jsonpath='{{.status.containerStatuses[0].restartCount}}'")
        restart_count = int(result.stdout.strip())
        assert restart_count == 0, f"Pod had {restart_count} restarts after 5 minutes, expected 0"
        
    finally:
        # Clean up
        run_cmd(f"kubectl delete -f {PROMETHEUS_DIR} -n {TEST_NAMESPACE}", check=False)
        run_cmd(f"kubectl delete pvc {PVC_NAME} -n {TEST_NAMESPACE}", check=False)

def test_ac11_service_health_endpoint_returns_200():
    """AC-11: Prometheus service returns HTTP 200 OK at http://prometheus:9090/-/healthy from within cluster"""
    if not os.path.exists(PROMETHEUS_DIR):
        pytest.skip("Prometheus manifest directory not present")
    
    try:
        # Apply manifests
        run_cmd(f"kubectl apply -f {PROMETHEUS_DIR} -n {TEST_NAMESPACE}")
        
        # Wait for pod to be ready
        time.sleep(60)
        
        # Run a test pod to access the service
        test_pod_name = "prometheus-test-temp"
        run_cmd(f"kubectl run {test_pod_name} --image=curlimages/curl:latest -n {TEST_NAMESPACE} --restart=Never --command -- sleep 300", check=True)
        time.sleep(10)
        
        # Test health endpoint
        result = run_cmd(f"kubectl exec {test_pod_name} -n {TEST_NAMESPACE} -- curl -s -o /dev/null -w '%{{http_code}}' http://{SERVICE_NAME}:9090/-/healthy", check=False)
        assert result.returncode == 0, "Curl command to prometheus service failed"
        assert result.stdout.strip() == "200", f"Expected HTTP 200 from health endpoint, got {result.stdout.strip()}"
        
    finally:
        # Clean up
        run_cmd(f"kubectl delete pod {test_pod_name} -n {TEST_NAMESPACE}", check=False)
        run_cmd(f"kubectl delete -f {PROMETHEUS_DIR} -n {TEST_NAMESPACE}", check=False)
        run_cmd(f"kubectl delete pvc {PVC_NAME} -n {TEST_NAMESPACE}", check=False)
