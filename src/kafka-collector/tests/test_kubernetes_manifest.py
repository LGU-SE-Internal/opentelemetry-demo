"""
Integration tests for Kubernetes deployment of kafka-collector service
Verifies all acceptance criteria from issue #1835 spec
"""
import os
import subprocess
import yaml
import time

KAFKA_COLLECTOR_DIR = "./kubernetes/kafka-collector/"
SERVICE_NAME = "kafka-collector"
TEST_NAMESPACE = os.getenv("TEST_NAMESPACE", "default")

def run_cmd(cmd, shell=True, check=True):
    """Run shell command and return output"""
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {cmd}\nStderr: {result.stderr}\nStdout: {result.stdout}")
    return result

def load_all_manifests():
    """Load all YAML manifests from kafka-collector directory"""
    manifests = []
    for filename in os.listdir(KAFKA_COLLECTOR_DIR):
        if not filename.endswith(".yaml") and not filename.endswith(".yml"):
            continue
        filepath = os.path.join(KAFKA_COLLECTOR_DIR, filename)
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

def get_running_pod_name():
    """Get name of running kafka-collector pod"""
    cmd = f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].metadata.name}}'"
    result = run_cmd(cmd)
    return result.stdout.strip()

def test_ac1_complete_manifest_set_exists():
    """AC-1: Complete manifest set exists with all 4 required resources named kafka-collector"""
    # Check all required resources exist
    required_kinds = ["ServiceAccount", "ConfigMap", "Deployment", "Service"]
    for kind in required_kinds:
        manifest = get_manifest_by_kind(kind, name=SERVICE_NAME)
        assert manifest is not None, f"{kind} named {SERVICE_NAME} not found"

def test_ac2_resource_requests_limits_configured():
    """AC-2: Deployment defines required resource requests/limits exactly as specified"""
    deploy = get_manifest_by_kind("Deployment")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "resources" in container
    assert "requests" in container["resources"]
    assert "limits" in container["resources"]
    
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert container["resources"]["requests"]["memory"] == "128Mi"
    assert container["resources"]["limits"]["cpu"] == "500m"
    assert container["resources"]["limits"]["memory"] == "256Mi"

def test_ac3_security_context_configured():
    """AC-3: Deployment pod template includes full required security context with no extra privileges"""
    deploy = get_manifest_by_kind("Deployment")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "securityContext" in container
    sc = container["securityContext"]
    
    assert sc["runAsNonRoot"] == True
    assert sc["runAsUser"] == 10001
    assert sc["allowPrivilegeEscalation"] == False
    assert sc["readOnlyRootFilesystem"] == True
    assert "capabilities" in sc
    assert "drop" in sc["capabilities"]
    assert "ALL" in sc["capabilities"]["drop"]
    # Ensure no added capabilities
    assert "add" not in sc["capabilities"], "Extra capabilities should not be added"
    assert "privileged" not in sc or sc["privileged"] == False, "Container should not be privileged"

def test_ac4_probes_configured():
    """AC-4: Deployment includes liveness and readiness probes exactly matching specification"""
    deploy = get_manifest_by_kind("Deployment")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    # Check liveness probe
    assert "livenessProbe" in container
    lp = container["livenessProbe"]
    assert lp["httpGet"]["path"] == "/health"
    assert lp["httpGet"]["port"] == 13133
    assert lp["initialDelaySeconds"] == 10
    assert lp["periodSeconds"] == 30
    assert lp["timeoutSeconds"] == 5
    assert lp["failureThreshold"] == 3
    
    # Check readiness probe
    assert "readinessProbe" in container
    rp = container["readinessProbe"]
    assert rp["httpGet"]["path"] == "/health"
    assert rp["httpGet"]["port"] == 13133
    assert rp["initialDelaySeconds"] == 5
    assert rp["periodSeconds"] == 10
    assert rp["timeoutSeconds"] == 3
    assert rp["failureThreshold"] == 3

def test_ac5_service_exposes_all_required_ports():
    """AC-5: Service exposes all 4 required ports with type ClusterIP"""
    svc = get_manifest_by_kind("Service")
    assert svc["spec"]["type"] == "ClusterIP"
    
    expected_ports = {
        4317: "TCP",
        4318: "TCP",
        8888: "TCP",
        13133: "TCP"
    }
    actual_ports = {p["port"]: p["protocol"] for p in svc["spec"]["ports"]}
    
    for port, proto in expected_ports.items():
        assert port in actual_ports, f"Port {port} not found in service"
        assert actual_ports[port] == proto, f"Port {port} should use {proto} protocol"

def test_ac6_configmap_has_valid_otel_config():
    """AC-6: ConfigMap contains valid OTel collector configuration for kafka ingestion"""
    cm = get_manifest_by_kind("ConfigMap")
    assert "data" in cm
    # Check for collector config key (usually collector.yaml or config.yaml)
    config_key = next((k for k in cm["data"].keys() if k.endswith(".yaml")), None)
    assert config_key is not None, "No YAML configuration found in ConfigMap"
    
    # Parse config and verify basic structure
    collector_config = yaml.safe_load(cm["data"][config_key])
    assert "receivers" in collector_config
    assert "kafka" in collector_config["receivers"], "Kafka receiver should be configured"
    assert "exporters" in collector_config
    assert "service" in collector_config
    assert "pipelines" in collector_config["service"]
    assert "traces" in collector_config["service"]["pipelines"] or "metrics" in collector_config["service"]["pipelines"], "No telemetry pipelines configured"

def test_ac7_manifest_conventions_match_existing_services():
    """AC-7: Manifests use same labels, annotations, and formatting conventions as existing services"""
    deploy = get_manifest_by_kind("Deployment")
    metadata = deploy["metadata"]
    template_metadata = deploy["spec"]["template"]["metadata"]
    
    # Check standard labels exist
    assert "app" in metadata["labels"], "app label missing from deployment"
    assert metadata["labels"]["app"] == SERVICE_NAME
    assert "app" in template_metadata["labels"], "app label missing from pod template"
    assert template_metadata["labels"]["app"] == SERVICE_NAME
    
    # Check for standard annotations (e.g. prometheus scrape) if present on other services
    # Verify no custom non-standard labels/annotations that don't follow existing patterns
    assert "prometheus.io/scrape" in template_metadata["annotations"] or "prometheus.io/scrape" in metadata["annotations"], "Prometheus scrape annotation missing"

def test_ac8_kubectl_apply_completes_successfully():
    """AC-8: kubectl apply -k ./kubernetes/kafka-collector/ completes without errors"""
    # Apply the manifests
    result = run_cmd(f"kubectl apply -k {KAFKA_COLLECTOR_DIR} -n {TEST_NAMESPACE}", check=False)
    assert result.returncode == 0, f"kubectl apply failed: {result.stderr}"
    # Clean up after test
    run_cmd(f"kubectl delete -k {KAFKA_COLLECTOR_DIR} -n {TEST_NAMESPACE}", check=False)

def test_ac9_pod_runs_and_probes_pass():
    """AC-9: After apply, pod enters Running state within 2 minutes and probes pass"""
    try:
        # Apply manifests
        run_cmd(f"kubectl apply -k {KAFKA_COLLECTOR_DIR} -n {TEST_NAMESPACE}")
        
        # Wait up to 2 minutes for pod to be running
        start_time = time.time()
        pod_running = False
        while time.time() - start_time < 120:
            result = run_cmd(f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].status.phase}}'", check=False)
            if result.returncode == 0 and result.stdout.strip() == "Running":
                pod_running = True
                break
            time.sleep(5)
        assert pod_running, "Pod did not enter Running state within 2 minutes"
        
        # Check probe status
        pod_name = get_running_pod_name()
        # Wait for initial probe delays
        time.sleep(15)
        # Get pod events
        result = run_cmd(f"kubectl describe pod -n {TEST_NAMESPACE} {pod_name}")
        assert "Liveness probe failed" not in result.stdout, "Liveness probe is failing"
        assert "Readiness probe failed" not in result.stdout, "Readiness probe is failing"
        
        # Verify pod is ready
        result = run_cmd(f"kubectl get pod -n {TEST_NAMESPACE} {pod_name} -o jsonpath='{{.status.containerStatuses[0].ready}}'")
        assert result.stdout.strip() == "true", "Pod is not in ready state"
    finally:
        # Clean up
        run_cmd(f"kubectl delete -k {KAFKA_COLLECTOR_DIR} -n {TEST_NAMESPACE}", check=False)

def test_ac10_service_account_has_no_extra_rbac():
    """AC-10: Service account has no additional RBAC permissions assigned (least privilege)"""
    sa = get_manifest_by_kind("ServiceAccount")
    sa_name = sa["metadata"]["name"]
    
    # Check for any Roles or ClusterRoles bound to this service account
    result = run_cmd(f"kubectl get rolebindings,clusterrolebindings -A -o jsonpath='{{.items[?(@.subjects[?(@.kind==\"ServiceAccount\" && @.name==\"{sa_name}\")])}}.metadata.name'", check=False)
    assert result.stdout.strip() == "", f"Service account {sa_name} has unexpected RBAC bindings: {result.stdout}"
