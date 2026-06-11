"""
Integration tests for Kubernetes deployment of email service
Verifies all acceptance criteria from issue #2043 spec
"""
import os
import subprocess
import yaml
import time

EMAIL_MANIFEST_DIR = "./src/email/k8s/"
SERVICE_NAME = "email-service"
TEST_NAMESPACE = os.getenv("TEST_NAMESPACE", "otel-demo")
EXPECTED_LABELS = {
    "app.kubernetes.io/name": "email-service",
    "app.kubernetes.io/part-of": "opentelemetry-demo"
}

def run_cmd(cmd, shell=True, check=True):
    """Run shell command and return output"""
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {cmd}\nStderr: {result.stderr}\nStdout: {result.stdout}")
    return result

def load_all_manifests():
    """Load all YAML manifests from email k8s directory"""
    manifests = []
    if not os.path.exists(EMAIL_MANIFEST_DIR):
        raise Exception(f"Manifest directory {EMAIL_MANIFEST_DIR} not found")
    for filename in os.listdir(EMAIL_MANIFEST_DIR):
        if not filename.endswith(".yaml") and not filename.endswith(".yml"):
            continue
        filepath = os.path.join(EMAIL_MANIFEST_DIR, filename)
        with open(filepath, 'r') as f:
            docs = list(yaml.safe_load_all(f))
            for doc in docs:
                if doc:
                    manifests.append(doc)
    return manifests

def get_manifest_by_kind(kind, name=None):
    """Get manifest of specified kind, optionally matching name"""
    manifests = load_all_manifests()
    for doc in manifests:
        if doc.get("kind") == kind:
            if name is None or doc.get("metadata", {}).get("name") == name:
                return doc
    raise Exception(f"Manifest of kind {kind} {'with name ' + name if name else ''} not found")

def test_ac1_manifest_set_exists():
    """AC-1: Kubernetes manifests for ServiceAccount, Service, and Deployment exist in src/email/k8s directory"""
    required_resources = [
        ("ServiceAccount", "email-service"),
        ("Service", "email-service"),
        ("Deployment", "email-service")
    ]
    for kind, name in required_resources:
        manifest = get_manifest_by_kind(kind, name=name)
        assert manifest.get("metadata", {}).get("namespace") == "otel-demo", f"{kind} {name} has wrong namespace"

def test_ac2_probes_configured_correctly():
    """AC-2: Deployment has liveness/readiness probes with correct endpoints, ports and thresholds"""
    deploy = get_manifest_by_kind("Deployment", "email-service")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    # Verify liveness probe
    lp = container.get("livenessProbe", {})
    assert lp.get("httpGet", {}).get("path") == "/healthz", "Liveness probe path should be /healthz"
    assert lp.get("httpGet", {}).get("port") == 9090, "Liveness probe port should be 9090"
    assert lp.get("timeoutSeconds") == 5, "Liveness probe timeout should be 5s"
    assert lp.get("periodSeconds") == 10, "Liveness probe period should be 10s"
    assert lp.get("failureThreshold") == 3, "Liveness probe failure threshold should be 3"
    
    # Verify readiness probe
    rp = container.get("readinessProbe", {})
    assert rp.get("httpGet", {}).get("path") == "/readyz", "Readiness probe path should be /readyz"
    assert rp.get("httpGet", {}).get("port") == 9090, "Readiness probe port should be 9090"
    assert rp.get("timeoutSeconds") == 5, "Readiness probe timeout should be 5s"
    assert rp.get("periodSeconds") == 10, "Readiness probe period should be 10s"
    assert rp.get("failureThreshold") == 3, "Readiness probe failure threshold should be 3"

def test_ac3_resource_limits_configured():
    """AC-3: Deployment has correct CPU/memory requests and limits"""
    deploy = get_manifest_by_kind("Deployment", "email-service")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    resources = container.get("resources", {})
    assert resources["requests"].get("cpu") == "100m", "CPU request should be 100m"
    assert resources["limits"].get("cpu") == "500m", "CPU limit should be 500m"
    assert resources["requests"].get("memory") == "256Mi", "Memory request should be 256Mi"
    assert resources["limits"].get("memory") == "512Mi", "Memory limit should be 512Mi"

def test_ac4_security_context_hardened():
    """AC-4: Deployment security context meets all production hardening requirements"""
    deploy = get_manifest_by_kind("Deployment", "email-service")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    sc = container.get("securityContext", {})
    
    assert sc.get("runAsNonRoot") == True, "runAsNonRoot should be true"
    assert sc.get("runAsUser") == 1000, "runAsUser should be 1000"
    assert sc.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem should be true"
    assert sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation should be false"
    assert sc.get("capabilities", {}).get("drop") == ["ALL"], "All capabilities should be dropped"
    allowed_add_capabilities = {None, ["NET_BIND_SERVICE"]}
    assert sc.get("capabilities", {}).get("add") in allowed_add_capabilities, "Only NET_BIND_SERVICE allowed as added capability"
    assert sc.get("seccompProfile", {}).get("type") == "RuntimeDefault", "seccompProfile should be RuntimeDefault"

def test_ac5_env_vars_configurable():
    """AC-5: All required environment variables are configurable via ConfigMap/Secret references with correct defaults"""
    deploy = get_manifest_by_kind("Deployment", "email-service")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    env_vars = container.get("env", [])
    env_var_names = [e["name"] for e in env_vars]
    
    required_env_vars = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "OTEL_EXPORTER_OTLP_ENDPOINT"]
    for var in required_env_vars:
        assert var in env_var_names, f"Required environment variable {var} missing"
        # Verify sensitive variables come from Secrets, others from ConfigMap where appropriate
        var_def = next(e for e in env_vars if e["name"] == var)
        if var == "SMTP_PASSWORD":
            assert "valueFrom" in var_def and "secretKeyRef" in var_def["valueFrom"], f"{var} should be referenced from Secret"
    
    # Verify defaults for non-required variables
    default_vars = {
        "SMTP_PORT": "587",
        "SERVICE_PORT": "8080",
        "HEALTH_PORT": "9090",
        "LOG_LEVEL": "info"
    }
    for var, expected_default in default_vars.items():
        assert var in env_var_names, f"Environment variable {var} missing"
        var_def = next(e for e in env_vars if e["name"] == var)
        assert var_def.get("value") == expected_default or "valueFrom" in var_def, f"{var} should have default {expected_default} or be configurable"

def test_ac6_service_configured_correctly():
    """AC-6: Service exposes correct ports for gRPC and health checks as ClusterIP"""
    svc = get_manifest_by_kind("Service", "email-service")
    assert svc["spec"]["type"] == "ClusterIP", "Service type should be ClusterIP"
    
    ports = {p["name"]: p for p in svc["spec"]["ports"]}
    assert "grpc" in ports, "gRPC port not found"
    assert ports["grpc"]["port"] == 8080, "gRPC port should be 8080"
    assert ports["grpc"]["targetPort"] == 8080, "gRPC target port should be 8080"
    
    assert "health" in ports, "Health port not found"
    assert ports["health"]["port"] == 9090, "Health port should be 9090"
    assert ports["health"]["targetPort"] == 9090, "Health target port should be 9090"

def test_ac7_service_account_least_privilege():
    """AC-7: ServiceAccount has no elevated permissions or privileged annotations"""
    sa = get_manifest_by_kind("ServiceAccount", "email-service")
    annotations = sa.get("metadata", {}).get("annotations", {})
    # No privileged annotations allowed
    privileged_annotations = ["eks.amazonaws.com/role-arn", "iam.gke.io/gcp-service-account", "kubernetes.io/serviceaccount.io/name"]
    for ann in privileged_annotations:
        assert ann not in annotations, f"Privileged annotation {ann} not allowed on ServiceAccount"

def test_ac8_deployment_succeeds_with_valid_config():
    """AC-8: Deployment applies successfully to K8s 1.24+ cluster and pods reach Running state with passing probes"""
    if not os.getenv("RUN_INTEGRATION_TESTS"):
        import pytest
        pytest.skip("Skipping integration test, set RUN_INTEGRATION_TESTS=1 to run")
    try:
        # Apply manifests
        run_cmd(f"kubectl apply -f {EMAIL_MANIFEST_DIR} -n {TEST_NAMESPACE}")
        
        # Wait up to 60 seconds for pods to be running and ready
        start_time = time.time()
        all_ready = False
        while time.time() - start_time < 60:
            result = run_cmd(f"kubectl get pods -n {TEST_NAMESPACE} -l app.kubernetes.io/name=email-service -o jsonpath='{{.items[*].status.containerStatuses[0].ready}}'", check=False)
            if result.returncode == 0:
                ready_statuses = result.stdout.strip().split()
                if len(ready_statuses) >= 2 and all(s == "true" for s in ready_statuses):
                    all_ready = True
                    break
            time.sleep(2)
        
        assert all_ready, "At least 2 pods did not become ready within 60 seconds"
    finally:
        # Clean up
        run_cmd(f"kubectl delete -f {EMAIL_MANIFEST_DIR} -n {TEST_NAMESPACE}", check=False)
