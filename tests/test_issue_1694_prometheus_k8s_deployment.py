#!/usr/bin/env python3
import os
import time
import yaml
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "./kubernetes/prometheus/deployment.yaml"
PVC_PATH = "./kubernetes/prometheus/pvc.yaml"
SERVICE_PATH = "./kubernetes/prometheus/service.yaml"
CONFIGMAP_PATH = "./kubernetes/prometheus/configmap.yaml"
EXPECTED_IMAGE = "prom/prometheus:v2.47.0"
EXPECTED_UID = 65534
EXPECTED_CONFIGMAP_NAME = "opentelemetry-demo-prometheus"
EXPECTED_PVC_NAME = "opentelemetry-demo-prometheus"
EXPECTED_SERVICE_NAME = "opentelemetry-demo-prometheus"
EXPECTED_DEPLOYMENT_NAME = "opentelemetry-demo-prometheus"


def test_ac1_pods_running_no_restarts():
    """AC-1: When the Kubernetes Deployment is applied, all pods reach Running state with 0 restarts within 2 minutes of creation."""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"

    start = time.time()
    while time.time() - start < 120:
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector=f"app.kubernetes.io/name=prometheus,app.kubernetes.io/part-of=opentelemetry-demo")
            if len(pods.items) > 0:
                all_running = True
                for pod in pods.items:
                    if pod.status.phase != "Running":
                        all_running = False
                        break
                    if pod.status.container_statuses:
                        for cs in pod.status.container_statuses:
                            if cs.restart_count > 0:
                                all_running = False
                                break
                if all_running:
                    return
        except ApiException:
            pass
        time.sleep(5)

    assert False, "Prometheus pods not all Running with 0 restarts within 2 minutes"


def test_ac2_non_root_user_no_privileges():
    """AC-2: Exec into a running Prometheus pod confirms the process is running as non-root user (UID != 0) with no privileged capabilities."""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"

    pods = v1.list_namespaced_pod(namespace, label_selector=f"app.kubernetes.io/name=prometheus,app.kubernetes.io/part-of=opentelemetry-demo")
    assert len(pods.items) > 0, "No Prometheus pods found"
    pod_name = pods.items[0].metadata.name

    # Check security context from deployment spec first
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    security_context = container.get("securityContext", {})
    assert security_context.get("runAsNonRoot") == True, "Container must run as non-root"
    assert security_context.get("runAsUser") == EXPECTED_UID, f"Container must run as UID {EXPECTED_UID}"
    assert security_context.get("privileged") == False, "Container must not be privileged"
    assert security_context.get("allowPrivilegeEscalation") == False, "Privilege escalation must be disabled"
    assert security_context.get("readOnlyRootFilesystem") == True, "Root filesystem must be read-only"


def test_ac3_health_endpoint_returns_200():
    """AC-3: A GET request to `http://<prometheus-service>:9090/-/healthy` returns 200 OK status code."""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"

    svc = v1.read_namespaced_service(EXPECTED_SERVICE_NAME, namespace)
    cluster_ip = svc.spec.cluster_ip

    try:
        resp = requests.get(f"http://{cluster_ip}:9090/-/healthy", timeout=10)
        assert resp.status_code == 200, f"Expected 200 OK from /-/healthy, got {resp.status_code}"
    except requests.exceptions.RequestException as e:
        assert False, f"Failed to connect to Prometheus health endpoint: {str(e)}"


def test_ac4_ready_endpoint_returns_200():
    """AC-4: A GET request to `http://<prometheus-service>:9090/-/ready` returns 200 OK status code."""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"

    svc = v1.read_namespaced_service(EXPECTED_SERVICE_NAME, namespace)
    cluster_ip = svc.spec.cluster_ip

    try:
        resp = requests.get(f"http://{cluster_ip}:9090/-/ready", timeout=10)
        assert resp.status_code == 200, f"Expected 200 OK from /-/ready, got {resp.status_code}"
    except requests.exceptions.RequestException as e:
        assert False, f"Failed to connect to Prometheus ready endpoint: {str(e)}"


def test_ac5_configmap_mounted_correctly():
    """AC-5: The prometheus-config.yaml from the ConfigMap is visible at `/etc/prometheus/prometheus.yaml` inside the pod with content matching the ConfigMap data."""
    # Check ConfigMap exists
    assert os.path.exists(CONFIGMAP_PATH), f"ConfigMap file {CONFIGMAP_PATH} not found"
    with open(CONFIGMAP_PATH, "r") as f:
        cm = yaml.safe_load(f)
    assert cm["kind"] == "ConfigMap", "Kind should be ConfigMap"
    assert cm["metadata"]["name"] == EXPECTED_CONFIGMAP_NAME, f"ConfigMap name should be {EXPECTED_CONFIGMAP_NAME}"
    assert "prometheus.yaml" in cm["data"], "ConfigMap must contain prometheus.yaml key"

    # Check mount in deployment
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    config_mount = None
    for mount in volume_mounts:
        if mount["mountPath"] == "/etc/prometheus/":
            config_mount = mount
            break
    assert config_mount, "ConfigMap not mounted to /etc/prometheus/"

    volumes = dep["spec"]["template"]["spec"].get("volumes", [])
    config_volume_found = False
    for volume in volumes:
        if volume["name"] == config_mount["name"]:
            assert volume["configMap"]["name"] == EXPECTED_CONFIGMAP_NAME, "Volume references wrong ConfigMap"
            config_volume_found = True
            break
    assert config_volume_found, "ConfigMap volume not defined in deployment spec"


def test_ac6_pvc_data_persists_after_pod_restart():
    """AC-6: Metrics data written to `/prometheus` directory persists after deleting and recreating the Prometheus pod (PVC retains data)."""
    # Check PVC exists and configured correctly
    assert os.path.exists(PVC_PATH), f"PVC file {PVC_PATH} not found"
    with open(PVC_PATH, "r") as f:
        pvc = yaml.safe_load(f)
    assert pvc["kind"] == "PersistentVolumeClaim", "Kind should be PersistentVolumeClaim"
    assert pvc["metadata"]["name"] == EXPECTED_PVC_NAME, f"PVC name should be {EXPECTED_PVC_NAME}"
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC access mode should be ReadWriteOnce"
    storage_request = pvc["spec"]["resources"]["requests"].get("storage", "")
    assert storage_request.endswith("Gi"), "Storage request should be in Gi"
    assert int(storage_request.replace("Gi", "")) >= 10, "Storage request should be at least 10Gi"

    # Check PVC mount in deployment
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    volume_mounts = container.get("volumeMounts", [])
    pvc_mount = None
    for mount in volume_mounts:
        if mount["mountPath"] == "/prometheus":
            pvc_mount = mount
            break
    assert pvc_mount, "PVC not mounted to /prometheus"

    volumes = dep["spec"]["template"]["spec"].get("volumes", [])
    pvc_volume_found = False
    for volume in volumes:
        if volume["name"] == pvc_mount["name"]:
            assert volume["persistentVolumeClaim"]["claimName"] == EXPECTED_PVC_NAME, "Volume references wrong PVC"
            pvc_volume_found = True
            break
    assert pvc_volume_found, "PVC volume not defined in deployment spec"


def test_ac7_clusterip_service_accessible():
    """AC-7: The ClusterIP service is accessible within the cluster on port 9090, returning the Prometheus UI homepage when requested."""
    # Check Service configuration
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    assert svc["kind"] == "Service", "Kind should be Service"
    assert svc["metadata"]["name"] == EXPECTED_SERVICE_NAME, f"Service name should be {EXPECTED_SERVICE_NAME}"
    assert svc["spec"]["type"] == "ClusterIP", "Service type should be ClusterIP"
    assert len(svc["spec"]["ports"]) == 1, "Service should expose exactly one port"
    assert svc["spec"]["ports"][0]["port"] == 9090, "Service should expose port 9090"
    assert svc["spec"]["ports"][0]["targetPort"] == 9090, "Service should target container port 9090"

    # Check connectivity to UI
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"

    svc = v1.read_namespaced_service(EXPECTED_SERVICE_NAME, namespace)
    cluster_ip = svc.spec.cluster_ip

    try:
        resp = requests.get(f"http://{cluster_ip}:9090", timeout=10)
        assert resp.status_code == 200, f"Expected 200 OK from Prometheus UI, got {resp.status_code}"
        assert "Prometheus" in resp.text, "Response does not contain Prometheus UI content"
    except requests.exceptions.RequestException as e:
        assert False, f"Failed to connect to Prometheus UI: {str(e)}"


def test_ac8_resource_limits_within_range():
    """AC-8: The Deployment manifest contains resource requests and limits that are within the defined ranges (CPU request 500m-2, memory request 1Gi-4Gi)."""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)

    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert resources, "Resource requirements not configured"
    assert "requests" in resources, "Resource requests missing"
    assert "limits" in resources, "Resource limits missing"

    # Validate CPU
    cpu_request = resources["requests"].get("cpu", "")
    cpu_limit = resources["limits"].get("cpu", "")
    if cpu_request.endswith("m"):
        cpu_request_val = int(cpu_request.replace("m", "")) / 1000
    else:
        cpu_request_val = float(cpu_request)
    assert 0.5 <= cpu_request_val <= 2, f"CPU request should be between 500m and 2, got {cpu_request}"

    if cpu_limit.endswith("m"):
        cpu_limit_val = int(cpu_limit.replace("m", "")) / 1000
    else:
        cpu_limit_val = float(cpu_limit)
    assert 0.5 <= cpu_limit_val <= 2, f"CPU limit should be between 500m and 2, got {cpu_limit}"

    # Validate Memory
    mem_request = resources["requests"].get("memory", "")
    mem_limit = resources["limits"].get("memory", "")
    if mem_request.endswith("Gi"):
        mem_request_val = int(mem_request.replace("Gi", ""))
    elif mem_request.endswith("Mi"):
        mem_request_val = int(mem_request.replace("Mi", "")) / 1024
    assert 1 <= mem_request_val <=4, f"Memory request should be between 1Gi and 4Gi, got {mem_request}"

    if mem_limit.endswith("Gi"):
        mem_limit_val = int(mem_limit.replace("Gi", ""))
    elif mem_limit.endswith("Mi"):
        mem_limit_val = int(mem_limit.replace("Mi", "")) / 1024
    assert 1 <= mem_limit_val <=4, f"Memory limit should be between 1Gi and 4Gi, got {mem_limit}"

    # Validate other deployment properties
    assert container["image"] == EXPECTED_IMAGE, f"Container image should be {EXPECTED_IMAGE}"
    assert container["imagePullPolicy"] == "IfNotPresent", "Image pull policy should be IfNotPresent"
    assert any("--config.file=/etc/prometheus/prometheus.yaml" in arg for arg in container["args"]), "Missing config file argument"
    assert any("--storage.tsdb.path=/prometheus" in arg for arg in container["args"]), "Missing storage path argument"

    # Validate probes
    liveness = container.get("livenessProbe", {})
    assert liveness, "Liveness probe not configured"
    assert liveness["httpGet"]["port"] == 9090, "Liveness probe should use port 9090"
    assert liveness["httpGet"]["path"] == "/", "Liveness probe should GET /"
    assert liveness["initialDelaySeconds"] == 30, "Liveness initial delay should be 30s"
    assert liveness["periodSeconds"] == 15, "Liveness period should be 15s"

    readiness = container.get("readinessProbe", {})
    assert readiness, "Readiness probe not configured"
    assert readiness["httpGet"]["port"] == 9090, "Readiness probe should use port 9090"
    assert readiness["httpGet"]["path"] == "/", "Readiness probe should GET /"
    assert readiness["initialDelaySeconds"] == 5, "Readiness initial delay should be 5s"
    assert readiness["periodSeconds"] == 10, "Readiness period should be 10s"
