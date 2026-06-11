import os
import subprocess
import yaml
import pytest
from kubernetes import client, config

# Paths to Kubernetes manifests
DEPLOYMENT_PATH = "./kubernetes/otel-collector/otel-collector-deployment.yaml"
PVC_PATH = "./kubernetes/otel-collector/otel-collector-pvc.yaml"
NETWORK_POLICY_PATH = "./kubernetes/otel-collector/otel-collector-networkpolicy.yaml"

# Load Kubernetes config
try:
    config.load_incluster_config()
except:
    config.load_kube_config()
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()
networking_v1 = client.NetworkingV1Api()

def test_ac1_resource_limits():
    """Test AC-1: Verify CPU/memory resource requests/limits are set correctly"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    containers = dep["spec"]["template"]["spec"]["containers"]
    otel_container = next(c for c in containers if c["name"] == "otel-collector")
    
    resources = otel_container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    
    assert requests.get("cpu") == "200m", "requests.cpu should be 200m"
    assert requests.get("memory") == "256Mi", "requests.memory should be 256Mi"
    assert limits.get("cpu") == "1", "limits.cpu should be 1"
    assert limits.get("memory") == "1Gi", "limits.memory should be 1Gi"

def test_ac2_security_context():
    """Test AC-2: Verify non-root security context is configured at pod and container level"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    pod_security_context = dep["spec"]["template"]["spec"].get("securityContext", {})
    assert pod_security_context.get("runAsNonRoot") == True, "runAsNonRoot should be true at pod level"
    assert pod_security_context.get("runAsUser") == 10001, "runAsUser should be 10001 at pod level"
    
    containers = dep["spec"]["template"]["spec"]["containers"]
    otel_container = next(c for c in containers if c["name"] == "otel-collector")
    container_security_context = otel_container.get("securityContext", {})
    
    assert container_security_context.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation should be false"
    assert container_security_context.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem should be true"
    assert "ALL" in container_security_context.get("capabilities", {}).get("drop", []), "ALL capabilities should be dropped"

def test_ac3_health_probes():
    """Test AC-3: Verify liveness and readiness probes are configured correctly"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    containers = dep["spec"]["template"]["spec"]["containers"]
    otel_container = next(c for c in containers if c["name"] == "otel-collector")
    
    liveness_probe = otel_container.get("livenessProbe", {})
    readiness_probe = otel_container.get("readinessProbe", {})
    
    # Check liveness probe
    assert liveness_probe.get("httpGet", {}).get("path") == "/health", "Liveness probe path should be /health"
    assert liveness_probe.get("httpGet", {}).get("port") == 13133, "Liveness probe port should be 13133"
    assert liveness_probe.get("initialDelaySeconds") == 30, "Liveness initialDelaySeconds should be 30"
    assert liveness_probe.get("periodSeconds") == 10, "Liveness periodSeconds should be 10"
    assert liveness_probe.get("timeoutSeconds") == 5, "Liveness timeoutSeconds should be 5"
    
    # Check readiness probe
    assert readiness_probe.get("httpGet", {}).get("path") == "/health", "Readiness probe path should be /health"
    assert readiness_probe.get("httpGet", {}).get("port") == 13133, "Readiness probe port should be 13133"
    assert readiness_probe.get("initialDelaySeconds") == 30, "Readiness initialDelaySeconds should be 30"
    assert readiness_probe.get("periodSeconds") == 10, "Readiness periodSeconds should be 10"
    assert readiness_probe.get("timeoutSeconds") == 5, "Readiness timeoutSeconds should be 5"

def test_ac4_pvc_and_volume_mount():
    """Test AC-4: Verify 10Gi PVC exists and is mounted to /var/lib/otelcol/buffer"""
    assert os.path.exists(PVC_PATH), f"PVC file {PVC_PATH} not found"
    
    with open(PVC_PATH, "r") as f:
        pvc = yaml.safe_load(f)
    
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi", "PVC storage should be 10Gi"
    
    # Check volume mount in deployment
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    
    containers = dep["spec"]["template"]["spec"]["containers"]
    otel_container = next(c for c in containers if c["name"] == "otel-collector")
    
    volume_mounts = otel_container.get("volumeMounts", [])
    buffer_mount = next((vm for vm in volume_mounts if vm["mountPath"] == "/var/lib/otelcol/buffer"), None)
    assert buffer_mount is not None, "Volume mount for /var/lib/otelcol/buffer not found"
    
    # Check volume exists in pod spec
    volumes = dep["spec"]["template"]["spec"].get("volumes", [])
    pvc_volume = next((v for v in volumes if v.get("persistentVolumeClaim", {}).get("claimName") == pvc["metadata"]["name"]), None)
    assert pvc_volume is not None, "PVC volume not found in deployment pod spec"

def test_ac5_network_policy():
    """Test AC-5: Verify NetworkPolicy restricts ingress/egress correctly"""
    assert os.path.exists(NETWORK_POLICY_PATH), f"NetworkPolicy file {NETWORK_POLICY_PATH} not found"
    
    with open(NETWORK_POLICY_PATH, "r") as f:
        np = yaml.safe_load(f)
    
    # Check ingress rules
    ingress_rules = np.get("spec", {}).get("ingress", [])
    assert len(ingress_rules) >= 2, "Should have at least 2 ingress rules: for app pods and monitoring pods"
    
    # Check egress rules
    egress_rules = np.get("spec", {}).get("egress", [])
    assert len(egress_rules) >= 3, "Should have at least 3 egress rules: jaeger, DNS, export endpoints"
    
    # Check port 4317/4318 allowed for app pods
    otlp_ports_found = False
    for rule in ingress_rules:
        ports = [p.get("port") for p in rule.get("ports", [])]
        if 4317 in ports and 4318 in ports:
            otlp_ports_found = True
            break
    assert otlp_ports_found, "Ingress for OTLP ports 4317/4318 not found"
    
    # Check port 8888 allowed for monitoring
    prom_port_found = False
    for rule in ingress_rules:
        ports = [p.get("port") for p in rule.get("ports", [])]
        if 8888 in ports:
            prom_port_found = True
            break
    assert prom_port_found, "Ingress for Prometheus port 8888 not found"
    
    # Check egress to 14250 (jaeger), 53 (DNS) allowed
    jaeger_port_found = False
    dns_port_found = False
    for rule in egress_rules:
        ports = [p.get("port") for p in rule.get("ports", [])]
        if 14250 in ports:
            jaeger_port_found = True
        if 53 in ports:
            dns_port_found = True
    assert jaeger_port_found, "Egress to Jaeger port 14250 not found"
    assert dns_port_found, "Egress to DNS port 53 not found"

def test_ac6_kube_linter_validation():
    """Test AC-6: Verify all manifests pass kube-linter validation with no critical/warning errors"""
    for path in [DEPLOYMENT_PATH, PVC_PATH, NETWORK_POLICY_PATH]:
        if not os.path.exists(path):
            pytest.skip(f"{path} not yet implemented")
    
    result = subprocess.run(
        ["kube-linter", "lint", DEPLOYMENT_PATH, PVC_PATH, NETWORK_POLICY_PATH, "--format", "json"],
        capture_output=True,
        text=True
    )
    
    import json
    output = json.loads(result.stdout)
    
    critical_errors = [v for v in output.get("Reports", []) if v["Severity"] == "Critical"]
    warning_errors = [v for v in output.get("Reports", []) if v["Severity"] == "Warning"]
    
    assert len(critical_errors) == 0, f"Found {len(critical_errors)} critical kube-linter errors: {[e['Message'] for e in critical_errors]}"
    assert len(warning_errors) == 0, f"Found {len(warning_errors)} warning kube-linter errors: {[e['Message'] for e in warning_errors]}"
