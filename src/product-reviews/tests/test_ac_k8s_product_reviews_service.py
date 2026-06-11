"""
Integration tests for Kubernetes deployment of product-reviews service
Verifies all acceptance criteria from issue #2093 spec
"""
import os
import subprocess
import yaml
import time

PRODUCT_REVIEWS_MANIFEST_DIR = "./src/product-reviews/k8s/"
SERVICE_NAME = "product-reviews"
TEST_NAMESPACE = os.getenv("TEST_NAMESPACE", "otel-demo")
EXPECTED_LABELS = {
    "app.kubernetes.io/name": "product-reviews",
    "app.kubernetes.io/part-of": "opentelemetry-demo",
    "app.kubernetes.io/component": "backend"
}

def run_cmd(cmd, shell=True, check=True):
    """Run shell command and return output"""
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {cmd}\nStderr: {result.stderr}\nStdout: {result.stdout}")
    return result

def load_all_manifests():
    """Load all YAML manifests from product-reviews k8s directory"""
    manifests = []
    if not os.path.exists(PRODUCT_REVIEWS_MANIFEST_DIR):
        raise Exception(f"Manifest directory {PRODUCT_REVIEWS_MANIFEST_DIR} not found")
    for filename in os.listdir(PRODUCT_REVIEWS_MANIFEST_DIR):
        if not filename.endswith(".yaml") and not filename.endswith(".yml"):
            continue
        filepath = os.path.join(PRODUCT_REVIEWS_MANIFEST_DIR, filename)
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

def test_ac1_manifests_apply_successfully():
    """AC-1: When applying manifests to K8s 1.24+, all three resources are created without validation errors"""
    required_resources = [
        ("ServiceAccount", "product-reviews"),
        ("Service", "product-reviews"),
        ("Deployment", "product-reviews")
    ]
    # Verify all required manifests exist first
    for kind, name in required_resources:
        manifest = get_manifest_by_kind(kind, name=name)
        assert manifest, f"{kind} {name} manifest missing"
    
    # Test kubectl dry-run validation
    run_cmd(f"kubectl apply --dry-run=client -f {PRODUCT_REVIEWS_MANIFEST_DIR} -n {TEST_NAMESPACE}")
    run_cmd(f"kubectl apply --dry-run=server -f {PRODUCT_REVIEWS_MANIFEST_DIR} -n {TEST_NAMESPACE}")

def test_ac2_liveness_probe_configured_correctly():
    """AC-2: Deployment has livenessProbe pointing to /health/live port 8080 with correct parameters"""
    deploy = get_manifest_by_kind("Deployment", "product-reviews")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    lp = container.get("livenessProbe", {})
    assert lp.get("httpGet", {}).get("path") == "/health/live", "Liveness probe path must be /health/live"
    assert lp.get("httpGet", {}).get("port") == 8080, "Liveness probe port must be 8080"
    assert lp.get("initialDelaySeconds") == 5, "Liveness probe initialDelaySeconds must be 5"
    assert lp.get("periodSeconds") == 10, "Liveness probe periodSeconds must be 10"
    assert lp.get("timeoutSeconds") == 3, "Liveness probe timeoutSeconds must be 3"
    assert lp.get("failureThreshold") == 3, "Liveness probe failureThreshold must be 3"

def test_ac3_readiness_probe_configured_correctly():
    """AC-3: Deployment has readinessProbe pointing to /health/ready port 8080 with correct parameters"""
    deploy = get_manifest_by_kind("Deployment", "product-reviews")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    rp = container.get("readinessProbe", {})
    assert rp.get("httpGet", {}).get("path") == "/health/ready", "Readiness probe path must be /health/ready"
    assert rp.get("httpGet", {}).get("port") == 8080, "Readiness probe port must be 8080"
    assert rp.get("initialDelaySeconds") == 2, "Readiness probe initialDelaySeconds must be 2"
    assert rp.get("periodSeconds") == 5, "Readiness probe periodSeconds must be 5"
    assert rp.get("timeoutSeconds") == 3, "Readiness probe timeoutSeconds must be 3"
    assert rp.get("failureThreshold") == 3, "Readiness probe failureThreshold must be 3"

def test_ac4_resource_requests_limits_configured():
    """AC-4: Deployment has correct CPU/memory requests and limits"""
    deploy = get_manifest_by_kind("Deployment", "product-reviews")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    resources = container.get("resources", {})
    assert resources["requests"].get("cpu") == "100m", "CPU request must be 100m"
    assert resources["requests"].get("memory") == "128Mi", "Memory request must be 128Mi"
    assert resources["limits"].get("cpu") == "200m", "CPU limit must be 200m"
    assert resources["limits"].get("memory") == "256Mi", "Memory limit must be 256Mi"

def test_ac5_security_context_hardened():
    """AC-5: Pod security context meets all production hardening requirements"""
    deploy = get_manifest_by_kind("Deployment", "product-reviews")
    pod_spec = deploy["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Pod-level security context
    pod_sc = pod_spec.get("securityContext", {})
    assert pod_sc.get("runAsNonRoot") == True, "runAsNonRoot must be true"
    assert pod_sc.get("runAsUser") == 10001, "runAsUser must be 10001"
    assert pod_sc.get("runAsGroup") == 10001, "runAsGroup must be 10001"
    assert pod_sc.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem must be true"
    
    # Container-level security context
    container_sc = container.get("securityContext", {})
    assert container_sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation must be false"
    assert container_sc.get("capabilities", {}).get("drop") == ["ALL"], "All capabilities must be dropped"

def test_ac6_otel_environment_variables_configured():
    """AC-6: Deployment container includes required OTel environment variables with correct values"""
    deploy = get_manifest_by_kind("Deployment", "product-reviews")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    env_vars = container.get("env", [])
    env_var_map = {e["name"]: e.get("value") for e in env_vars}
    
    assert "OTEL_SERVICE_NAME" in env_var_map, "OTEL_SERVICE_NAME environment variable missing"
    assert env_var_map["OTEL_SERVICE_NAME"] == "product-reviews", "OTEL_SERVICE_NAME must be product-reviews"
    
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in env_var_map, "OTEL_EXPORTER_OTLP_ENDPOINT missing"
    assert env_var_map["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4317", "OTLP endpoint must point to otel-collector:4317"
    
    assert "OTEL_RESOURCE_ATTRIBUTES" in env_var_map, "OTEL_RESOURCE_ATTRIBUTES missing"

def test_ac7_service_prometheus_annotations_present():
    """AC-7: Service has correct Prometheus scraping annotations"""
    svc = get_manifest_by_kind("Service", "product-reviews")
    annotations = svc.get("metadata", {}).get("annotations", {})
    
    assert annotations.get("prometheus.io/scrape") == "true", "prometheus.io/scrape annotation must be true"
    assert annotations.get("prometheus.io/port") == "8080", "prometheus.io/port annotation must be 8080"
    assert annotations.get("prometheus.io/path") == "/metrics", "prometheus.io/path annotation must be /metrics"

def test_ac8_deployment_uses_correct_service_account():
    """AC-8: Deployment pod spec explicitly references product-reviews ServiceAccount"""
    deploy = get_manifest_by_kind("Deployment", "product-reviews")
    pod_spec = deploy["spec"]["template"]["spec"]
    
    assert pod_spec.get("serviceAccountName") == "product-reviews", "Deployment must use product-reviews ServiceAccount"

def test_ac9_all_resources_have_standard_labels():
    """AC-9: All three resources have standard OTel demo labels"""
    required_resources = [
        ("ServiceAccount", "product-reviews"),
        ("Service", "product-reviews"),
        ("Deployment", "product-reviews")
    ]
    
    for kind, name in required_resources:
        manifest = get_manifest_by_kind(kind, name=name)
        labels = manifest.get("metadata", {}).get("labels", {})
        for label_key, expected_value in EXPECTED_LABELS.items():
            assert labels.get(label_key) == expected_value, f"{kind} {name} missing expected label {label_key}={expected_value}"
        
        # Also check deployment pod template labels
        if kind == "Deployment":
            pod_labels = manifest["spec"]["template"]["metadata"]["labels"]
            for label_key, expected_value in EXPECTED_LABELS.items():
                assert pod_labels.get(label_key) == expected_value, f"Deployment pod template missing label {label_key}={expected_value}"

def test_ac10_service_routes_traffic_correctly():
    """AC-10: Service correctly routes traffic to running product-reviews pods, returns 200 OK for health endpoint"""
    if not os.getenv("RUN_INTEGRATION_TESTS"):
        import pytest
        pytest.skip("Skipping integration test, set RUN_INTEGRATION_TESTS=1 to run")
    try:
        # Apply manifests
        run_cmd(f"kubectl apply -f {PRODUCT_REVIEWS_MANIFEST_DIR} -n {TEST_NAMESPACE}")
        
        # Wait up to 60 seconds for pods to be ready
        start_time = time.time()
        all_ready = False
        while time.time() - start_time < 60:
            result = run_cmd(f"kubectl get pods -n {TEST_NAMESPACE} -l app.kubernetes.io/name=product-reviews -o jsonpath='{{.items[*].status.containerStatuses[0].ready}}'", check=False)
            if result.returncode == 0:
                ready_statuses = result.stdout.strip().split()
                if len(ready_statuses) >= 1 and all(s == "true" for s in ready_statuses):
                    all_ready = True
                    break
            time.sleep(2)
        assert all_ready, "Pods did not become ready within 60 seconds"
        
        # Test service connectivity from a temporary pod
        test_cmd = f"kubectl run -n {TEST_NAMESPACE} -i --rm --restart=Never test-connectivity --image=curlimages/curl:latest -- curl -s -o /dev/null -w '%{{http_code}}' http://product-reviews:8080/health/ready"
        result = run_cmd(test_cmd, check=False)
        assert result.stdout.strip() == "200", f"Expected 200 OK from service health endpoint, got {result.stdout.strip()}"
    finally:
        # Clean up
        run_cmd(f"kubectl delete -f {PRODUCT_REVIEWS_MANIFEST_DIR} -n {TEST_NAMESPACE}", check=False)
        run_cmd(f"kubectl delete pod -n {TEST_NAMESPACE} test-connectivity --ignore-not-found", check=False)
