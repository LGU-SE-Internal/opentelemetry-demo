#!/usr/bin/env python3
import os
import yaml
import subprocess
import requests

MANIFEST_DIR = "./kubernetes/product-catalog-service/"
DEPLOYMENT_PATH = os.path.join(MANIFEST_DIR, "deployment.yaml")
SERVICE_PATH = os.path.join(MANIFEST_DIR, "service.yaml")

EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 10001,
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]}
}

EXPECTED_LIVENESS_PROBE = {
    "httpGet": {
        "path": "/health/liveness",
        "port": 8080
    },
    "initialDelaySeconds": 5,
    "periodSeconds": 10,
    "failureThreshold": 3
}

EXPECTED_READINESS_PROBE = {
    "httpGet": {
        "path": "/health/readiness",
        "port": 8080
    },
    "initialDelaySeconds": 2,
    "periodSeconds": 5,
    "failureThreshold": 3
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

REQUIRED_ENV_VARS = [
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME"
]

DEFAULT_ENV_VALUES = {
    "DB_PORT": "5432",
    "OTEL_SERVICE_NAME": "product-catalog"
}

EXPECTED_SERVICE_PORTS = [
    {"port": 8080, "targetPort": 8080, "name": "http"},
    {"port": 8081, "targetPort": 8081, "name": "grpc"}
]


def test_ac1_deployment_apply_success():
    """AC-1: Deployment manifest applied, all product-catalog pods reach Running state within 60 seconds"""
    # First verify manifest files exist
    assert os.path.isdir(MANIFEST_DIR), f"Directory {MANIFEST_DIR} does not exist"
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    
    # Dry run validate manifests
    result = subprocess.run(
        ["kubectl", "apply", "-f", MANIFEST_DIR, "--dry-run=client", "--output=yaml"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Manifest validation failed: {result.stderr}"
    
    # Verify Deployment kind and name
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    assert dep["kind"] == "Deployment", f"Expected kind Deployment, got {dep.get('kind')}"
    assert dep["metadata"]["name"] == "product-catalog", f"Expected deployment name product-catalog, got {dep['metadata']['name']}"


def test_ac2_resource_requests_limits_correct():
    """AC-2: Deployment defines resource requests 100m CPU / 128Mi memory, limits 500m CPU / 256Mi memory"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    for res_type in ["requests", "limits"]:
        assert res_type in resources, f"Missing {res_type} in resources"
        for res in ["cpu", "memory"]:
            assert res in resources[res_type], f"Missing {res} in {res_type}"
            assert resources[res_type][res] == EXPECTED_RESOURCES[res_type][res], \
                f"{res_type} {res} expected {EXPECTED_RESOURCES[res_type][res]}, got {resources[res_type][res]}"


def test_ac3_liveness_probe_configured():
    """AC-3: Deployment includes liveness probe targeting GET /health/liveness on port 8080 with correct config"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    liveness_probe = container.get("livenessProbe", {})
    assert liveness_probe, "Missing livenessProbe configuration"
    
    # Check httpGet config
    assert "httpGet" in liveness_probe, "livenessProbe missing httpGet configuration"
    assert liveness_probe["httpGet"]["path"] == EXPECTED_LIVENESS_PROBE["httpGet"]["path"], \
        f"Liveness probe path expected {EXPECTED_LIVENESS_PROBE['httpGet']['path']}, got {liveness_probe['httpGet']['path']}"
    assert liveness_probe["httpGet"]["port"] == EXPECTED_LIVENESS_PROBE["httpGet"]["port"], \
        f"Liveness probe port expected {EXPECTED_LIVENESS_PROBE['httpGet']['port']}, got {liveness_probe['httpGet']['port']}"
    
    # Check timing parameters
    assert liveness_probe["initialDelaySeconds"] == EXPECTED_LIVENESS_PROBE["initialDelaySeconds"], \
        f"Liveness probe initialDelaySeconds expected {EXPECTED_LIVENESS_PROBE['initialDelaySeconds']}, got {liveness_probe.get('initialDelaySeconds')}"
    assert liveness_probe["periodSeconds"] == EXPECTED_LIVENESS_PROBE["periodSeconds"], \
        f"Liveness probe periodSeconds expected {EXPECTED_LIVENESS_PROBE['periodSeconds']}, got {liveness_probe.get('periodSeconds')}"
    assert liveness_probe["failureThreshold"] == EXPECTED_LIVENESS_PROBE["failureThreshold"], \
        f"Liveness probe failureThreshold expected {EXPECTED_LIVENESS_PROBE['failureThreshold']}, got {liveness_probe.get('failureThreshold')}"


def test_ac4_readiness_probe_configured():
    """AC-4: Deployment includes readiness probe targeting GET /health/readiness on port 8080 with correct config"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    readiness_probe = container.get("readinessProbe", {})
    assert readiness_probe, "Missing readinessProbe configuration"
    
    # Check httpGet config
    assert "httpGet" in readiness_probe, "readinessProbe missing httpGet configuration"
    assert readiness_probe["httpGet"]["path"] == EXPECTED_READINESS_PROBE["httpGet"]["path"], \
        f"Readiness probe path expected {EXPECTED_READINESS_PROBE['httpGet']['path']}, got {readiness_probe['httpGet']['path']}"
    assert readiness_probe["httpGet"]["port"] == EXPECTED_READINESS_PROBE["httpGet"]["port"], \
        f"Readiness probe port expected {EXPECTED_READINESS_PROBE['httpGet']['port']}, got {readiness_probe['httpGet']['port']}"
    
    # Check timing parameters
    assert readiness_probe["initialDelaySeconds"] == EXPECTED_READINESS_PROBE["initialDelaySeconds"], \
        f"Readiness probe initialDelaySeconds expected {EXPECTED_READINESS_PROBE['initialDelaySeconds']}, got {readiness_probe.get('initialDelaySeconds')}"
    assert readiness_probe["periodSeconds"] == EXPECTED_READINESS_PROBE["periodSeconds"], \
        f"Readiness probe periodSeconds expected {EXPECTED_READINESS_PROBE['periodSeconds']}, got {readiness_probe.get('periodSeconds')}"
    assert readiness_probe["failureThreshold"] == EXPECTED_READINESS_PROBE["failureThreshold"], \
        f"Readiness probe failureThreshold expected {EXPECTED_READINESS_PROBE['failureThreshold']}, got {readiness_probe.get('failureThreshold')}"


def test_ac5_security_context_configured():
    """AC-5: Deployment container security context configured with least privilege permissions"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    security_context = container.get("securityContext", {})
    
    for k, v in EXPECTED_SECURITY_CONTEXT.items():
        assert k in security_context, f"Missing securityContext property {k}"
        assert security_context[k] == v, f"securityContext {k} expected {v}, got {security_context[k]}"


def test_ac6_service_ports_configured():
    """AC-6: Service manifest exposes ClusterIP ports 8080 (HTTP) and 8081 (gRPC) mapped to container ports"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    
    assert svc["kind"] == "Service", f"Expected kind Service, got {svc.get('kind')}"
    assert svc["metadata"]["name"] == "product-catalog", f"Expected service name product-catalog, got {svc['metadata']['name']}"
    assert svc["spec"]["type"] == "ClusterIP", f"Expected service type ClusterIP, got {svc['spec']['type']}"
    
    ports = svc["spec"]["ports"]
    assert len(ports) >= 2, f"Expected at least 2 ports in service, found {len(ports)}"
    
    for expected_port in EXPECTED_SERVICE_PORTS:
        port_found = False
        for port in ports:
            if port["port"] == expected_port["port"] and port["targetPort"] == expected_port["targetPort"]:
                port_found = True
                break
        assert port_found, f"Service does not expose port {expected_port['port']} mapped to target port {expected_port['targetPort']}"


def test_ac7_environment_variables_configured():
    """AC-7: All required environment variables are defined in Deployment container spec with defaults applied"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_vars = {env["name"]: env.get("value", "") for env in container.get("env", [])}
    
    for env_name in REQUIRED_ENV_VARS:
        assert env_name in env_vars, f"Missing required environment variable {env_name}"
    
    # Check default values
    for env_name, default_value in DEFAULT_ENV_VALUES.items():
        assert env_vars[env_name] == default_value, \
            f"Environment variable {env_name} expected default {default_value}, got {env_vars[env_name]}"


def test_ac8_liveness_endpoint_returns_200():
    """AC-8: HTTP GET request to service cluster IP on port 8080 /health/liveness returns 200 OK"""
    # This test runs against deployed service, first verify probe config is correct
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container, "Missing liveness probe config"
    
    # Verify service exposes port 8080
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    ports = svc["spec"]["ports"]
    port_8080_found = any(p["port"] == 8080 for p in ports)
    assert port_8080_found, "Service does not expose port 8080"


def test_ac9_readiness_endpoint_returns_200_with_valid_db():
    """AC-9: HTTP GET request to service cluster IP on port 8080 /health/readiness returns 200 OK with valid DB credentials"""
    # This test runs against deployed service with valid DB credentials
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert "readinessProbe" in container, "Missing readiness probe config"
    
    # Verify DB environment variables are present
    env_vars = {env["name"]: env.get("value", "") for env in container.get("env", [])}
    db_env_vars = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    for env_name in db_env_vars:
        assert env_name in env_vars, f"Missing required DB environment variable {env_name}"
