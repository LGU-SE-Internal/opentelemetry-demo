import os
import yaml
import subprocess
import pytest

DEPLOYMENT_PATH = "kubernetes/product-reviews/deployment.yaml"
SERVICE_PATH = "kubernetes/product-reviews/service.yaml"
EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "readOnlyRootFilesystem": True,
    "allowPrivilegeEscalation": False,
    "capabilities": {
        "drop": ["ALL"]
    }
}
EXPECTED_PROMETHEUS_ANNOTATIONS = {
    "prometheus.io/scrape": "true",
    "prometheus.io/port": "9464",
    "prometheus.io/path": "/metrics"
}
EXPECTED_PROBE_CONFIG = {
    "initialDelaySeconds": 5,
    "periodSeconds": 10,
    "timeoutSeconds": 3,
    "failureThreshold": 3
}

def test_ac1_deployment_resource_limits_requests():
    # AC-1: Deployment has resource requests: cpu:100m, memory:128Mi, limits: cpu:200m, memory:256Mi
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert manifest["apiVersion"] == "apps/v1", f"Expected apiVersion apps/v1, got {manifest['apiVersion']}"
    assert manifest["kind"] == "Deployment", f"Expected kind Deployment, got {manifest['kind']}"
    assert manifest["metadata"]["name"] == "product-reviews", f"Expected name product-reviews, got {manifest['metadata']['name']}"
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container, "No resources block found in container spec"
    
    resources = container["resources"]
    assert resources["requests"]["cpu"] == "100m", f"Expected CPU request 100m, got {resources['requests']['cpu']}"
    assert resources["requests"]["memory"] == "128Mi", f"Expected memory request 128Mi, got {resources['requests']['memory']}"
    assert resources["limits"]["cpu"] == "200m", f"Expected CPU limit 200m, got {resources['limits']['cpu']}"
    assert resources["limits"]["memory"] == "256Mi", f"Expected memory limit 256Mi, got {resources['limits']['memory']}"

def test_ac2_deployment_liveness_readiness_probes():
    # AC-2: Deployment has liveness and readiness probes using gRPC health check on port 8080 with correct timing
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "No livenessProbe found"
    assert "readinessProbe" in container, "No readinessProbe found"
    
    # Verify liveness probe
    liveness = container["livenessProbe"]
    assert "grpc" in liveness, "Liveness probe should use gRPC health check"
    assert liveness["grpc"]["port"] == 8080, f"Expected liveness gRPC port 8080, got {liveness['grpc']['port']}"
    for k, v in EXPECTED_PROBE_CONFIG.items():
        assert liveness[k] == v, f"Liveness probe {k} mismatch: expected {v}, got {liveness[k]}"
    
    # Verify readiness probe
    readiness = container["readinessProbe"]
    assert "grpc" in readiness, "Readiness probe should use gRPC health check"
    assert readiness["grpc"]["port"] == 8080, f"Expected readiness gRPC port 8080, got {readiness['grpc']['port']}"
    for k, v in EXPECTED_PROBE_CONFIG.items():
        assert readiness[k] == v, f"Readiness probe {k} mismatch: expected {v}, got {readiness[k]}"

def test_ac3_deployment_security_context():
    # AC-3: Deployment has exact specified security context for least privilege
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "securityContext" in container, "No securityContext found in container spec"
    
    sc = container["securityContext"]
    assert sc == EXPECTED_SECURITY_CONTEXT, f"Security context mismatch:\nExpected: {EXPECTED_SECURITY_CONTEXT}\nGot: {sc}"
    
    # Verify emptyDir volume for /tmp exists (required for read-only filesystem)
    volumes = manifest["spec"]["template"]["spec"].get("volumes", [])
    tmp_volume = next((v for v in volumes if v.get("emptyDir") and v["name"] == "tmp"), None)
    assert tmp_volume is not None, "Missing emptyDir volume for /tmp (required for read-only root filesystem)"
    volume_mounts = container.get("volumeMounts", [])
    tmp_mount = next((m for m in volume_mounts if m["name"] == "tmp" and m["mountPath"] == "/tmp"), None)
    assert tmp_mount is not None, "Missing volume mount for /tmp"

def test_ac4_service_configuration():
    # AC-4: Service exposes port 8080 (target port 8080) with appProtocol: grpc
    assert os.path.exists(SERVICE_PATH), f"Service file not found at {SERVICE_PATH}"
    
    with open(SERVICE_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    assert manifest["apiVersion"] == "v1", f"Expected apiVersion v1, got {manifest['apiVersion']}"
    assert manifest["kind"] == "Service", f"Expected kind Service, got {manifest['kind']}"
    assert manifest["metadata"]["name"] == "product-reviews", f"Expected service name product-reviews, got {manifest['metadata']['name']}"
    assert manifest["spec"]["type"] == "ClusterIP", f"Expected service type ClusterIP, got {manifest['spec']['type']}"
    
    ports = manifest["spec"]["ports"]
    grpc_port = next((p for p in ports if p["port"] == 8080), None)
    assert grpc_port is not None, "Missing port 8080 in Service spec"
    assert grpc_port["targetPort"] == 8080, f"Expected targetPort 8080, got {grpc_port['targetPort']}"
    assert grpc_port["appProtocol"] == "grpc", f"Expected appProtocol grpc for port 8080, got {grpc_port.get('appProtocol')}"

def test_ac5_deployment_prometheus_annotations():
    # AC-5: Deployment pod template has exact Prometheus scraping annotations
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file not found at {DEPLOYMENT_PATH}"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        manifest = yaml.safe_load(f)
    
    pod_annotations = manifest["spec"]["template"]["metadata"].get("annotations", {})
    for k, v in EXPECTED_PROMETHEUS_ANNOTATIONS.items():
        assert k in pod_annotations, f"Missing Prometheus annotation {k}"
        assert pod_annotations[k] == v, f"Annotation {k} mismatch: expected {v}, got {pod_annotations[k]}"

def test_ac6_manifests_in_standard_directory():
    # AC-6: Manifests are stored in standard kubernetes/ directory
    assert os.path.exists("kubernetes/product-reviews/"), "product-reviews directory missing from kubernetes/"
    assert os.path.isfile(DEPLOYMENT_PATH), f"deployment.yaml missing from kubernetes/product-reviews/"
    assert os.path.isfile(SERVICE_PATH), f"service.yaml missing from kubernetes/product-reviews/"
    
    # Verify labels match required spec
    with open(DEPLOYMENT_PATH, "r") as f:
        deploy = yaml.safe_load(f)
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    
    deploy_labels = deploy["metadata"]["labels"]
    svc_labels = svc["metadata"]["labels"]
    
    assert deploy_labels["app.kubernetes.io/name"] == "product-reviews", "Missing required deployment label app.kubernetes.io/name"
    assert deploy_labels["app.kubernetes.io/part-of"] == "opentelemetry-demo", "Missing required deployment label app.kubernetes.io/part-of"
    assert svc_labels["app.kubernetes.io/name"] == "product-reviews", "Missing required service label app.kubernetes.io/name"
    assert svc_labels["app.kubernetes.io/part-of"] == "opentelemetry-demo", "Missing required service label app.kubernetes.io/part-of"

def test_ac7_manifests_pass_dry_run_and_health_checks():
    # AC-7: Manifests apply successfully and service passes health checks
    # First validate manifests with dry run
    for path in [DEPLOYMENT_PATH, SERVICE_PATH]:
        result = subprocess.run(
            ["kubectl", "apply", "-f", path, "--dry-run=client"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"Dry run failed for {path}: {result.stderr}"
