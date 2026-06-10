#!/usr/bin/env python3
import os
import subprocess
import yaml
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

MANIFESTS_DIR = "kubernetes/product-reviews/"
DEPLOYMENT_PATH = os.path.join(MANIFESTS_DIR, "deployment.yaml")
SERVICE_PATH = os.path.join(MANIFESTS_DIR, "service.yaml")
SERVICE_ACCOUNT_PATH = os.path.join(MANIFESTS_DIR, "serviceaccount.yaml")
PDB_PATH = os.path.join(MANIFESTS_DIR, "poddisruptionbudget.yaml")
NAMESPACE = "default"
SERVICE_NAME = "product-reviews"

def test_ac1_all_resources_exist_and_apply_successfully():
    """AC-1: All four resources (ServiceAccount, Service, Deployment, PodDisruptionBudget) are successfully created when manifests are applied"""
    # Check all manifest files exist
    assert os.path.exists(SERVICE_ACCOUNT_PATH), f"ServiceAccount manifest missing at {SERVICE_ACCOUNT_PATH}"
    assert os.path.exists(SERVICE_PATH), f"Service manifest missing at {SERVICE_PATH}"
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment manifest missing at {DEPLOYMENT_PATH}"
    assert os.path.exists(PDB_PATH), f"PodDisruptionBudget manifest missing at {PDB_PATH}"
    
    # Validate ServiceAccount schema
    with open(SERVICE_ACCOUNT_PATH, "r") as f:
        sa = yaml.safe_load(f)
    assert sa["apiVersion"] == "v1"
    assert sa["kind"] == "ServiceAccount"
    assert sa["metadata"]["name"] == SERVICE_NAME
    assert sa["metadata"]["namespace"] == NAMESPACE
    
    # Validate Service schema
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    assert svc["apiVersion"] == "v1"
    assert svc["kind"] == "Service"
    assert svc["metadata"]["name"] == SERVICE_NAME
    assert svc["spec"]["type"] == "ClusterIP"
    grpc_port = next(p for p in svc["spec"]["ports"] if p["name"] == "grpc")
    assert grpc_port["port"] == 8080
    assert grpc_port["targetPort"] == 8080
    assert svc["spec"]["selector"]["app.kubernetes.io/name"] == SERVICE_NAME
    
    # Validate Deployment schema
    with open(DEPLOYMENT_PATH, "r") as f:
        deploy = yaml.safe_load(f)
    assert deploy["apiVersion"] == "apps/v1"
    assert deploy["kind"] == "Deployment"
    assert deploy["metadata"]["name"] == SERVICE_NAME
    assert deploy["spec"]["replicas"] == 2
    
    # Validate PDB schema
    with open(PDB_PATH, "r") as f:
        pdb = yaml.safe_load(f)
    assert pdb["apiVersion"] == "policy/v1"
    assert pdb["kind"] == "PodDisruptionBudget"
    assert pdb["metadata"]["name"] == SERVICE_NAME
    
    # Dry run apply to validate manifests work
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR, "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"kubectl apply dry run failed: {apply_result.stderr}"

def test_ac2_security_context_configured_correctly():
    """AC-2: All pods have required security context settings: non-root, read-only root fs, no privilege escalation, all capabilities dropped"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deploy = yaml.safe_load(f)
    
    pod_spec = deploy["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Check pod-level security context
    assert "securityContext" in pod_spec
    pod_sc = pod_spec["securityContext"]
    assert pod_sc["runAsNonRoot"] == True
    assert pod_sc["runAsUser"] == 10001
    assert pod_sc["fsGroup"] == 10001
    
    # Check container-level security context
    assert "securityContext" in container
    container_sc = container["securityContext"]
    assert container_sc["allowPrivilegeEscalation"] == False
    assert container_sc["readOnlyRootFilesystem"] == True
    assert "drop" in container_sc["capabilities"]
    assert "ALL" in container_sc["capabilities"]["drop"]

def test_ac3_grpc_probes_configured_correctly():
    """AC-3: Deployment has gRPC liveness and readiness probes pointing to port 8080 with correct timing parameters"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deploy = yaml.safe_load(f)
    
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container
    assert "readinessProbe" in container
    
    liveness = container["livenessProbe"]
    assert "grpc" in liveness
    assert liveness["grpc"]["port"] == 8080
    assert liveness["grpc"]["service"] == SERVICE_NAME
    assert liveness["initialDelaySeconds"] == 5
    assert liveness["periodSeconds"] == 10
    assert liveness["timeoutSeconds"] == 1
    assert liveness["failureThreshold"] == 3
    
    readiness = container["readinessProbe"]
    assert "grpc" in readiness
    assert readiness["grpc"]["port"] == 8080
    assert readiness["grpc"]["service"] == SERVICE_NAME
    assert readiness["initialDelaySeconds"] == 2
    assert readiness["periodSeconds"] == 5
    assert readiness["timeoutSeconds"] == 1
    assert readiness["failureThreshold"] == 3

def test_ac4_resource_requests_limits_match_other_python_services():
    """AC-4: CPU/memory resource requests and limits exactly match values used for other Python services"""
    with open(DEPLOYMENT_PATH, "r") as f:
        deploy = yaml.safe_load(f)
    
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container
    resources = container["resources"]
    
    assert "requests" in resources
    assert resources["requests"]["cpu"] == "100m"
    assert resources["requests"]["memory"] == "128Mi"
    
    assert "limits" in resources
    assert resources["limits"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "256Mi"

def test_ac5_standard_labels_and_annotations_present():
    """AC-5: All resources have standard Kubernetes labels and annotations including auto-instrumentation and observability metadata"""
    # Check Deployment labels and annotations
    with open(DEPLOYMENT_PATH, "r") as f:
        deploy = yaml.safe_load(f)
    
    deploy_labels = deploy["metadata"]["labels"]
    assert deploy_labels["app.kubernetes.io/name"] == SERVICE_NAME
    assert deploy_labels["app.kubernetes.io/part-of"] == "opentelemetry-demo"
    assert deploy_labels["app.kubernetes.io/component"] == "service"
    
    pod_annotations = deploy["spec"]["template"]["metadata"]["annotations"]
    assert pod_annotations["instrumentation.opentelemetry.io/inject-python"] == "true"
    assert pod_annotations["prometheus.io/scrape"] == "true"
    assert pod_annotations["prometheus.io/port"] == "8080"
    
    # Check Service labels
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    svc_labels = svc["metadata"]["labels"]
    assert svc_labels["app.kubernetes.io/name"] == SERVICE_NAME
    assert svc_labels["app.kubernetes.io/part-of"] == "opentelemetry-demo"
    assert svc_labels["app.kubernetes.io/component"] == "service"

def test_ac6_pdb_enforces_min_available_replicas():
    """AC-6: PodDisruptionBudget enforces a minimum of 1 available replica during voluntary disruptions"""
    with open(PDB_PATH, "r") as f:
        pdb = yaml.safe_load(f)
    
    assert pdb["spec"]["minAvailable"] == 1
    assert pdb["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == SERVICE_NAME

@pytest.mark.e2e
def test_ac7_service_responds_to_grpc_health_checks():
    """AC-7: Fully deployed service responds successfully to gRPC health check requests via ClusterIP endpoint"""
    # Apply manifests
    apply_result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFESTS_DIR],
        capture_output=True,
        text=True
    )
    assert apply_result.returncode == 0, f"Failed to apply manifests: {apply_result.stderr}"
    
    # Wait for pods to be running and ready
    config.load_kube_config()
    v1 = client.CoreV1Api()
    
    all_pods_ready = False
    for _ in range(120):
        try:
            pods = v1.list_namespaced_pod(NAMESPACE, label_selector=f"app.kubernetes.io/name={SERVICE_NAME}")
            if len(pods.items) >= 2:
                ready_count = sum(1 for p in pods.items if all(c.ready for c in p.status.container_statuses))
                if ready_count >= 2:
                    all_pods_ready = True
                    break
        except ApiException:
            pass
        time.sleep(1)
    
    assert all_pods_ready, "Not all pods reached ready state within 120s"
    
    # Get service ClusterIP
    svc = v1.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    service_ip = svc.spec.cluster_ip
    
    # Test gRPC health check using grpcurl
    grpc_health_result = subprocess.run(
        [
            "kubectl", "run", "-it", "--rm", "grpc-health-test",
            "--image=fullstorydev/grpcurl:latest",
            "--restart=Never",
            "--", f"grpcurl -plaintext {service_ip}:8080 grpc.health.v1.Health/Check"
        ],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Clean up resources
    subprocess.run(["kubectl", "delete", "-f", MANIFESTS_DIR], capture_output=True)
    
    assert grpc_health_result.returncode == 0, f"gRPC health check failed: {grpc_health_result.stderr}"
    assert '"status": "SERVING"' in grpc_health_result.stdout, "Expected SERVING status from health check"
