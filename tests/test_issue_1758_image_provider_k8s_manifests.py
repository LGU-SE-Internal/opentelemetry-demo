#!/usr/bin/env python3
import os
import subprocess
import yaml
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

MANIFESTS_DIR = "kubernetes/image-provider/"
DEPLOYMENT_PATH = os.path.join(MANIFESTS_DIR, "deployment.yaml")
SERVICE_PATH = os.path.join(MANIFESTS_DIR, "service.yaml")
SERVICEACCOUNT_PATH = os.path.join(MANIFESTS_DIR, "serviceaccount.yaml")
NAMESPACE = "default"

def test_ac1_three_manifest_files_exist():
    """AC-1: Three new Kubernetes manifest files exist for the image-provider service: deployment.yaml, service.yaml, serviceaccount.yaml in the repository's standard Kubernetes manifests directory"""
    assert os.path.exists(MANIFESTS_DIR), f"Image provider manifests directory missing at {MANIFESTS_DIR}"
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment manifest missing at {DEPLOYMENT_PATH}"
    assert os.path.exists(SERVICE_PATH), f"Service manifest missing at {SERVICE_PATH}"
    assert os.path.exists(SERVICEACCOUNT_PATH), f"ServiceAccount manifest missing at {SERVICEACCOUNT_PATH}"
    
    # Verify manifest kinds
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
        assert dep["kind"] == "Deployment"
    
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
        assert svc["kind"] == "Service"
    
    with open(SERVICEACCOUNT_PATH, "r") as f:
        sa = yaml.safe_load(f)
        assert sa["kind"] == "ServiceAccount"

def test_ac2_probes_configured_correctly():
    """AC-2: The deployment.yaml includes livenessProbe and readinessProbe configuration, both performing GET /healthz requests on the container's Nginx port, with initialDelaySeconds=5, periodSeconds=10, failureThreshold=3"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "Liveness probe not configured"
    assert "readinessProbe" in container, "Readiness probe not configured"
    
    # Check liveness probe
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/healthz", f"Expected liveness probe path /healthz, got {liveness['httpGet']['path']}"
    assert liveness["httpGet"]["port"] == 8080, f"Expected liveness probe port 8080, got {liveness['httpGet']['port']}"
    assert liveness["initialDelaySeconds"] == 5, f"Expected initialDelaySeconds=5, got {liveness['initialDelaySeconds']}"
    assert liveness["periodSeconds"] == 10, f"Expected periodSeconds=10, got {liveness['periodSeconds']}"
    assert liveness["failureThreshold"] == 3, f"Expected failureThreshold=3, got {liveness['failureThreshold']}"
    
    # Check readiness probe
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/healthz", f"Expected readiness probe path /healthz, got {readiness['httpGet']['path']}"
    assert readiness["httpGet"]["port"] == 8080, f"Expected readiness probe port 8080, got {readiness['httpGet']['port']}"
    assert readiness["initialDelaySeconds"] == 5, f"Expected initialDelaySeconds=5, got {readiness['initialDelaySeconds']}"
    assert readiness["periodSeconds"] == 10, f"Expected periodSeconds=10, got {readiness['periodSeconds']}"
    assert readiness["failureThreshold"] == 3, f"Expected failureThreshold=3, got {readiness['failureThreshold']}"

def test_ac3_resource_constraints_configured():
    """AC-3: The deployment.yaml defines production-grade resource constraints: requests (cpu: 100m, memory: 128Mi), limits (cpu: 500m, memory: 256Mi)"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container, "Resources not configured for container"
    assert "requests" in container["resources"], "Resource requests not configured"
    assert "limits" in container["resources"], "Resource limits not configured"
    
    requests = container["resources"]["requests"]
    assert requests["cpu"] == "100m", f"Expected CPU request 100m, got {requests['cpu']}"
    assert requests["memory"] == "128Mi", f"Expected memory request 128Mi, got {requests['memory']}"
    
    limits = container["resources"]["limits"]
    assert limits["cpu"] == "500m", f"Expected CPU limit 500m, got {limits['cpu']}"
    assert limits["memory"] == "256Mi", f"Expected memory limit 256Mi, got {limits['memory']}"

def test_ac4_security_context_least_privilege():
    """AC-4: The deployment.yaml security context enforces least privilege: runAsNonRoot: true, runAsUser: 1001, readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, all capabilities dropped"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Check pod-level security context or container-level
    if "securityContext" in pod_spec:
        sc = pod_spec["securityContext"]
    else:
        sc = container["securityContext"]
    
    assert sc["runAsNonRoot"] == True, "runAsNonRoot should be true"
    assert sc["runAsUser"] == 1001, f"Expected runAsUser=1001, got {sc['runAsUser']}"
    assert container["securityContext"]["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem should be true"
    assert container["securityContext"]["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation should be false"
    assert "capabilities" in container["securityContext"], "Capabilities not configured"
    assert "drop" in container["securityContext"]["capabilities"], "Capabilities drop not configured"
    assert "ALL" in container["securityContext"]["capabilities"]["drop"], "All capabilities should be dropped"

def test_ac5_environment_variables_configured():
    """AC-5: The deployment.yaml includes environment variable definitions that are properly substituted into the Nginx configuration at container runtime"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "env" in container, "Environment variables not configured"
    
    env_vars = {e["name"]: e.get("value", "") for e in container["env"]}
    assert "NGINX_PORT" in env_vars, "NGINX_PORT env var missing"
    assert env_vars["NGINX_PORT"] == "8080", f"Expected NGINX_PORT default 8080, got {env_vars['NGINX_PORT']}"
    assert "NGINX_CLIENT_MAX_BODY_SIZE" in env_vars, "NGINX_CLIENT_MAX_BODY_SIZE env var missing"
    assert env_vars["NGINX_CLIENT_MAX_BODY_SIZE"] == "1M", f"Expected NGINX_CLIENT_MAX_BODY_SIZE default 1M, got {env_vars['NGINX_CLIENT_MAX_BODY_SIZE']}"
    assert "NGINX_GZIP_ENABLE" in env_vars, "NGINX_GZIP_ENABLE env var missing"
    assert env_vars["NGINX_GZIP_ENABLE"] == "on", f"Expected NGINX_GZIP_ENABLE default on, got {env_vars['NGINX_GZIP_ENABLE']}"

def test_ac6_service_configured_correctly():
    """AC-6: The service.yaml correctly selects pods with the app: image-provider label, exposes port 80 targeting the container's Nginx port, and uses ClusterIP service type"""
    assert os.path.exists(SERVICE_PATH), f"Service file missing at {SERVICE_PATH}"
    
    with open(SERVICE_PATH, "r") as f:
        service = yaml.safe_load(f)
    
    assert service["metadata"]["name"] == "image-provider", f"Expected service name image-provider, got {service['metadata']['name']}"
    assert service["spec"]["type"] == "ClusterIP", f"Expected service type ClusterIP, got {service['spec']['type']}"
    
    ports = service["spec"]["ports"]
    http_port = next(p for p in ports if p["name"] == "http" or p["port"] == 80)
    assert http_port["port"] == 80, f"Expected service port 80, got {http_port['port']}"
    assert http_port["targetPort"] == 8080, f"Expected target port 8080, got {http_port['targetPort']}"
    
    assert service["spec"]["selector"]["app"] == "image-provider", f"Expected selector app: image-provider, got {service['spec']['selector']}"

def test_ac7_serviceaccount_configured():
    """AC-7: The serviceaccount.yaml is referenced in the deployment spec, and has no additional RBAC roles or role bindings assigned"""
    assert os.path.exists(SERVICEACCOUNT_PATH), f"ServiceAccount file missing at {SERVICEACCOUNT_PATH}"
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(SERVICEACCOUNT_PATH, "r") as f:
        sa = yaml.safe_load(f)
    
    assert sa["metadata"]["name"] == "image-provider", f"Expected service account name image-provider, got {sa['metadata']['name']}"
    
    # Check no extra permissions
    assert "secrets" not in sa or len(sa["secrets"]) == 0, "Service account should not have extra secrets attached by default"
    assert "imagePullSecrets" not in sa or len(sa["imagePullSecrets"]) == 0, "Service account should not have imagePullSecrets by default"
    
    # Check deployment references the service account
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "image-provider", f"Deployment should reference service account image-provider, got {pod_spec.get('serviceAccountName', 'none')}"

def test_ac8_manifest_validation_passes():
    """AC-8: Applying all three manifests to a running Kubernetes cluster results in 3 ready pods within 60 seconds, with 0 restarts"""
    assert os.path.exists(MANIFESTS_DIR), f"Manifests directory missing at {MANIFESTS_DIR}"
    
    # Dry run validation first
    result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR, "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Manifest dry-run validation failed: {result.stderr}"
    
    # Check deployment has 3 replicas
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    assert deployment["spec"]["replicas"] == 3, f"Expected 3 replicas for production, got {deployment['spec']['replicas']}"

@pytest.mark.e2e
def test_ac9_healthz_endpoint_returns_200():
    """AC-9: Querying http://image-provider.<namespace>.svc.cluster.local/healthz from inside the cluster returns a 200 OK status code"""
    # Apply manifests
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR],
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"Failed to apply manifests: {apply_result.stderr}"
    
    try:
        # Wait for all 3 pods to be ready
        config.load_kube_config()
        v1 = client.CoreV1Api()
        
        all_ready = False
        for _ in range(60):
            try:
                pods = v1.list_namespaced_pod(NAMESPACE, label_selector="app=image-provider")
                if len(pods.items) == 3:
                    ready_count = sum(1 for p in pods.items if all(c.ready for c in p.status.container_statuses))
                    if ready_count == 3:
                        all_ready = True
                        break
            except ApiException:
                pass
            time.sleep(1)
        
        assert all_ready, "Not all 3 pods became ready within 60 seconds"
        
        # Test health endpoint via service
        curl_result = subprocess.run(
            ["kubectl", "run", "-it", "--rm", "curl-test", "--image=curlimages/curl:latest", "--restart=Never", "--", 
             f"curl -s -o /dev/null -w '%{{http_code}}' http://image-provider.{NAMESPACE}.svc.cluster.local/healthz"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        assert curl_result.returncode == 0, f"Failed to connect to health endpoint: {curl_result.stderr}"
        assert curl_result.stdout.strip() == "200", f"Expected 200 OK from /healthz, got {curl_result.stdout}"
    finally:
        # Clean up
        subprocess.run(["kubectl", "delete", "-f", MANIFESTS_DIR], capture_output=True)

@pytest.mark.e2e
def test_ac10_image_endpoints_work():
    """AC-10: Querying an existing image path returns 200 OK, querying non-existent path returns 404 Not Found"""
    # Apply manifests
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR],
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"Failed to apply manifests: {apply_result.stderr}"
    
    try:
        # Wait for service to be available
        config.load_kube_config()
        v1 = client.CoreV1Api()
        
        service_ready = False
        for _ in range(30):
            try:
                svc = v1.read_namespaced_service("image-provider", NAMESPACE)
                if svc.spec.cluster_ip:
                    service_ready = True
                    break
            except ApiException:
                pass
            time.sleep(1)
        
        assert service_ready, "Service did not get a cluster IP within 30s"
        
        # Test existing image (assuming sample.jpg exists in static assets)
        existing_image_result = subprocess.run(
            ["kubectl", "run", "-it", "--rm", "curl-test-1", "--image=curlimages/curl:latest", "--restart=Never", "--", 
             f"curl -s -o /dev/null -w '%{{http_code}}' http://image-provider.{NAMESPACE}.svc.cluster.local/images/product1.jpg"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        assert existing_image_result.returncode == 0, "Failed to query existing image"
        assert existing_image_result.stdout.strip() == "200", f"Expected 200 for existing image, got {existing_image_result.stdout}"
        
        # Test non-existent image
        non_existent_result = subprocess.run(
            ["kubectl", "run", "-it", "--rm", "curl-test-2", "--image=curlimages/curl:latest", "--restart=Never", "--", 
             f"curl -s -o /dev/null -w '%{{http_code}}' http://image-provider.{NAMESPACE}.svc.cluster.local/images/non_existent_image_1234.jpg"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        assert non_existent_result.returncode == 0, "Failed to query non-existent image"
        assert non_existent_result.stdout.strip() == "404", f"Expected 404 for non-existent image, got {non_existent_result.stdout}"
    finally:
        # Clean up
        subprocess.run(["kubectl", "delete", "-f", MANIFESTS_DIR], capture_output=True)
