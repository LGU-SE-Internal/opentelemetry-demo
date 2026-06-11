#!/usr/bin/env python3
import os
import yaml

DEPLOYMENT_PATH = "./kubernetes/adservice.deployment.yaml"
SERVICE_PATH = "./kubernetes/adservice.service.yaml"
SERVICE_ACCOUNT_PATH = "./kubernetes/adservice.serviceaccount.yaml"

# Expected values from spec
EXPECTED_DEPLOYMENT_NAME = "adservice"
EXPECTED_SERVICE_NAME = "adservice"
EXPECTED_SERVICE_ACCOUNT_NAME = "adservice"
EXPECTED_REPLICAS = 2
EXPECTED_PROBE_PORT = 8080
EXPECTED_LIVENESS_PATH = "/liveness"
EXPECTED_READINESS_PATH = "/readiness"

EXPECTED_LIVENESS_PROBE_CONFIG = {
    "httpGet": {
        "path": EXPECTED_LIVENESS_PATH,
        "port": EXPECTED_PROBE_PORT
    },
    "initialDelaySeconds": 30,
    "periodSeconds": 10,
    "timeoutSeconds": 5,
    "failureThreshold": 3
}

EXPECTED_READINESS_PROBE_CONFIG = {
    "httpGet": {
        "path": EXPECTED_READINESS_PATH,
        "port": EXPECTED_PROBE_PORT
    },
    "initialDelaySeconds": 5,
    "periodSeconds": 5,
    "timeoutSeconds": 3,
    "failureThreshold": 2
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

EXPECTED_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {
        "drop": ["ALL"]
    },
    "seccompProfile": {
        "type": "RuntimeDefault"
    },
    "privileged": False
}

EXPECTED_ENV_VARS = [
    ("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otelcol:4317"),
    ("OTEL_SERVICE_NAME", "adservice"),
    ("OTEL_RESOURCE_ATTRIBUTES", "service.instance.id=")
]

EXPECTED_POD_LABELS = {
    "app.kubernetes.io/name": "adservice",
    "app.kubernetes.io/part-of": "opentelemetry-demo"
}


def test_ac1_deployment_replicas_labels_serviceaccount():
    """AC-1: Deployment has exactly 2 replicas, correct pod labels, and references adservice ServiceAccount"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    assert dep["metadata"]["name"] == EXPECTED_DEPLOYMENT_NAME, f"Expected deployment name {EXPECTED_DEPLOYMENT_NAME}, got {dep.get('metadata', {}).get('name')}"
    assert dep["spec"]["replicas"] == EXPECTED_REPLICAS, f"Expected {EXPECTED_REPLICAS} replicas, got {dep['spec'].get('replicas')}"
    
    # Check pod labels
    pod_labels = dep["spec"]["template"]["metadata"].get("labels", {})
    for k, v in EXPECTED_POD_LABELS.items():
        assert k in pod_labels, f"Missing pod label {k}"
        assert pod_labels[k] == v, f"Pod label {k} expected {v}, got {pod_labels[k]}"
    
    # Check service account reference
    assert dep["spec"]["template"]["spec"]["serviceAccountName"] == EXPECTED_SERVICE_ACCOUNT_NAME, f"Expected serviceAccountName {EXPECTED_SERVICE_ACCOUNT_NAME}, got {dep['spec']['template']['spec'].get('serviceAccountName')}"
    
    # Check service account exists
    assert os.path.exists(SERVICE_ACCOUNT_PATH), f"ServiceAccount file {SERVICE_ACCOUNT_PATH} not found"
    with open(SERVICE_ACCOUNT_PATH, "r") as f:
        sa = yaml.safe_load(f)
    assert sa["metadata"]["name"] == EXPECTED_SERVICE_ACCOUNT_NAME, f"Expected service account name {EXPECTED_SERVICE_ACCOUNT_NAME}, got {sa.get('metadata', {}).get('name')}"


def test_ac2_liveness_probe_configured_correctly():
    """AC-2: LivenessProbe sends GET to /liveness on port 8080 with correct timing parameters"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    liveness_probe = container.get("livenessProbe", {})
    assert liveness_probe, "Missing livenessProbe configuration"
    
    # Check httpGet config
    assert "httpGet" in liveness_probe, "livenessProbe missing httpGet configuration"
    assert liveness_probe["httpGet"]["path"] == EXPECTED_LIVENESS_PROBE_CONFIG["httpGet"]["path"], f"livenessProbe path expected {EXPECTED_LIVENESS_PROBE_CONFIG['httpGet']['path']}, got {liveness_probe['httpGet']['path']}"
    assert liveness_probe["httpGet"]["port"] == EXPECTED_LIVENESS_PROBE_CONFIG["httpGet"]["port"], f"livenessProbe port expected {EXPECTED_LIVENESS_PROBE_CONFIG['httpGet']['port']}, got {liveness_probe['httpGet']['port']}"
    
    # Check timing parameters
    assert liveness_probe["initialDelaySeconds"] == EXPECTED_LIVENESS_PROBE_CONFIG["initialDelaySeconds"], f"livenessProbe initialDelaySeconds expected {EXPECTED_LIVENESS_PROBE_CONFIG['initialDelaySeconds']}, got {liveness_probe.get('initialDelaySeconds')}"
    assert liveness_probe["periodSeconds"] == EXPECTED_LIVENESS_PROBE_CONFIG["periodSeconds"], f"livenessProbe periodSeconds expected {EXPECTED_LIVENESS_PROBE_CONFIG['periodSeconds']}, got {liveness_probe.get('periodSeconds')}"
    assert liveness_probe["timeoutSeconds"] == EXPECTED_LIVENESS_PROBE_CONFIG["timeoutSeconds"], f"livenessProbe timeoutSeconds expected {EXPECTED_LIVENESS_PROBE_CONFIG['timeoutSeconds']}, got {liveness_probe.get('timeoutSeconds')}"
    assert liveness_probe["failureThreshold"] == EXPECTED_LIVENESS_PROBE_CONFIG["failureThreshold"], f"livenessProbe failureThreshold expected {EXPECTED_LIVENESS_PROBE_CONFIG['failureThreshold']}, got {liveness_probe.get('failureThreshold')}"


def test_ac3_readiness_probe_configured_correctly():
    """AC-3: ReadinessProbe sends GET to /readiness on port 8080 with correct timing parameters"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    readiness_probe = container.get("readinessProbe", {})
    assert readiness_probe, "Missing readinessProbe configuration"
    
    # Check httpGet config
    assert "httpGet" in readiness_probe, "readinessProbe missing httpGet configuration"
    assert readiness_probe["httpGet"]["path"] == EXPECTED_READINESS_PROBE_CONFIG["httpGet"]["path"], f"readinessProbe path expected {EXPECTED_READINESS_PROBE_CONFIG['httpGet']['path']}, got {readiness_probe['httpGet']['path']}"
    assert readiness_probe["httpGet"]["port"] == EXPECTED_READINESS_PROBE_CONFIG["httpGet"]["port"], f"readinessProbe port expected {EXPECTED_READINESS_PROBE_CONFIG['httpGet']['port']}, got {readiness_probe['httpGet']['port']}"
    
    # Check timing parameters
    assert readiness_probe["initialDelaySeconds"] == EXPECTED_READINESS_PROBE_CONFIG["initialDelaySeconds"], f"readinessProbe initialDelaySeconds expected {EXPECTED_READINESS_PROBE_CONFIG['initialDelaySeconds']}, got {readiness_probe.get('initialDelaySeconds')}"
    assert readiness_probe["periodSeconds"] == EXPECTED_READINESS_PROBE_CONFIG["periodSeconds"], f"readinessProbe periodSeconds expected {EXPECTED_READINESS_PROBE_CONFIG['periodSeconds']}, got {readiness_probe.get('periodSeconds')}"
    assert readiness_probe["timeoutSeconds"] == EXPECTED_READINESS_PROBE_CONFIG["timeoutSeconds"], f"readinessProbe timeoutSeconds expected {EXPECTED_READINESS_PROBE_CONFIG['timeoutSeconds']}, got {readiness_probe.get('timeoutSeconds')}"
    assert readiness_probe["failureThreshold"] == EXPECTED_READINESS_PROBE_CONFIG["failureThreshold"], f"readinessProbe failureThreshold expected {EXPECTED_READINESS_PROBE_CONFIG['failureThreshold']}, got {readiness_probe.get('failureThreshold')}"


def test_ac4_resource_requests_limits_configured():
    """AC-4: Resource requests set to 100m CPU / 128Mi memory, limits set to 500m CPU / 256Mi memory"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    # Check requests
    assert "requests" in resources, "Missing resource requests configuration"
    assert resources["requests"]["cpu"] == EXPECTED_RESOURCES["requests"]["cpu"], f"CPU request expected {EXPECTED_RESOURCES['requests']['cpu']}, got {resources['requests'].get('cpu')}"
    assert resources["requests"]["memory"] == EXPECTED_RESOURCES["requests"]["memory"], f"Memory request expected {EXPECTED_RESOURCES['requests']['memory']}, got {resources['requests'].get('memory')}"
    
    # Check limits
    assert "limits" in resources, "Missing resource limits configuration"
    assert resources["limits"]["cpu"] == EXPECTED_RESOURCES["limits"]["cpu"], f"CPU limit expected {EXPECTED_RESOURCES['limits']['cpu']}, got {resources['limits'].get('cpu')}"
    assert resources["limits"]["memory"] == EXPECTED_RESOURCES["limits"]["memory"], f"Memory limit expected {EXPECTED_RESOURCES['limits']['memory']}, got {resources['limits'].get('memory')}"


def test_ac5_security_context_configured_correctly():
    """AC-5: Pod security context enforces non-root user, read-only root filesystem, dropped capabilities, RuntimeDefault seccomp profile"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    security_context = container.get("securityContext", {})
    
    assert security_context.get("runAsNonRoot") == EXPECTED_SECURITY_CONTEXT["runAsNonRoot"], f"Expected runAsNonRoot={EXPECTED_SECURITY_CONTEXT['runAsNonRoot']}, got {security_context.get('runAsNonRoot')}"
    assert security_context.get("runAsUser") == EXPECTED_SECURITY_CONTEXT["runAsUser"], f"Expected runAsUser={EXPECTED_SECURITY_CONTEXT['runAsUser']}, got {security_context.get('runAsUser')}"
    assert security_context.get("allowPrivilegeEscalation") == EXPECTED_SECURITY_CONTEXT["allowPrivilegeEscalation"], f"Expected allowPrivilegeEscalation={EXPECTED_SECURITY_CONTEXT['allowPrivilegeEscalation']}, got {security_context.get('allowPrivilegeEscalation')}"
    assert security_context.get("readOnlyRootFilesystem") == EXPECTED_SECURITY_CONTEXT["readOnlyRootFilesystem"], f"Expected readOnlyRootFilesystem={EXPECTED_SECURITY_CONTEXT['readOnlyRootFilesystem']}, got {security_context.get('readOnlyRootFilesystem')}"
    assert security_context.get("privileged") == EXPECTED_SECURITY_CONTEXT["privileged"], f"Expected privileged={EXPECTED_SECURITY_CONTEXT['privileged']}, got {security_context.get('privileged')}"
    
    # Check capabilities
    assert "capabilities" in security_context, "Missing capabilities in security context"
    assert "drop" in security_context["capabilities"], "Missing drop list in capabilities"
    assert "ALL" in security_context["capabilities"]["drop"], "ALL not found in capabilities.drop list"
    
    # Check seccomp profile
    assert "seccompProfile" in security_context, "Missing seccompProfile in security context"
    assert security_context["seccompProfile"]["type"] == EXPECTED_SECURITY_CONTEXT["seccompProfile"]["type"], f"Expected seccompProfile type {EXPECTED_SECURITY_CONTEXT['seccompProfile']['type']}, got {security_context['seccompProfile'].get('type')}"


def test_ac6_opentelemetry_environment_variables_configured():
    """AC-6: Deployment includes all required OpenTelemetry environment variables with correct values"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_vars = {env["name"]: env.get("value", "") for env in container.get("env", [])}
    
    for expected_name, expected_value in EXPECTED_ENV_VARS:
        assert expected_name in env_vars, f"Missing required environment variable {expected_name}"
        assert env_vars[expected_name].startswith(expected_value), f"Environment variable {expected_name} expected to start with '{expected_value}', got '{env_vars[expected_name]}'"


def test_ac7_service_configured_correctly():
    """AC-7: Service exposes port 8080 targeting container port 8080 with correct labeling scheme"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    
    assert svc["metadata"]["name"] == EXPECTED_SERVICE_NAME, f"Expected service name {EXPECTED_SERVICE_NAME}, got {svc.get('metadata', {}).get('name')}"
    
    # Check service labels
    service_labels = svc["metadata"].get("labels", {})
    for k, v in EXPECTED_POD_LABELS.items():
        assert k in service_labels, f"Missing service label {k}"
        assert service_labels[k] == v, f"Service label {k} expected {v}, got {service_labels[k]}"
    
    # Check ports
    ports = svc.get("spec", {}).get("ports", [])
    assert len(ports) >= 1, "No ports configured in service"
    assert ports[0]["port"] == EXPECTED_PROBE_PORT, f"Expected service port {EXPECTED_PROBE_PORT}, got {ports[0].get('port')}"
    assert ports[0]["targetPort"] == EXPECTED_PROBE_PORT, f"Expected target port {EXPECTED_PROBE_PORT}, got {ports[0].get('targetPort')}"
