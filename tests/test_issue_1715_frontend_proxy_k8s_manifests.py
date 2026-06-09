import os
import subprocess
import yaml
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Constants from spec
CONFIGMAP_NAME = "frontend-proxy-nginx-config"
DEPLOYMENT_NAME = "frontend-proxy"
SERVICE_NAME = "frontend-proxy"
NGINX_IMAGE = "nginxinc/nginx-unprivileged:stable-alpine"
EXPECTED_CPU_REQUEST = "100m"
EXPECTED_CPU_LIMIT = "200m"
EXPECTED_MEM_REQUEST = "128Mi"
EXPECTED_MEM_LIMIT = "256Mi"
EXPECTED_CONTAINER_PORT_HTTP = 8080
EXPECTED_CONTAINER_PORT_HTTPS = 8443
EXPECTED_SERVICE_PORT_HTTP = 80
EXPECTED_SERVICE_PORT_HTTPS = 443
EXPECTED_RUN_AS_USER = 101

MANIFEST_DIR = os.path.join(os.path.dirname(__file__), "..", "k8s")

def get_manifest_path(filename):
    return os.path.join(MANIFEST_DIR, filename)

@pytest.fixture(scope="module")
def k8s_client():
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    return client.CoreV1Api(), client.AppsV1Api()

@pytest.fixture(scope="module")
def all_manifests():
    manifests = {}
    for fname in os.listdir(MANIFEST_DIR):
        if fname.endswith(".yaml") and "frontend-proxy" in fname:
            with open(get_manifest_path(fname), "r") as f:
                docs = list(yaml.safe_load_all(f))
                for doc in docs:
                    if doc and "kind" in doc:
                        manifests[doc["kind"]] = doc
    return manifests

# AC-1 Tests
def test_ac1_configmap_exists(all_manifests):
    assert "ConfigMap" in all_manifests, "frontend-proxy ConfigMap manifest not found"
    cm = all_manifests["ConfigMap"]
    assert cm["metadata"]["name"] == CONFIGMAP_NAME, f"ConfigMap name should be {CONFIGMAP_NAME}"
    assert "nginx.conf" in cm["data"], "nginx.conf key missing in ConfigMap data"

def test_ac1_configmap_valid_nginx(all_manifests):
    if "ConfigMap" not in all_manifests:
        pytest.skip("ConfigMap not present")
    cm = all_manifests["ConfigMap"]
    nginx_conf = cm["data"]["nginx.conf"]
    assert len(nginx_conf) > 0, "nginx.conf content is empty"
    # Verify it listens on non-root ports as per assumption
    assert "listen 8080" in nginx_conf or "listen *:8080" in nginx_conf, "Nginx config must listen on port 8080"

# AC-2 Tests
def test_ac2_deployment_exists(all_manifests):
    assert "Deployment" in all_manifests, "frontend-proxy Deployment manifest not found"
    dep = all_manifests["Deployment"]
    assert dep["metadata"]["name"] == DEPLOYMENT_NAME, f"Deployment name should be {DEPLOYMENT_NAME}"
    assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == NGINX_IMAGE, f"Deployment uses wrong image, expected {NGINX_IMAGE}"

def test_ac2_deployment_resource_limits(all_manifests):
    if "Deployment" not in all_manifests:
        pytest.skip("Deployment not present")
    dep = all_manifests["Deployment"]
    resources = dep["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources["requests"]["cpu"] == EXPECTED_CPU_REQUEST, f"CPU request should be {EXPECTED_CPU_REQUEST}"
    assert resources["limits"]["cpu"] == EXPECTED_CPU_LIMIT, f"CPU limit should be {EXPECTED_CPU_LIMIT}"
    assert resources["requests"]["memory"] == EXPECTED_MEM_REQUEST, f"Memory request should be {EXPECTED_MEM_REQUEST}"
    assert resources["limits"]["memory"] == EXPECTED_MEM_LIMIT, f"Memory limit should be {EXPECTED_MEM_LIMIT}"

def test_ac2_deployment_probes(all_manifests):
    if "Deployment" not in all_manifests:
        pytest.skip("Deployment not present")
    dep = all_manifests["Deployment"]
    container = dep["spec"]["template"]["spec"]["containers"][0]
    # Liveness probe checks
    assert "livenessProbe" in container, "Liveness probe missing"
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/healthz", "Liveness probe path should be /healthz"
    assert liveness["httpGet"]["port"] == EXPECTED_CONTAINER_PORT_HTTP, "Liveness probe port should be 8080"
    assert liveness["initialDelaySeconds"] == 5, "Liveness initialDelaySeconds should be 5"
    assert liveness["periodSeconds"] == 10, "Liveness periodSeconds should be 10"
    assert liveness["failureThreshold"] == 3, "Liveness failureThreshold should be 3"
    # Readiness probe checks
    assert "readinessProbe" in container, "Readiness probe missing"
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/healthz", "Readiness probe path should be /healthz"
    assert readiness["httpGet"]["port"] == EXPECTED_CONTAINER_PORT_HTTP, "Readiness probe port should be 8080"
    assert readiness["initialDelaySeconds"] == 2, "Readiness initialDelaySeconds should be 2"
    assert readiness["periodSeconds"] == 5, "Readiness periodSeconds should be 5"
    assert readiness["failureThreshold"] == 2, "Readiness failureThreshold should be 2"

def test_ac2_deployment_security_context(all_manifests):
    if "Deployment" not in all_manifests:
        pytest.skip("Deployment not present")
    dep = all_manifests["Deployment"]
    sc = dep["spec"]["template"]["spec"]["securityContext"]
    assert sc["runAsNonRoot"] == True, "runAsNonRoot must be true"
    assert sc["runAsUser"] == EXPECTED_RUN_AS_USER, f"runAsUser should be {EXPECTED_RUN_AS_USER}"
    assert sc["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation must be false"
    assert sc["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem must be true"
    assert "ALL" in sc["capabilities"]["drop"], "Must drop ALL capabilities"

# AC-3 Tests
def test_ac3_service_exists(all_manifests):
    assert "Service" in all_manifests, "frontend-proxy Service manifest not found"
    svc = all_manifests["Service"]
    assert svc["metadata"]["name"] == SERVICE_NAME, f"Service name should be {SERVICE_NAME}"
    assert svc["spec"]["type"] == "ClusterIP", "Service type should be ClusterIP"

def test_ac3_service_ports(all_manifests):
    if "Service" not in all_manifests:
        pytest.skip("Service not present")
    svc = all_manifests["Service"]
    ports = svc["spec"]["ports"]
    http_found = False
    https_found = False
    for port in ports:
        if port["port"] == EXPECTED_SERVICE_PORT_HTTP:
            assert port["targetPort"] == EXPECTED_CONTAINER_PORT_HTTP, f"HTTP targetPort should be {EXPECTED_CONTAINER_PORT_HTTP}"
            http_found = True
        if port["port"] == EXPECTED_SERVICE_PORT_HTTPS:
            assert port["targetPort"] == EXPECTED_CONTAINER_PORT_HTTPS, f"HTTPS targetPort should be {EXPECTED_CONTAINER_PORT_HTTPS}"
            https_found = True
    assert http_found, "HTTP port 80 not exposed on service"
    assert https_found, "HTTPS port 443 not exposed on service"

def test_ac3_service_selector(all_manifests):
    if "Service" not in all_manifests:
        pytest.skip("Service not present")
    svc = all_manifests["Service"]
    assert svc["spec"]["selector"]["app.kubernetes.io/name"] == DEPLOYMENT_NAME, "Service selector does not match deployment labels"

# AC-4 Tests
def test_ac4_manifests_valid_kubernetes():
    manifest_files = [get_manifest_path(f) for f in os.listdir(MANIFEST_DIR) if f.endswith(".yaml") and "frontend-proxy" in f]
    assert len(manifest_files) >= 3, "Missing required manifest files (ConfigMap, Deployment, Service)"
    for mf in manifest_files:
        result = subprocess.run(
            ["kubectl", "apply", "-f", mf, "--dry-run=server", "--namespace", "default"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"Manifest {mf} failed dry-run validation: {result.stderr}"

# AC-5 Test
def test_ac5_nginx_config_forwards_to_frontend(all_manifests):
    if "ConfigMap" not in all_manifests:
        pytest.skip("ConfigMap not present")
    cm = all_manifests["ConfigMap"]
    nginx_conf = cm["data"]["nginx.conf"]
    assert "proxy_pass" in nginx_conf, "No proxy_pass directive found in Nginx config"
    assert "http://frontend" in nginx_conf or "http://frontend." in nginx_conf, "Nginx config does not forward to frontend service"
