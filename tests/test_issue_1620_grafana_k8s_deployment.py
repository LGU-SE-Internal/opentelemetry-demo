#!/usr/bin/env python3
import os
import time
import yaml
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "./kubernetes/grafana/deployment.yaml"
PVC_PATH = "./kubernetes/grafana/pvc.yaml"
SERVICE_PATH = "./kubernetes/grafana/service.yaml"
EXPECTED_LABELS = {
    "app.kubernetes.io/name": "grafana",
    "app.kubernetes.io/part-of": "opentelemetry-demo"
}


def test_ac1_deployment_resource_requirements():
    """AC-1: Grafana Deployment manifest exists at kubernetes/grafana/deployment.yaml and explicitly defines CPU requests ≥ 100m, CPU limits ≤ 500m, memory requests ≥ 256Mi, memory limits ≤ 512Mi"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    assert dep["apiVersion"] == "apps/v1", "Deployment apiVersion should be apps/v1"
    assert dep["kind"] == "Deployment", "Kind should be Deployment"
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    assert resources, "Resource requirements not configured"
    assert "requests" in resources, "Resource requests missing"
    assert "limits" in resources, "Resource limits missing"
    
    # Validate CPU
    cpu_request = resources["requests"].get("cpu", "")
    cpu_limit = resources["limits"].get("cpu", "")
    assert cpu_request.endswith("m"), "CPU request should be in millicores"
    assert int(cpu_request.replace("m", "")) >= 100, "CPU request should be at least 100m"
    assert cpu_limit.endswith("m"), "CPU limit should be in millicores"
    assert int(cpu_limit.replace("m", "")) <= 500, "CPU limit should be at most 500m"
    
    # Validate Memory
    mem_request = resources["requests"].get("memory", "")
    mem_limit = resources["limits"].get("memory", "")
    assert mem_request.endswith("Mi"), "Memory request should be in Mi"
    assert int(mem_request.replace("Mi", "")) >= 256, "Memory request should be at least 256Mi"
    assert mem_limit.endswith("Mi"), "Memory limit should be in Mi"
    assert int(mem_limit.replace("Mi", "")) <= 512, "Memory limit should be at most 512Mi"


def test_ac2_liveness_probe_configured():
    """AC-2: The Grafana Deployment manifest includes a liveness probe targeting GET /api/health on port 3000 with initialDelaySeconds ≥ 30, periodSeconds ≥ 10, failureThreshold = 3"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    liveness = container.get("livenessProbe", {})
    
    assert liveness, "Liveness probe not configured"
    assert liveness["httpGet"]["port"] == 3000, "Liveness probe should use port 3000"
    assert liveness["httpGet"]["path"] == "/api/health", "Liveness probe should GET /api/health"
    assert liveness["initialDelaySeconds"] >= 30, "Liveness initial delay should be at least 30s"
    assert liveness["periodSeconds"] >= 10, "Liveness period should be at least 10s"
    assert liveness["failureThreshold"] == 3, "Liveness failure threshold should be 3"


def test_ac3_readiness_probe_configured():
    """AC-3: The Grafana Deployment manifest includes a readiness probe targeting GET /api/health on port 3000 with initialDelaySeconds ≥ 10, periodSeconds ≥ 5, failureThreshold = 3"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    readiness = container.get("readinessProbe", {})
    
    assert readiness, "Readiness probe not configured"
    assert readiness["httpGet"]["port"] == 3000, "Readiness probe should use port 3000"
    assert readiness["httpGet"]["path"] == "/api/health", "Readiness probe should GET /api/health"
    assert readiness["initialDelaySeconds"] >= 10, "Readiness initial delay should be at least 10s"
    assert readiness["periodSeconds"] >= 5, "Readiness period should be at least 5s"
    assert readiness["failureThreshold"] == 3, "Readiness failure threshold should be 3"


def test_ac4_security_context_configured():
    """AC-4: The Grafana Deployment manifest security context is configured with runAsNonRoot: true, runAsUser: 472, allowPrivilegeEscalation: false, and privileged: false"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    security_context = dep["spec"]["template"]["spec"]["containers"][0].get("securityContext", {})
    
    assert security_context, "Security context not configured"
    assert security_context["runAsNonRoot"] == True, "Should run as non-root user"
    assert security_context["runAsUser"] == 472, "Should run as UID 472 (official Grafana non-root user)"
    assert security_context["runAsGroup"] == 472, "Should run as GID 472 (official Grafana non-root group)"
    assert security_context["allowPrivilegeEscalation"] == False, "Privilege escalation should be disabled"
    assert security_context["privileged"] == False, "Container should not run in privileged mode"


def test_ac5_pvc_exists_and_mounted():
    """AC-5: A PersistentVolumeClaim manifest exists at kubernetes/grafana/pvc.yaml with accessModes: ["ReadWriteOnce"], storage request ≥ 10Gi, and is mounted to /var/lib/grafana in the Deployment"""
    # Check PVC exists
    assert os.path.exists(PVC_PATH), f"PVC file {PVC_PATH} not found"
    with open(PVC_PATH, "r") as f:
        pvc = yaml.safe_load(f)
    
    assert pvc["apiVersion"] == "v1", "PVC apiVersion should be v1"
    assert pvc["kind"] == "PersistentVolumeClaim", "Kind should be PersistentVolumeClaim"
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC access mode should be ReadWriteOnce"
    
    storage_request = pvc["spec"]["resources"]["requests"].get("storage", "")
    assert storage_request.endswith("Gi"), "Storage request should be in Gi"
    assert int(storage_request.replace("Gi", "")) >= 10, "Storage request should be at least 10Gi"
    
    # Check PVC is mounted in deployment
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    
    grafana_mount = None
    for mount in volume_mounts:
        if mount["mountPath"] == "/var/lib/grafana":
            grafana_mount = mount
            break
    
    assert grafana_mount, "PVC not mounted to /var/lib/grafana"
    assert "name" in grafana_mount, "Volume mount missing name reference"
    
    # Check volume exists in deployment spec
    volumes = dep["spec"]["template"]["spec"].get("volumes", [])
    volume_found = False
    for volume in volumes:
        if volume["name"] == grafana_mount["name"]:
            assert volume["persistentVolumeClaim"]["claimName"] == pvc["metadata"]["name"], "Volume references wrong PVC"
            volume_found = True
            break
    
    assert volume_found, "PVC volume not defined in deployment spec"


def test_ac6_service_configured():
    """AC-6: A ClusterIP Service manifest exists at kubernetes/grafana/service.yaml with type: ClusterIP, exposing port 3000 targeting the Grafana Deployment's container port 3000, with no NodePort or LoadBalancer configuration"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    
    assert svc["apiVersion"] == "v1", "Service apiVersion should be v1"
    assert svc["kind"] == "Service", "Kind should be Service"
    assert svc["spec"]["type"] == "ClusterIP", "Service type should be ClusterIP (internal only)"
    
    ports = svc["spec"]["ports"]
    assert len(ports) == 1, "Service should expose exactly one port (3000)"
    assert ports[0]["port"] == 3000, "Service should expose port 3000"
    assert ports[0]["targetPort"] == 3000, "Service should target container port 3000"
    assert "nodePort" not in ports[0], "Service should not have NodePort configured"
    
    # Verify service selector matches deployment labels
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    selector = svc["spec"]["selector"]
    dep_labels = dep["spec"]["template"]["metadata"]["labels"]
    for k, v in selector.items():
        assert dep_labels.get(k) == v, f"Service selector {k}: {v} does not match deployment pod labels"


def test_ac7_pod_running_after_deployment():
    """AC-7: When applying all 3 manifests to a running Kubernetes cluster, the Grafana pod transitions to Running state within 60 seconds and passes both liveness and readiness probes"""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"  # Adjust if demo uses different namespace
    
    start = time.time()
    while time.time() - start < 60:
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=grafana")
            if len(pods.items) > 0:
                pod = pods.items[0]
                if pod.status.phase == "Running":
                    # Check pod conditions for ready status
                    for cond in pod.status.conditions:
                        if cond.type == "Ready" and cond.status == "True":
                            return
        except ApiException:
            pass
        time.sleep(5)
    
    assert False, "Grafana pod not in Running and Ready state after 60s"


def test_ac8_service_returns_200_ok():
    """AC-8: When sending an HTTP GET request to the ClusterIP service's port 3000 from within the cluster, a 200 OK response is returned containing the Grafana login page content"""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"
    
    # Get service cluster IP
    svc = v1.read_namespaced_service("grafana", namespace)
    cluster_ip = svc.spec.cluster_ip
    
    try:
        resp = requests.get(f"http://{cluster_ip}:3000", timeout=10)
        assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}"
        assert "Grafana" in resp.text, "Response does not contain Grafana login page content"
    except requests.exceptions.RequestException as e:
        assert False, f"Failed to connect to Grafana service: {str(e)}"
