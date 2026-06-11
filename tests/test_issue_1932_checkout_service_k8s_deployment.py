#!/usr/bin/env python3
import os
import yaml

DEPLOYMENT_PATH = "./kubernetes/checkout-service.deployment.yaml"
EXPECTED_METADATA_LABELS = {
    "app.kubernetes.io/name": "checkout-service",
    "app.kubernetes.io/part-of": "opentelemetry-demo"
}
EXPECTED_RESOURCES = {
    "requests": {
        "cpu": "100m",
        "memory": "128Mi"
    },
    "limits": {
        "cpu": "500m",
        "memory": "256Mi"
    }
}
EXPECTED_PROBE_CONFIG = {
    "httpGet": {
        "path": "/health",
        "port": 8080
    },
    "initialDelaySeconds": 5,
    "periodSeconds": 10,
    "failureThreshold": 3
}
EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "allowPrivilegeEscalation": False,
    "privileged": False,
    "readOnlyRootFilesystem": True
}
EXPECTED_ENV_VARS = [
    "PAYMENT_SERVICE_ADDR",
    "SHIPPING_SERVICE_ADDR",
    "CART_SERVICE_ADDR",
    "PRODUCT_CATALOG_SERVICE_ADDR",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "FEATURE_FLAG_GRPC_SERVICE_ADDR"
]
EXPECTED_ANNOTATIONS = {
    "prometheus.io/scrape": "true",
    "prometheus.io/port": "9090",
    "opentelemetry.io/inject-sdk": "true"
}
EXPECTED_PORTS = [8080, 9090]

def test_ac1_deployment_exists_correct_api_kind_labels():
    """AC-1: Checkout service Deployment manifest exists at standard path, uses apps/v1 API version and Deployment kind, with required metadata labels"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    assert dep["apiVersion"] == "apps/v1", f"Expected apiVersion apps/v1, got {dep.get('apiVersion')}"
    assert dep["kind"] == "Deployment", f"Expected kind Deployment, got {dep.get('kind')}"
    
    # Check metadata labels
    for k, v in EXPECTED_METADATA_LABELS.items():
        assert k in dep["metadata"]["labels"], f"Deployment missing metadata label {k}"
        assert dep["metadata"]["labels"][k] == v, f"Deployment label {k} expected {v}, got {dep['metadata']['labels'][k]}"

def test_ac2_resource_requests_limits_configured():
    """AC-2: Deployment defines correct CPU and memory resource requests and limits aligned with other Go services"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "checkout-service", f"Expected container name checkout-service, got {container.get('name')}"
    
    resources = container.get("resources", {})
    for res_type in ["requests", "limits"]:
        assert res_type in resources, f"Missing {res_type} in resources"
        for res in ["cpu", "memory"]:
            assert res in resources[res_type], f"Missing {res} in {res_type}"
            assert resources[res_type][res] == EXPECTED_RESOURCES[res_type][res], f"{res_type} {res} expected {EXPECTED_RESOURCES[res_type][res]}, got {resources[res_type][res]}"

def test_ac3_liveness_readiness_probes_configured():
    """AC-3: Deployment includes livenessProbe and readinessProbe targeting /health on port 8080 with correct timing parameters"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    for probe_type in ["livenessProbe", "readinessProbe"]:
        probe = container.get(probe_type, {})
        assert probe, f"Missing {probe_type} configuration"
        
        # Check httpGet config
        assert "httpGet" in probe, f"{probe_type} missing httpGet configuration"
        assert probe["httpGet"]["path"] == EXPECTED_PROBE_CONFIG["httpGet"]["path"], f"{probe_type} path expected {EXPECTED_PROBE_CONFIG['httpGet']['path']}, got {probe['httpGet']['path']}"
        assert probe["httpGet"]["port"] == EXPECTED_PROBE_CONFIG["httpGet"]["port"], f"{probe_type} port expected {EXPECTED_PROBE_CONFIG['httpGet']['port']}, got {probe['httpGet']['port']}"
        
        # Check timing parameters
        assert probe["initialDelaySeconds"] == EXPECTED_PROBE_CONFIG["initialDelaySeconds"], f"{probe_type} initialDelaySeconds expected {EXPECTED_PROBE_CONFIG['initialDelaySeconds']}, got {probe.get('initialDelaySeconds')}"
        assert probe["periodSeconds"] == EXPECTED_PROBE_CONFIG["periodSeconds"], f"{probe_type} periodSeconds expected {EXPECTED_PROBE_CONFIG['periodSeconds']}, got {probe.get('periodSeconds')}"
        assert probe["failureThreshold"] == EXPECTED_PROBE_CONFIG["failureThreshold"], f"{probe_type} failureThreshold expected {EXPECTED_PROBE_CONFIG['failureThreshold']}, got {probe.get('failureThreshold')}"

def test_ac4_security_context_hardening_configured():
    """AC-4: Deployment enforces security hardening via pod and container security contexts, with /tmp EmptyDir volume"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_spec = dep["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Check security context settings
    security_context = container.get("securityContext", {})
    for k, v in EXPECTED_SECURITY_CONTEXT.items():
        assert k in security_context, f"Missing securityContext setting {k}"
        assert security_context[k] == v, f"securityContext {k} expected {v}, got {security_context[k]}"
    
    # Check /tmp EmptyDir volume exists and is mounted
    volumes = pod_spec.get("volumes", [])
    tmp_volume_found = any(vol.get("emptyDir") is not None and vol.get("name") == "tmp" for vol in volumes)
    assert tmp_volume_found, "Missing emptyDir volume named tmp for /tmp mount"
    
    volume_mounts = container.get("volumeMounts", [])
    tmp_mount_found = any(mnt.get("mountPath") == "/tmp" and mnt.get("name") == "tmp" for mnt in volume_mounts)
    assert tmp_mount_found, "Missing volume mount for /tmp path"

def test_ac5_required_environment_variables_configured():
    """AC-5: Deployment container includes all required environment variables for service configuration"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_vars = [env["name"] for env in container.get("env", [])]
    
    for expected_env in EXPECTED_ENV_VARS:
        assert expected_env in env_vars, f"Missing required environment variable {expected_env}"

def test_ac6_observability_annotations_configured():
    """AC-6: Deployment pod template metadata includes required observability annotations for Prometheus scraping and OpenTelemetry instrumentation"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_annotations = dep["spec"]["template"]["metadata"].get("annotations", {})
    for k, v in EXPECTED_ANNOTATIONS.items():
        assert k in pod_annotations, f"Missing pod annotation {k}"
        assert pod_annotations[k] == v, f"Pod annotation {k} expected {v}, got {pod_annotations[k]}"

def test_ac7_standard_observability_labels_and_ports_configured():
    """AC-7: Deployment includes all standard observability labels on pod template, and exposes required ports 8080 and 9090"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_labels = dep["spec"]["template"]["metadata"].get("labels", {})
    # Check required metadata labels are also present on pod template
    for k, v in EXPECTED_METADATA_LABELS.items():
        assert k in pod_labels, f"Pod template missing label {k}"
        assert pod_labels[k] == v, f"Pod template label {k} expected {v}, got {pod_labels[k]}"
    
    # Check ports are exposed
    container = dep["spec"]["template"]["spec"]["containers"][0]
    ports = [p["containerPort"] for p in container.get("ports", [])]
    for expected_port in EXPECTED_PORTS:
        assert expected_port in ports, f"Missing exposed container port {expected_port}"
