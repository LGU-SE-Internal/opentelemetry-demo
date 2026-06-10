#!/usr/bin/env python3
import os
import subprocess
import yaml
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "kubernetes/ad-service-deployment.yaml"
SERVICE_PATH = "kubernetes/ad-service-service.yaml"
MANIFEST_PATHS = [DEPLOYMENT_PATH, SERVICE_PATH]

def test_ac1_deployment_exists_with_resources():
    """AC-1: Deployment manifest exists with valid schema and specified resource requests/limits"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "ad-service"
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "ad-service"
    assert "resources" in container
    assert "requests" in container["resources"]
    assert "cpu" in container["resources"]["requests"]
    assert "memory" in container["resources"]["requests"]
    assert "limits" in container["resources"]
    assert "cpu" in container["resources"]["limits"]
    assert "memory" in container["resources"]["limits"]
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert container["resources"]["requests"]["memory"] == "256Mi"
    assert container["resources"]["limits"]["cpu"] == "500m"
    assert container["resources"]["limits"]["memory"] == "512Mi"

def test_ac2_liveness_probe_configured():
    """AC-2: Deployment includes correctly configured livenessProbe"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container
    
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/q/health/live"
    assert liveness["httpGet"]["port"] == 8080
    assert liveness["initialDelaySeconds"] == 10
    assert liveness["periodSeconds"] == 30
    assert liveness["timeoutSeconds"] == 5

def test_ac3_readiness_probe_configured():
    """AC-3: Deployment includes correctly configured readinessProbe"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "readinessProbe" in container
    
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/q/health/ready"
    assert readiness["httpGet"]["port"] == 8080
    assert readiness["initialDelaySeconds"] == 5
    assert readiness["periodSeconds"] == 10
    assert readiness["timeoutSeconds"] == 3

def test_ac4_security_context_configured():
    """AC-4: Security context applied at pod and container levels, with /tmp emptyDir mount"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    pod_spec = deployment["spec"]["template"]["spec"]
    assert "securityContext" in pod_spec
    pod_sc = pod_spec["securityContext"]
    assert pod_sc["runAsNonRoot"] == True
    assert pod_sc["runAsUser"] == 10001
    
    container = pod_spec["containers"][0]
    assert "securityContext" in container
    container_sc = container["securityContext"]
    assert container_sc["readOnlyRootFilesystem"] == True
    assert container_sc["allowPrivilegeEscalation"] == False
    assert "drop" in container_sc["capabilities"]
    assert "ALL" in container_sc["capabilities"]["drop"]
    
    # Check emptyDir mount at /tmp
    volumes = pod_spec.get("volumes", [])
    tmp_volume = next((v for v in volumes if v["name"] == "tmp"), None)
    assert tmp_volume is not None, "tmp volume not found"
    assert "emptyDir" in tmp_volume
    
    volume_mounts = container.get("volumeMounts", [])
    tmp_mount = next((m for m in volume_mounts if m["name"] == "tmp"), None)
    assert tmp_mount is not None, "/tmp mount not found"
    assert tmp_mount["mountPath"] == "/tmp"

def test_ac5_environment_variables_present():
    """AC-5: All documented environment variables present with correct default values"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_vars = {e["name"]: e.get("value", "") for e in container.get("env", [])}
    
    assert "GRACEFUL_SHUTDOWN_TIMEOUT" in env_vars
    assert env_vars["GRACEFUL_SHUTDOWN_TIMEOUT"] == "30s"
    assert "GRPC_TLS_ENABLED" in env_vars
    assert env_vars["GRPC_TLS_ENABLED"] == "false"
    assert "GRPC_TLS_CERT_PATH" in env_vars
    assert env_vars["GRPC_TLS_CERT_PATH"] == ""
    assert "GRPC_TLS_KEY_PATH" in env_vars
    assert env_vars["GRPC_TLS_KEY_PATH"] == ""
    assert "HEALTH_ENDPOINT_PORT" in env_vars
    assert env_vars["HEALTH_ENDPOINT_PORT"] == "8080"

def test_ac6_service_exists_with_correct_spec():
    """AC-6: Service manifest exists as ClusterIP exposing port 9555 with matching selector"""
    assert os.path.exists(SERVICE_PATH), f"Service file missing at {SERVICE_PATH}"
    
    with open(SERVICE_PATH, "r") as f:
        service = yaml.safe_load(f)
    
    assert service["apiVersion"] == "v1"
    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "ad-service"
    assert service["spec"]["type"] == "ClusterIP"
    
    ports = service["spec"]["ports"]
    grpc_port = next(p for p in ports if p["name"] == "grpc")
    assert grpc_port["port"] == 9555
    assert grpc_port["targetPort"] == 9555
    
    # Check selector matches pod labels from deployment
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]
    for k, v in service["spec"]["selector"].items():
        assert k in pod_labels
        assert pod_labels[k] == v, f"Selector {k}={v} does not match pod label {pod_labels.get(k)}"

def test_ac7_kubectl_validate_passes():
    """AC-7: kubectl validate passes for both manifests"""
    result = subprocess.run(
        ["kubectl", "validate", "-f"] + MANIFEST_PATHS,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl validate failed: {result.stderr}"

@pytest.mark.e2e
def test_ac8_pods_run_and_service_reachable():
    """AC-8: Pods reach Running status with 0 restarts, service reachable via gRPC"""
    # Apply manifests
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f"] + MANIFEST_PATHS,
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"Failed to apply manifests: {apply_result.stderr}"
    
    # Wait for pods to be running and ready
    config.load_kube_config()
    v1 = client.CoreV1Api()
    namespace = "default"
    
    pods_running = False
    for _ in range(60):
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=ad-service")
            if len(pods.items) >= 2:  # Replica count default is 2
                all_running = all(p.status.phase == "Running" for p in pods.items)
                all_ready = all(all(c.ready for c in p.status.container_statuses) for p in pods.items)
                no_restarts = all(c.restart_count == 0 for p in pods.items for c in p.status.container_statuses)
                if all_running and all_ready and no_restarts:
                    pods_running = True
                    break
        except ApiException as e:
            pass
        time.sleep(1)
    
    assert pods_running, "Pods did not reach Running state with 0 restarts within 60s"
    
    # Test gRPC connectivity to service
    service = v1.read_namespaced_service("ad-service", namespace)
    service_ip = service.spec.cluster_ip
    
    # Use grpcurl to test connection
    grpcurl_result = subprocess.run(
        ["kubectl", "run", "-it", "--rm", "grpc-test", "--image=fullstorydev/grpcurl:latest", "--restart=Never", "--", f"-plaintext {service_ip}:9555 list"],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Clean up manifests
    subprocess.run(["kubectl", "delete", "-f"] + MANIFEST_PATHS, capture_output=True)
    
    assert grpcurl_result.returncode == 0, f"Failed to connect to gRPC service: {grpcurl_result.stderr}"

@pytest.mark.e2e
def test_ac9_unhealthy_pod_removed_from_endpoints():
    """AC-9: Unhealthy pod is removed from service endpoint list when health check fails"""
    # This test is skipped until implementation exists
    pytest.skip("Requires implementation to test health check failure scenario")
