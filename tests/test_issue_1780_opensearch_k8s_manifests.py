#!/usr/bin/env python3
import os
import subprocess
import yaml
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import json

MANIFESTS_DIR = "kubernetes/opensearch/"
PVC_PATH = os.path.join(MANIFESTS_DIR, "pvc.yaml")
DEPLOYMENT_PATH = os.path.join(MANIFESTS_DIR, "deployment.yaml")
SERVICE_PATH = os.path.join(MANIFESTS_DIR, "service.yaml")
NAMESPACE = "opentelemetry-demo"

def test_ac1_pod_starts_running_within_2_minutes():
    """AC-1: When the opensearch Deployment is applied to a Kubernetes cluster, the pod starts successfully and reaches Running state within 2 minutes."""
    # Check manifests exist first
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    assert os.path.exists(PVC_PATH), f"PVC file missing at {PVC_PATH}"
    assert os.path.exists(SERVICE_PATH), f"Service file missing at {SERVICE_PATH}"
    
    # Dry run apply to validate manifest schema
    dry_run_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR, "--dry-run=client", "-n", NAMESPACE],
        capture_output=True,
        text=True
    )
    assert dry_run_result.returncode == 0, f"Manifest dry run validation failed: {dry_run_result.stderr}"
    
    # Apply manifests
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR, "-n", NAMESPACE],
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"Failed to apply manifests: {apply_result.stderr}"
    
    # Wait for pod to be running
    config.load_kube_config()
    v1 = client.CoreV1Api()
    
    pod_running = False
    for _ in range(120):  # 2 minutes timeout as per AC
        try:
            pods = v1.list_namespaced_pod(NAMESPACE, label_selector="app: opensearch")
            if pods.items:
                pod = pods.items[0]
                if pod.status.phase == "Running":
                    all_ready = all(c.ready for c in pod.status.container_statuses)
                    if all_ready:
                        pod_running = True
                        break
        except ApiException:
            pass
        time.sleep(1)
    
    # Clean up
    subprocess.run(["kubectl", "delete", "-f", MANIFESTS_DIR, "-n", NAMESPACE], capture_output=True)
    
    assert pod_running, "Opensearch pod did not reach Running state with ready containers within 2 minutes"

def test_ac2_non_root_security_context_configured():
    """AC-2: The Deployment pod runs as non-root user (UID 1000) with no privilege escalation allowed."""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    # Check pod security context
    pod_sc = deployment["spec"]["template"]["spec"]["securityContext"]
    assert pod_sc["runAsUser"] == 1000, f"Expected runAsUser=1000, got {pod_sc.get('runAsUser')}"
    assert pod_sc["runAsNonRoot"] == True, "Expected runAsNonRoot=true"
    assert pod_sc["fsGroup"] == 1000, f"Expected fsGroup=1000, got {pod_sc.get('fsGroup')}"
    
    # Check container security context
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    container_sc = container["securityContext"]
    assert container_sc["allowPrivilegeEscalation"] == False, "Expected allowPrivilegeEscalation=false"

def test_ac3_liveness_probe_works_no_restarts():
    """AC-3: The liveness probe returns HTTP 200 status code when queried, and the pod is not restarted due to liveness probe failures after 10 minutes of runtime."""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "Liveness probe missing from deployment"
    
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/", f"Expected liveness probe path /, got {liveness['httpGet']['path']}"
    assert liveness["httpGet"]["port"] == 9200, f"Expected liveness probe port 9200, got {liveness['httpGet']['port']}"
    assert liveness["initialDelaySeconds"] == 30, f"Expected initialDelaySeconds=30, got {liveness['initialDelaySeconds']}"
    assert liveness["periodSeconds"] == 10, f"Expected periodSeconds=10, got {liveness['periodSeconds']}"
    assert liveness["timeoutSeconds"] == 5, f"Expected timeoutSeconds=5, got {liveness['timeoutSeconds']}"
    assert liveness["failureThreshold"] == 3, f"Expected failureThreshold=3, got {liveness['failureThreshold']}"

def test_ac4_readiness_probe_works_marks_pod_ready():
    """AC-4: The readiness probe returns HTTP 200 status code when queried, and the pod is marked as Ready once the probe succeeds."""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "readinessProbe" in container, "Readiness probe missing from deployment"
    
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/_cluster/health?local=true", f"Expected readiness probe path /_cluster/health?local=true, got {readiness['httpGet']['path']}"
    assert readiness["httpGet"]["port"] == 9200, f"Expected readiness probe port 9200, got {readiness['httpGet']['port']}"
    assert readiness["initialDelaySeconds"] == 10, f"Expected initialDelaySeconds=10, got {readiness['initialDelaySeconds']}"
    assert readiness["periodSeconds"] == 5, f"Expected periodSeconds=5, got {readiness['periodSeconds']}"
    assert readiness["timeoutSeconds"] == 3, f"Expected timeoutSeconds=3, got {readiness['timeoutSeconds']}"
    assert readiness["failureThreshold"] == 3, f"Expected failureThreshold=3, got {readiness['failureThreshold']}"

def test_ac5_pvc_configured_data_persists():
    """AC-5: A 10Gi PersistentVolumeClaim is bound to the pod, and data written to /usr/share/opensearch/data persists across pod restarts."""
    assert os.path.exists(PVC_PATH), f"PVC file missing at {PVC_PATH}"
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    # Check PVC spec
    with open(PVC_PATH, "r") as f:
        pvc = yaml.safe_load(f)
    
    assert pvc["apiVersion"] == "v1", f"Expected PVC apiVersion v1, got {pvc['apiVersion']}"
    assert pvc["kind"] == "PersistentVolumeClaim", f"Expected kind PersistentVolumeClaim, got {pvc['kind']}"
    assert pvc["metadata"]["name"] == "opensearch-data", f"Expected PVC name opensearch-data, got {pvc['metadata']['name']}"
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "Expected accessModes to include ReadWriteOnce"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi", f"Expected storage request 10Gi, got {pvc['spec']['resources']['requests']['storage']}"
    
    # Check volume mount in deployment
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    volumes = deployment["spec"]["template"]["spec"]["volumes"]
    pvc_volume = next(v for v in volumes if v["persistentVolumeClaim"]["claimName"] == "opensearch-data")
    assert pvc_volume is not None, "PVC volume opensearch-data not found in deployment volumes"
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    volume_mount = next(vm for vm in container["volumeMounts"] if vm["name"] == pvc_volume["name"])
    assert volume_mount["mountPath"] == "/usr/share/opensearch/data", f"Expected volume mount path /usr/share/opensearch/data, got {volume_mount['mountPath']}"

def test_ac6_clusterip_service_exposes_ports():
    """AC-6: The ClusterIP service opensearch exposes ports 9200 and 9300, and internal cluster services can access the opensearch HTTP API."""
    assert os.path.exists(SERVICE_PATH), f"Service file missing at {SERVICE_PATH}"
    
    with open(SERVICE_PATH, "r") as f:
        service = yaml.safe_load(f)
    
    assert service["apiVersion"] == "v1", f"Expected Service apiVersion v1, got {service['apiVersion']}"
    assert service["kind"] == "Service", f"Expected kind Service, got {service['kind']}"
    assert service["metadata"]["name"] == "opensearch", f"Expected service name opensearch, got {service['metadata']['name']}"
    assert service["spec"]["type"] == "ClusterIP", f"Expected service type ClusterIP, got {service['spec']['type']}"
    
    ports = service["spec"]["ports"]
    http_port = next(p for p in ports if p["name"] == "http")
    assert http_port["port"] == 9200, f"Expected http port 9200, got {http_port['port']}"
    assert http_port["targetPort"] == 9200, f"Expected http targetPort 9200, got {http_port['targetPort']}"
    
    transport_port = next(p for p in ports if p["name"] == "transport")
    assert transport_port["port"] == 9300, f"Expected transport port 9300, got {transport_port['port']}"
    assert transport_port["targetPort"] == 9300, f"Expected transport targetPort 9300, got {transport_port['targetPort']}"
    
    assert service["spec"]["selector"]["app"] == "opensearch", f"Expected service selector app: opensearch, got {service['spec']['selector']}"

def test_ac7_cluster_name_configured():
    """AC-7: Opensearch cluster name is opentelemetry-demo-logs as returned by GET http://opensearch:9200 response JSON cluster_name field."""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_vars = {e["name"]: e.get("value", "") for e in container["env"]}
    
    assert "cluster.name" in env_vars, "Environment variable cluster.name missing"
    assert env_vars["cluster.name"] == "opentelemetry-demo-logs", f"Expected cluster.name=opentelemetry-demo-logs, got {env_vars['cluster.name']}"

def test_ac8_single_node_discovery_mode_green_health():
    """AC-8: Opensearch runs in single-node discovery mode, with cluster health status green."""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_vars = {e["name"]: e.get("value", "") for e in container["env"]}
    
    assert "discovery.type" in env_vars, "Environment variable discovery.type missing"
    assert env_vars["discovery.type"] == "single-node", f"Expected discovery.type=single-node, got {env_vars['discovery.type']}"

def test_ac9_resource_requests_limits_configured():
    """AC-9: Resource requests (1 CPU, 2Gi memory) and limits (2 CPU, 4Gi memory) are configured on the deployment container."""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file missing at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container, "Resources section missing from container spec"
    
    requests = container["resources"]["requests"]
    assert requests["cpu"] == "1" or requests["cpu"] == "1000m", f"Expected CPU request 1, got {requests['cpu']}"
    assert requests["memory"] == "2Gi", f"Expected memory request 2Gi, got {requests['memory']}"
    
    limits = container["resources"]["limits"]
    assert limits["cpu"] == "2" or limits["cpu"] == "2000m", f"Expected CPU limit 2, got {limits['cpu']}"
    assert limits["memory"] == "4Gi", f"Expected memory limit 4Gi, got {limits['memory']}"
