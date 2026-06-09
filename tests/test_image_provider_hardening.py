import pytest
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
import subprocess
import requests

DEPLOYMENT_PATH = "./k8s/image-provider-deployment.yaml"
NAMESPACE = "default"
DEPLOYMENT_NAME = "image-provider"
CONTAINER_NAME = "image-provider"

@pytest.fixture(scope="module")
def k8s_client():
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    return client.AppsV1Api(), client.CoreV1Api()

@pytest.fixture(scope="module")
def deployment_manifest():
    with open(DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

def test_ac1_cpu_memory_requests_limits(deployment_manifest):
    """AC-1: Verify explicit CPU/memory requests and limits are set correctly"""
    container = next(c for c in deployment_manifest["spec"]["template"]["spec"]["containers"] if c["name"] == CONTAINER_NAME)
    assert "resources" in container, "No resources field found in container spec"
    resources = container["resources"]
    
    assert "requests" in resources, "No resource requests defined"
    assert resources["requests"]["cpu"] == "10m", f"Expected cpu request 10m, got {resources['requests'].get('cpu')}"
    assert resources["requests"]["memory"] == "32Mi", f"Expected memory request 32Mi, got {resources['requests'].get('memory')}"
    
    assert "limits" in resources, "No resource limits defined"
    assert resources["limits"]["cpu"] == "100m", f"Expected cpu limit 100m, got {resources['limits'].get('cpu')}"
    assert resources["limits"]["memory"] == "64Mi", f"Expected memory limit 64Mi, got {resources['limits'].get('memory')}"

def test_ac2_security_context_non_root(deployment_manifest):
    """AC-2: Verify security context enforces non-root execution"""
    template_spec = deployment_manifest["spec"]["template"]["spec"]
    assert "securityContext" in template_spec, "No pod security context found"
    sc = template_spec["securityContext"]
    
    assert sc["runAsNonRoot"] == True, "runAsNonRoot should be true"
    assert sc["runAsUser"] == 101, f"Expected runAsUser 101, got {sc.get('runAsUser')}"
    assert sc["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation should be false"

def test_ac3_read_only_root_filesystem(deployment_manifest):
    """AC-3: Verify root filesystem is read-only"""
    container = next(c for c in deployment_manifest["spec"]["template"]["spec"]["containers"] if c["name"] == CONTAINER_NAME)
    assert "securityContext" in container, "No container security context found"
    sc = container["securityContext"]
    
    assert sc["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem should be true"

def test_ac4_drop_all_privileged_capabilities(deployment_manifest):
    """AC-4: Verify all privileged capabilities are dropped, none added"""
    container = next(c for c in deployment_manifest["spec"]["template"]["spec"]["containers"] if c["name"] == CONTAINER_NAME)
    assert "securityContext" in container, "No container security context found"
    sc = container["securityContext"]
    
    assert "capabilities" in sc, "No capabilities field in security context"
    assert "drop" in sc["capabilities"], "No capabilities dropped"
    assert "ALL" in sc["capabilities"]["drop"], "Should drop all capabilities"
    assert "add" not in sc["capabilities"] or len(sc["capabilities"]["add"]) == 0, "Should not add any capabilities"

def test_ac5_liveness_probe_configuration(deployment_manifest):
    """AC-5: Verify liveness probe is configured correctly on /health endpoint port 8080"""
    container = next(c for c in deployment_manifest["spec"]["template"]["spec"]["containers"] if c["name"] == CONTAINER_NAME)
    assert "livenessProbe" in container, "No liveness probe defined"
    lp = container["livenessProbe"]
    
    assert "httpGet" in lp, "Liveness probe should be HTTP GET"
    assert lp["httpGet"]["path"] == "/health", f"Expected liveness probe path /health, got {lp['httpGet'].get('path')}"
    assert lp["httpGet"]["port"] == 8080, f"Expected liveness probe port 8080, got {lp['httpGet'].get('port')}"
    assert lp["initialDelaySeconds"] == 5, f"Expected initialDelaySeconds 5, got {lp.get('initialDelaySeconds')}"
    assert lp["periodSeconds"] == 10, f"Expected periodSeconds 10, got {lp.get('periodSeconds')}"
    assert lp["failureThreshold"] == 3, f"Expected failureThreshold 3, got {lp.get('failureThreshold')}"

def test_ac6_readiness_probe_unchanged(deployment_manifest):
    """AC-6: Verify existing readiness probe remains configured with production values"""
    container = next(c for c in deployment_manifest["spec"]["template"]["spec"]["containers"] if c["name"] == CONTAINER_NAME)
    assert "readinessProbe" in container, "No readiness probe defined"
    rp = container["readinessProbe"]
    
    assert "httpGet" in rp, "Readiness probe should be HTTP GET"
    assert rp["httpGet"]["path"] == "/health", f"Expected readiness probe path /health, got {rp['httpGet'].get('path')}"
    assert rp["httpGet"]["port"] == 8080, f"Expected readiness probe port 8080, got {rp['httpGet'].get('port')}"
    assert rp["initialDelaySeconds"] == 2, f"Expected initialDelaySeconds 2, got {rp.get('initialDelaySeconds')}"
    assert rp["periodSeconds"] == 5, f"Expected periodSeconds 5, got {rp.get('periodSeconds')}"
    assert rp["failureThreshold"] == 2, f"Expected failureThreshold 2, got {rp.get('failureThreshold')}"

@pytest.mark.cluster
def test_ac7_deployment_succeeds(k8s_client):
    """AC-7: Verify deployment applies successfully and pod reaches Running/Ready state within 60s"""
    apps_v1, core_v1 = k8s_client
    
    # Apply deployment
    subprocess.run(["kubectl", "apply", "-f", DEPLOYMENT_PATH, "-n", NAMESPACE], check=True)
    
    # Wait for deployment to be available
    for _ in range(60):
        try:
            deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
            if deploy.status.available_replicas and deploy.status.available_replicas >= 1:
                break
        except ApiException:
            pass
        time.sleep(1)
    else:
        pytest.fail("Deployment did not become available within 60 seconds")
    
    # Check pod is running and ready
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No pods found for deployment"
    pod = pods.items[0]
    assert pod.status.phase == "Running", f"Pod not in Running phase: {pod.status.phase}"
    ready_condition = next(c for c in pod.status.conditions if c.type == "Ready")
    assert ready_condition.status == "True", "Pod is not in Ready state"

@pytest.mark.cluster
def test_ac8_processes_run_as_non_root(k8s_client):
    """AC-8: Verify nginx processes run as UID 101, not root (UID 0)"""
    _, core_v1 = k8s_client
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    pod_name = pods.items[0].metadata.name
    
    # Get UID of nginx processes
    result = subprocess.run(
        ["kubectl", "exec", "-n", NAMESPACE, pod_name, "-c", CONTAINER_NAME, "--", "ps", "-o", "uid,cmd"],
        capture_output=True, text=True, check=True
    )
    process_lines = result.stdout.strip().split("\n")[1:]
    nginx_lines = [line for line in process_lines if "nginx" in line]
    
    assert len(nginx_lines) > 0, "No nginx processes found running"
    for line in nginx_lines:
        uid = line.strip().split()[0]
        assert uid == "101", f"Found nginx process running as UID {uid}, expected 101"
        assert uid != "0", "Found nginx process running as root (UID 0)"

@pytest.mark.cluster
def test_ac9_liveness_probe_triggers_restart(k8s_client):
    """AC-9: Verify liveness probe detects unresponsive Nginx and triggers restart within 30s"""
    apps_v1, core_v1 = k8s_client
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    pod_name = pods.items[0].metadata.name
    initial_restart_count = next(c.restart_count for c in pods.items[0].status.container_statuses if c.name == CONTAINER_NAME)
    
    # Kill nginx process to make it unresponsive
    try:
        subprocess.run(
            ["kubectl", "exec", "-n", NAMESPACE, pod_name, "-c", CONTAINER_NAME, "--", "pkill", "-SIGKILL", "nginx"],
            check=True
        )
    except subprocess.CalledProcessError:
        # Process already killed, ignore
        pass
    
    # Check for restart within 30 seconds
    restarted = False
    for _ in range(30):
        try:
            pod = core_v1.read_namespaced_pod(pod_name, NAMESPACE)
            current_restart_count = next(c.restart_count for c in pod.status.container_statuses if c.name == CONTAINER_NAME)
            if current_restart_count > initial_restart_count:
                restarted = True
                break
        except:
            pass
        time.sleep(1)
    
    assert restarted, "Pod was not restarted after nginx process was killed, liveness probe failed to detect failure"

@pytest.mark.cluster
def test_ac10_health_endpoint_returns_200(k8s_client):
    """AC-10: Verify /health endpoint returns 200 OK"""
    _, core_v1 = k8s_client
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    pod_ip = pods.items[0].status.pod_ip
    
    response = requests.get(f"http://{pod_ip}:8080/health", timeout=5)
    assert response.status_code == 200, f"Expected 200 OK from /health endpoint, got {response.status_code}"
