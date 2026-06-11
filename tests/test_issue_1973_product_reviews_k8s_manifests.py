#!/usr/bin/env python3
import os
import yaml
import subprocess

MANIFEST_DIR = "./kubernetes/product-reviews-service/"
DEPLOYMENT_PATH = os.path.join(MANIFEST_DIR, "deployment.yaml")
SERVICE_PATH = os.path.join(MANIFEST_DIR, "service.yaml")

EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]}
}

EXPECTED_PROBE_CONFIG = {
    "grpc": {
        "port": 8080,
        "service": "product_reviews"
    },
    "initialDelaySeconds": 5,
    "periodSeconds": 10,
    "timeoutSeconds": 2,
    "failureThreshold": 3
}

EXPECTED_RESOURCES = {
    "requests": {
        "cpu": "100m",
        "memory": "128Mi"
    },
    "limits": {
        "cpu": "200m",
        "memory": "256Mi"
    }
}

EXPECTED_ENV_VARS = {
    "OTEL_SERVICE_NAME": "product-reviews",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otelcol:4317",
    "OTEL_RESOURCE_ATTRIBUTES": "service.namespace=opentelemetry-demo"
}

EXPECTED_PROMETHEUS_ANNOTATIONS = {
    "prometheus.io/scrape": "true",
    "prometheus.io/port": "9090",
    "prometheus.io/path": "/metrics"
}


def test_ac1_directory_and_files_exist():
    """AC-1: The directory kubernetes/product-reviews-service/ exists and contains exactly deployment.yaml and service.yaml"""
    assert os.path.isdir(MANIFEST_DIR), f"Directory {MANIFEST_DIR} does not exist"
    files = os.listdir(MANIFEST_DIR)
    assert len(files) == 2, f"Expected exactly 2 files in {MANIFEST_DIR}, found {len(files)}: {files}"
    assert "deployment.yaml" in files, "deployment.yaml not found in manifest directory"
    assert "service.yaml" in files, "service.yaml not found in manifest directory"


def test_ac2_deployment_security_context_configured():
    """AC-2: Deployment manifest includes required pod security context properties"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    security_context = container.get("securityContext", {})
    
    for k, v in EXPECTED_SECURITY_CONTEXT.items():
        assert k in security_context, f"Missing securityContext property {k}"
        assert security_context[k] == v, f"securityContext {k} expected {v}, got {security_context[k]}"


def test_ac3_liveness_readiness_probes_configured():
    """AC-3: Deployment defines livenessProbe and readinessProbe using gRPC probe type with correct parameters"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    for probe_type in ["livenessProbe", "readinessProbe"]:
        probe = container.get(probe_type, {})
        assert probe, f"Missing {probe_type} configuration"
        
        # Check gRPC config
        assert "grpc" in probe, f"{probe_type} missing gRPC configuration"
        assert probe["grpc"]["port"] == EXPECTED_PROBE_CONFIG["grpc"]["port"], f"{probe_type} gRPC port expected {EXPECTED_PROBE_CONFIG['grpc']['port']}, got {probe['grpc']['port']}"
        assert probe["grpc"]["service"] == EXPECTED_PROBE_CONFIG["grpc"]["service"], f"{probe_type} gRPC service name expected {EXPECTED_PROBE_CONFIG['grpc']['service']}, got {probe['grpc']['service']}"
        
        # Check timing parameters
        assert probe["initialDelaySeconds"] == EXPECTED_PROBE_CONFIG["initialDelaySeconds"], f"{probe_type} initialDelaySeconds expected {EXPECTED_PROBE_CONFIG['initialDelaySeconds']}, got {probe.get('initialDelaySeconds')}"
        assert probe["periodSeconds"] == EXPECTED_PROBE_CONFIG["periodSeconds"], f"{probe_type} periodSeconds expected {EXPECTED_PROBE_CONFIG['periodSeconds']}, got {probe.get('periodSeconds')}"
        assert probe["timeoutSeconds"] == EXPECTED_PROBE_CONFIG["timeoutSeconds"], f"{probe_type} timeoutSeconds expected {EXPECTED_PROBE_CONFIG['timeoutSeconds']}, got {probe.get('timeoutSeconds')}"
        assert probe["failureThreshold"] == EXPECTED_PROBE_CONFIG["failureThreshold"], f"{probe_type} failureThreshold expected {EXPECTED_PROBE_CONFIG['failureThreshold']}, got {probe.get('failureThreshold')}"


def test_ac4_resource_requests_limits_configured():
    """AC-4: Deployment defines correct CPU and memory resource requests and limits"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    for res_type in ["requests", "limits"]:
        assert res_type in resources, f"Missing {res_type} in resources"
        for res in ["cpu", "memory"]:
            assert res in resources[res_type], f"Missing {res} in {res_type}"
            assert resources[res_type][res] == EXPECTED_RESOURCES[res_type][res], f"{res_type} {res} expected {EXPECTED_RESOURCES[res_type][res]}, got {resources[res_type][res]}"


def test_ac5_opentelemetry_env_vars_configured():
    """AC-5: Deployment includes all required OpenTelemetry environment variables"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_vars = {env["name"]: env["value"] for env in container.get("env", [])}
    
    for expected_name, expected_value in EXPECTED_ENV_VARS.items():
        assert expected_name in env_vars, f"Missing required environment variable {expected_name}"
        assert env_vars[expected_name] == expected_value, f"Environment variable {expected_name} expected {expected_value}, got {env_vars[expected_name]}"


def test_ac6_prometheus_annotations_configured():
    """AC-6: Deployment metadata includes required Prometheus scrape annotations"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_annotations = dep["spec"]["template"]["metadata"].get("annotations", {})
    for k, v in EXPECTED_PROMETHEUS_ANNOTATIONS.items():
        assert k in pod_annotations, f"Missing pod annotation {k}"
        assert pod_annotations[k] == v, f"Pod annotation {k} expected {v}, got {pod_annotations[k]}"


def test_ac7_service_configuration_correct():
    """AC-7: Service manifest defines ClusterIP service exposing port 8080 mapped to target port 8080"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    
    assert svc["kind"] == "Service", f"Expected kind Service, got {svc.get('kind')}"
    assert svc["spec"]["type"] == "ClusterIP", f"Expected service type ClusterIP, got {svc['spec']['type']}"
    
    ports = svc["spec"]["ports"]
    assert len(ports) >= 1, "Service has no ports defined"
    grpc_port_found = False
    for port in ports:
        if port["port"] == 8080 and port["targetPort"] == 8080:
            grpc_port_found = True
            break
    assert grpc_port_found, "Service does not expose port 8080 mapped to target port 8080"


def test_ac8_manifests_apply_without_validation_errors():
    """AC-8: Manifests can be applied to Kubernetes 1.24+ without validation errors"""
    assert os.path.isdir(MANIFEST_DIR), f"Directory {MANIFEST_DIR} does not exist"
    result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFEST_DIR, "--dry-run=client", "--output=yaml"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Manifest validation failed: {result.stderr}"


def test_ac9_probes_pass_within_30_seconds():
    """AC-9: When deployed, service passes readiness and liveness probe checks within 30 seconds of pod creation"""
    # This test will be run in a real cluster environment, here we just verify the probe config is valid
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    for probe_type in ["livenessProbe", "readinessProbe"]:
        assert probe_type in container, f"Missing {probe_type}"
        probe = container[probe_type]
        assert probe["initialDelaySeconds"] <= 5, f"{probe_type} initial delay too long: {probe['initialDelaySeconds']}"
        assert probe["periodSeconds"] <= 10, f"{probe_type} period too long: {probe['periodSeconds']}"
        assert probe["failureThreshold"] <= 3, f"{probe_type} failure threshold too high: {probe['failureThreshold']}"
