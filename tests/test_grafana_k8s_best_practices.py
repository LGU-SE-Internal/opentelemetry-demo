import yaml
import subprocess
import pytest
import time
import requests

DEPLOYMENT_FILE = "./deploy/kubernetes/grafana-deployment.yaml"
DEPLOYMENT_NAME = "grafana"
NAMESPACE = "default"  # Adjust if needed based on actual deployment namespace
PVC_NAME = "grafana-storage"


def load_all_manifest_objects():
    with open(DEPLOYMENT_FILE, "r") as f:
        return list(yaml.safe_load_all(f))


def get_deployment_object(manifest_objs):
    for obj in manifest_objs:
        if obj["kind"] == "Deployment" and obj["metadata"]["name"] == DEPLOYMENT_NAME:
            return obj
    pytest.fail(f"Deployment {DEPLOYMENT_NAME} not found in manifest")


def get_pvc_object(manifest_objs):
    for obj in manifest_objs:
        if obj["kind"] == "PersistentVolumeClaim" and obj["metadata"]["name"] == PVC_NAME:
            return obj
    pytest.fail(f"PVC {PVC_NAME} not found in manifest")


def get_running_deployment():
    result = subprocess.run(
        ["kubectl", "get", "deployment", DEPLOYMENT_NAME, "-n", NAMESPACE, "-o", "yaml"],
        capture_output=True,
        text=True,
        check=True
    )
    return yaml.safe_load(result.stdout)


def get_running_pods():
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE, "-l", f"app={DEPLOYMENT_NAME}", "-o", "yaml"],
        capture_output=True,
        text=True,
        check=True
    )
    return yaml.safe_load(result.stdout)["items"]


def test_ac1_manifest_validates_with_kubectl_dryrun():
    """AC-1: Valid Kubernetes manifest exists at correct path and passes kubectl dry run validation"""
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-f", DEPLOYMENT_FILE],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Manifest dry run validation failed: {result.stderr}"


def test_ac2_liveness_readiness_probes_configured():
    """AC-2: Liveness and readiness probes point to /api/health on port 3000 with correct timing values"""
    manifest_objs = load_all_manifest_objects()
    deploy = get_deployment_object(manifest_objs)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    # Check liveness probe
    assert "livenessProbe" in container, "Liveness probe missing"
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/api/health", "Incorrect liveness probe path"
    assert liveness["httpGet"]["port"] == 3000, "Incorrect liveness probe port"
    assert liveness["initialDelaySeconds"] == 30, "Incorrect liveness initial delay"
    assert liveness["periodSeconds"] == 10, "Incorrect liveness period"
    assert liveness["timeoutSeconds"] == 5, "Incorrect liveness timeout"
    assert liveness["failureThreshold"] == 3, "Incorrect liveness failure threshold"
    
    # Check readiness probe
    assert "readinessProbe" in container, "Readiness probe missing"
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/api/health", "Incorrect readiness probe path"
    assert readiness["httpGet"]["port"] == 3000, "Incorrect readiness probe port"
    assert readiness["initialDelaySeconds"] == 30, "Incorrect readiness initial delay"
    assert readiness["periodSeconds"] == 10, "Incorrect readiness period"
    assert readiness["timeoutSeconds"] == 5, "Incorrect readiness timeout"
    assert readiness["failureThreshold"] == 3, "Incorrect readiness failure threshold"


def test_ac3_resource_requests_limits_configured():
    """AC-3: CPU/memory resource requests and limits set to required demo workload values"""
    manifest_objs = load_all_manifest_objects()
    deploy = get_deployment_object(manifest_objs)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "resources" in container, "Resources section missing"
    resources = container["resources"]
    
    assert "requests" in resources, "Resource requests missing"
    assert resources["requests"]["cpu"] == "100m", "CPU request incorrect"
    assert resources["requests"]["memory"] == "256Mi", "Memory request incorrect"
    
    assert "limits" in resources, "Resource limits missing"
    assert resources["limits"]["cpu"] == "500m", "CPU limit incorrect"
    assert resources["limits"]["memory"] == "1Gi", "Memory limit incorrect"


def test_ac4_security_context_configured():
    """AC-4: Non-root security context configured with correct UID and privilege restrictions"""
    manifest_objs = load_all_manifest_objects()
    deploy = get_deployment_object(manifest_objs)
    pod_spec = deploy["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Check container security context first, fall back to pod-level
    sec_ctx = container.get("securityContext", pod_spec.get("securityContext", {}))
    
    assert sec_ctx.get("runAsNonRoot") == True, "runAsNonRoot must be true"
    assert sec_ctx.get("runAsUser") == 472, "runAsUser must be 472 (Grafana standard UID)"
    assert sec_ctx.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation must be false"


def test_ac5_pvc_configured_and_mounted():
    """AC-5: PersistentVolumeClaim exists with correct specs and is mounted to /var/lib/grafana"""
    manifest_objs = load_all_manifest_objects()
    deploy = get_deployment_object(manifest_objs)
    pvc = get_pvc_object(manifest_objs)
    
    # Check PVC specs
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC access mode must include ReadWriteOnce"
    storage_request = pvc["spec"]["resources"]["requests"]["storage"]
    # Convert storage to Gi for comparison
    if storage_request.endswith("Gi"):
        storage_val = float(storage_request[:-2])
    elif storage_request.endswith("Mi"):
        storage_val = float(storage_request[:-2]) / 1024
    else:
        pytest.fail(f"Unsupported storage unit: {storage_request}")
    assert storage_val >= 1.0, "PVC storage request must be at least 1Gi"
    
    # Check PVC is mounted in container
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    pvc_mount_found = False
    for mount in volume_mounts:
        if mount["mountPath"] == "/var/lib/grafana":
            pvc_mount_found = True
            # Check volume exists and references the PVC
            volumes = deploy["spec"]["template"]["spec"].get("volumes", [])
            volume_found = False
            for vol in volumes:
                if vol["name"] == mount["name"]:
                    assert vol["persistentVolumeClaim"]["claimName"] == PVC_NAME, f"Volume must reference PVC {PVC_NAME}"
                    volume_found = True
                    break
            assert volume_found, f"Volume {mount['name']} not found in deployment spec"
            break
    assert pvc_mount_found, "PVC not mounted to /var/lib/grafana path"


def test_ac6_grafana_starts_successfully_and_serves_ui():
    """AC-6: Grafana pod starts, passes probes, and serves UI on port 3000 within 60 seconds"""
    # Trigger rollout restart to test fresh startup
    subprocess.run(
        ["kubectl", "rollout", "restart", f"deployment/{DEPLOYMENT_NAME}", "-n", NAMESPACE],
        check=True, capture_output=True, text=True
    )
    
    # Wait for rollout to complete (max 60s per AC)
    result = subprocess.run(
        ["kubectl", "rollout", "status", f"deployment/{DEPLOYMENT_NAME}", "-n", NAMESPACE, "--timeout=60s"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Rollout did not complete within 60 seconds: {result.stderr}"
    
    pods = get_running_pods()
    assert len(pods) > 0, "No running Grafana pods found after rollout"
    
    for pod in pods:
        assert pod["status"]["phase"] == "Running", "Pod not in Running phase"
        # Check all containers are ready
        for container_status in pod["status"]["containerStatuses"]:
            assert container_status["ready"] == True, "Container not ready"
            assert container_status["started"] == True, "Container not started"
    
    # Test UI/health endpoint accessibility
    pod_name = pods[0]["metadata"]["name"]
    
    # Port forward to pod
    port_forward = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod_name}", "3000:3000", "-n", NAMESPACE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(3)  # Wait for port forward to establish
    
    try:
        # Test health endpoint returns expected healthy response
        resp = requests.get("http://localhost:3000/api/health", timeout=10)
        assert resp.status_code == 200, f"Health endpoint returned {resp.status_code}"
        assert resp.json().get("status") == "ok", "Health endpoint status is not ok"
        
        # Test main UI page returns 200
        ui_resp = requests.get("http://localhost:3000/login", timeout=10)
        assert ui_resp.status_code == 200, "Grafana UI login page not accessible"
    finally:
        port_forward.terminate()
        port_forward.wait()
