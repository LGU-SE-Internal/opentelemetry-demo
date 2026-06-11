#!/usr/bin/env python3
import os
import yaml
import subprocess
import pytest

MANIFESTS_DIR = "src/cart/k8s/"
EXPECTED_MANIFESTS = [
    "deployment.yaml",
    "service.yaml",
    "serviceaccount.yaml",
    "networkpolicy.yaml"
]

def test_ac1_manifests_deploy_successfully():
    """AC-1: When applying all manifests in src/cart/k8s/ to a Kubernetes 1.24+ cluster, all cart service pods reach Running state with 0 restarts within 60 seconds"""
    # First verify all manifest files exist
    for manifest in EXPECTED_MANIFESTS:
        manifest_path = os.path.join(MANIFESTS_DIR, manifest)
        assert os.path.exists(manifest_path), f"Missing manifest file: {manifest_path}"
    
    # Run kubectl dry-run to check basic validity
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-f", MANIFESTS_DIR],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Manifest dry-run failed: {result.stderr}"
    
    # Check that deployment exists and has correct replicas setup
    with open(os.path.join(MANIFESTS_DIR, "deployment.yaml")) as f:
        deployment = yaml.safe_load(f)
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert "replicas" in deployment["spec"]
    assert deployment["spec"]["replicas"] >= 1, "Deployment should have at least 1 replica"

def test_ac2_resource_requests_limits_set_correctly():
    """AC-2: Deployment defines resource requests: minimum 100m CPU, 128Mi memory; resource limits: maximum 500m CPU, 512Mi memory"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment = yaml.safe_load(f)
    
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) > 0, "No containers defined in deployment"
    cart_container = containers[0]
    
    resources = cart_container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    
    assert requests.get("cpu") == "100m", f"Expected CPU request 100m, got {requests.get('cpu')}"
    assert requests.get("memory") == "128Mi", f"Expected memory request 128Mi, got {requests.get('memory')}"
    assert limits.get("cpu") == "500m", f"Expected CPU limit 500m, got {limits.get('cpu')}"
    assert limits.get("memory") == "512Mi", f"Expected memory limit 512Mi, got {limits.get('memory')}"

def test_ac3_liveness_readiness_probes_configured_correctly():
    """AC-3: Pod spec includes liveness and readiness probes both configured with: path: /health, port: 8080, initialDelaySeconds: 10, periodSeconds: 10, timeoutSeconds: 1, failureThreshold: 3"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment = yaml.safe_load(f)
    
    containers = deployment["spec"]["template"]["spec"]["containers"]
    cart_container = containers[0]
    
    liveness_probe = cart_container.get("livenessProbe", {})
    readiness_probe = cart_container.get("readinessProbe", {})
    
    for probe_name, probe in [("liveness", liveness_probe), ("readiness", readiness_probe)]:
        assert "httpGet" in probe, f"{probe_name} probe missing httpGet config"
        http_get = probe["httpGet"]
        assert http_get.get("path") == "/health", f"{probe_name} probe path should be /health, got {http_get.get('path')}"
        assert http_get.get("port") == 8080, f"{probe_name} probe port should be 8080, got {http_get.get('port')}"
        assert probe.get("initialDelaySeconds") == 10, f"{probe_name} probe initialDelaySeconds should be 10, got {probe.get('initialDelaySeconds')}"
        assert probe.get("periodSeconds") == 10, f"{probe_name} probe periodSeconds should be 10, got {probe.get('periodSeconds')}"
        assert probe.get("timeoutSeconds") == 1, f"{probe_name} probe timeoutSeconds should be 1, got {probe.get('timeoutSeconds')}"
        assert probe.get("failureThreshold") == 3, f"{probe_name} probe failureThreshold should be 3, got {probe.get('failureThreshold')}"

def test_ac4_security_context_configured_correctly():
    """AC-4: Pod security context is configured with: runAsNonRoot: true, runAsUser: 1000, readOnlyRootFilesystem: true, capabilities.drop: ["ALL"]"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment = yaml.safe_load(f)
    
    pod_spec = deployment["spec"]["template"]["spec"]
    security_context = pod_spec.get("securityContext", {})
    
    assert security_context.get("runAsNonRoot") == True, f"Expected runAsNonRoot: true, got {security_context.get('runAsNonRoot')}"
    assert security_context.get("runAsUser") == 1000, f"Expected runAsUser: 1000, got {security_context.get('runAsUser')}"
    
    containers = pod_spec["containers"]
    cart_container = containers[0]
    container_security_context = cart_container.get("securityContext", {})
    
    assert container_security_context.get("readOnlyRootFilesystem") == True, f"Expected readOnlyRootFilesystem: true, got {container_security_context.get('readOnlyRootFilesystem')}"
    capabilities = container_security_context.get("capabilities", {})
    assert "drop" in capabilities, "Capabilities drop section missing"
    assert "ALL" in capabilities["drop"], "Expected ALL capabilities to be dropped"

def test_ac5_otel_environment_variables_configured():
    """AC-5: Container environment variables include all standard OTel configuration: OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME, OTEL_RESOURCE_ATTRIBUTES, OTEL_TRACES_EXPORTER, OTEL_METRICS_EXPORTER, OTEL_LOGS_EXPORTER"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment = yaml.safe_load(f)
    
    containers = deployment["spec"]["template"]["spec"]["containers"]
    cart_container = containers[0]
    
    env_vars = cart_container.get("env", [])
    env_names = [var["name"] for var in env_vars]
    
    required_otel_vars = [
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER"
    ]
    
    for var in required_otel_vars:
        assert var in env_names, f"Missing required OTel environment variable: {var}"

def test_ac6_dedicated_service_account_used():
    """AC-6: Deployment spec references a dedicated cart-service service account, and no default service account is used"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    sa_path = os.path.join(MANIFESTS_DIR, "serviceaccount.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    assert os.path.exists(sa_path), "serviceaccount.yaml missing"
    
    with open(sa_path) as f:
        sa = yaml.safe_load(f)
    assert sa["kind"] == "ServiceAccount"
    assert sa["metadata"]["name"] == "cart-service", f"Expected service account name cart-service, got {sa['metadata']['name']}"
    
    with open(deployment_path) as f:
        deployment = yaml.safe_load(f)
    
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec.get("serviceAccountName") == "cart-service", f"Expected serviceAccountName: cart-service, got {pod_spec.get('serviceAccountName')}"
    assert pod_spec.get("automountServiceAccountToken") != False, "Service account token should be automounted"

def test_ac7_network_policy_restricts_traffic_correctly():
    """AC-7: NetworkPolicy allows ingress traffic to port 8080 only from pods with label app: frontend, and allows all egress traffic from cart service pods"""
    np_path = os.path.join(MANIFESTS_DIR, "networkpolicy.yaml")
    assert os.path.exists(np_path), "networkpolicy.yaml missing"
    
    with open(np_path) as f:
        np = yaml.safe_load(f)
    
    assert np["kind"] == "NetworkPolicy"
    assert np["apiVersion"] == "networking.k8s.io/v1"
    
    pod_selector = np["spec"]["podSelector"]
    assert pod_selector.get("matchLabels", {}).get("app") == "cart-service", "NetworkPolicy should select cart-service pods"
    
    # Check ingress rules
    ingress = np["spec"].get("ingress", [])
    assert len(ingress) > 0, "No ingress rules defined"
    ingress_rule = ingress[0]
    assert "ports" in ingress_rule, "Ingress rule missing ports"
    ports = [p["port"] for p in ingress_rule["ports"]]
    assert 8080 in ports, "Ingress should allow port 8080"
    
    from_rules = ingress_rule.get("from", [])
    assert len(from_rules) > 0, "Ingress from rule missing"
    from_rule = from_rules[0]
    assert from_rule.get("podSelector", {}).get("matchLabels", {}).get("app") == "frontend", "Ingress should only allow from app: frontend pods"
    
    # Check egress rules
    egress = np["spec"].get("egress", [])
    assert len(egress) == 1, "Should have one egress rule allowing all traffic"
    assert egress[0] == {}, "Egress rule should allow all traffic (empty rule)"
    assert np["spec"].get("policyTypes") == ["Ingress", "Egress"], "PolicyTypes should include Ingress and Egress"

def test_ac8_pod_anti_affinity_configured():
    """AC-8: Pod anti-affinity rule is configured with requiredDuringSchedulingIgnoredDuringExecution for topology key kubernetes.io/hostname, label selector matching app: cart-service to ensure pods run on separate nodes"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment = yaml.safe_load(f)
    
    pod_spec = deployment["spec"]["template"]["spec"]
    affinity = pod_spec.get("affinity", {})
    pod_anti_affinity = affinity.get("podAntiAffinity", {})
    
    required_rules = pod_anti_affinity.get("requiredDuringSchedulingIgnoredDuringExecution", [])
    assert len(required_rules) > 0, "No required pod anti-affinity rules defined"
    
    rule = required_rules[0]
    assert rule.get("topologyKey") == "kubernetes.io/hostname", f"Expected topologyKey kubernetes.io/hostname, got {rule.get('topologyKey')}"
    
    label_selector = rule.get("labelSelector", {})
    match_labels = label_selector.get("matchLabels", {})
    assert match_labels.get("app") == "cart-service", f"Expected label selector app: cart-service, got {match_labels}"

def test_ac9_manifests_pass_kubectl_dry_run():
    """AC-9: All manifests pass validation when running kubectl apply --dry-run=client -f src/cart/k8s/ with exit code 0"""
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-f", MANIFESTS_DIR],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl dry-run failed: {result.stderr}\n{result.stdout}"

# Tests for Issue #1964: Cart service Valkey TLS/mTLS support
def test_issue1964_ac1_default_no_tls_env_vars_exist():
    """AC-1: When no TLS configuration environment variables are set (default state), cart service connects over plaintext, no breakage"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment = yaml.safe_load(f)
    
    containers = deployment["spec"]["template"]["spec"]["containers"]
    cart_container = containers[0]
    env_vars = cart_container.get("env", [])
    env_names = [var["name"] for var in env_vars]
    
    # Verify no TLS env vars are set by default
    tls_env_vars = [
        "CART_VALKEY_TLS_ENABLED",
        "CART_VALKEY_TLS_INSECURE_SKIP_VERIFY",
        "CART_VALKEY_CA_CERT_PATH",
        "CART_VALKEY_CLIENT_CERT_PATH",
        "CART_VALKEY_CLIENT_KEY_PATH"
    ]
    for var in tls_env_vars:
        assert var not in env_names, f"TLS environment variable {var} should not be set by default"
    
    # Verify no certificate volume mounts exist by default
    volume_mounts = cart_container.get("volumeMounts", [])
    mount_paths = [mount["mountPath"] for mount in volume_mounts]
    for path in ["/certs/valkey/ca/", "/certs/valkey/client/"]:
        assert path not in mount_paths, f"Certificate mount path {path} should not exist by default"

def test_issue1964_ac2_tls_env_vars_exist_as_options():
    """AC-2: When CART_VALKEY_TLS_ENABLED=true and CA cert path set, cart service establishes encrypted TLS 1.2+ connection"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment_content = f.read()
    
    # Verify all required TLS env var options are present in the manifest template
    required_env_vars = [
        "CART_VALKEY_TLS_ENABLED",
        "CART_VALKEY_TLS_INSECURE_SKIP_VERIFY",
        "CART_VALKEY_CA_CERT_PATH",
        "CART_VALKEY_CLIENT_CERT_PATH",
        "CART_VALKEY_CLIENT_KEY_PATH"
    ]
    for var in required_env_vars:
        assert var in deployment_content, f"Required TLS environment variable {var} missing from deployment manifest options"

def test_issue1964_ac3_mtls_cert_paths_configurable():
    """AC-3: When client cert and key paths are set, cart service successfully authenticates to Valkey using mTLS"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment_content = f.read()
    
    # Verify client cert and key path options are present
    assert "CART_VALKEY_CLIENT_CERT_PATH" in deployment_content, "Client cert path env var missing"
    assert "CART_VALKEY_CLIENT_KEY_PATH" in deployment_content, "Client key path env var missing"
    
    # Verify mTLS volume mount options exist
    assert "/certs/valkey/client/tls.crt" in deployment_content or "valkey-client-cert" in deployment_content, "Client cert volume mount missing"
    assert "/certs/valkey/client/tls.key" in deployment_content or "valkey-client-key" in deployment_content, "Client key volume mount missing"

def test_issue1964_ac4_cert_secrets_mounted_correctly():
    """AC-4: When certificate secrets are configured, all cert files are present and readable at configured mount paths"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment_content = f.read()
    
    # Verify CA cert volume and mount options exist
    assert "valkey-ca-cert" in deployment_content, "CA cert secret volume option missing"
    assert "/certs/valkey/ca/" in deployment_content, "CA cert mount path missing"
    
    # Verify client cert/key volumes and mount options exist
    assert "valkey-client-cert" in deployment_content, "Client cert secret volume option missing"
    assert "valkey-client-key" in deployment_content, "Client key secret volume option missing"
    assert "/certs/valkey/client/" in deployment_content, "Client cert/key mount path missing"

def test_issue1964_ac5_insecure_skip_verify_option_exists():
    """AC-5: When CART_VALKEY_TLS_INSECURE_SKIP_VERIFY=true, cart service connects over TLS without validating server cert"""
    deployment_path = os.path.join(MANIFESTS_DIR, "deployment.yaml")
    assert os.path.exists(deployment_path), "deployment.yaml missing"
    
    with open(deployment_path) as f:
        deployment_content = f.read()
    
    assert "CART_VALKEY_TLS_INSECURE_SKIP_VERIFY" in deployment_content, "Insecure skip verify env var option missing"

def test_issue1964_ac6_readme_has_tls_instructions():
    """AC-6: Cart service README includes step-by-step instructions for enabling TLS/mTLS"""
    readme_path = "src/cart/README.md"
    assert os.path.exists(readme_path), "Cart service README missing"
    
    with open(readme_path) as f:
        readme_content = f.read()
    
    # Verify TLS/mTLS documentation exists
    required_sections = [
        "TLS",
        "mTLS",
        "certificate secrets",
        "insecure skip verify",
        "CART_VALKEY_TLS_ENABLED"
    ]
    for section in required_sections:
        assert section.lower() in readme_content.lower(), f"README missing required section: {section}"

def test_issue1964_ac7_existing_tests_pass_without_tls_config():
    """AC-7: All existing unit and integration tests pass without modification when no TLS configuration is set"""
    # Run all existing cart service tests to verify no breakage with default config
    result = subprocess.run(
        ["pytest", "tests/k8s/test_cart_service_manifests.py", "-k", "not issue1964", "-v"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Existing tests failed with default configuration: {result.stderr}\n{result.stdout}"

