#!/usr/bin/env python3
import os
import time
import yaml
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_PATH = "./kubernetes/jaeger-deployment.yaml"
SERVICE_PATH = "./kubernetes/jaeger-service.yaml"
EXPECTED_LABELS = {
    "app.kubernetes.io/name": "jaeger",
    "app.kubernetes.io/part-of": "opentelemetry-demo"
}

def test_ac1_deployment_exists_and_correct_labels():
    """AC-1: Jaeger Deployment manifest exists at ./kubernetes/jaeger-deployment.yaml and follows label conventions"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    assert dep["apiVersion"] == "apps/v1", "Deployment apiVersion should be apps/v1"
    assert dep["kind"] == "Deployment", "Kind should be Deployment"
    for k, v in EXPECTED_LABELS.items():
        assert dep["metadata"]["labels"].get(k) == v, f"Deployment missing label {k}: {v}"
        assert dep["spec"]["template"]["metadata"]["labels"].get(k) == v, f"Pod template missing label {k}: {v}"

def test_ac2_liveness_probe_configured():
    """AC-2: Deployment includes liveness probe pointing to Jaeger port 14269 with initial delay 5s, period 10s"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    liveness = container.get("livenessProbe", {})
    assert liveness, "Liveness probe not configured"
    assert liveness["httpGet"]["port"] == 14269, "Liveness probe should use port 14269"
    assert liveness["httpGet"]["path"] == "/", "Liveness probe should GET /"
    assert liveness["initialDelaySeconds"] == 5, "Liveness initial delay should be 5s"
    assert liveness["periodSeconds"] == 10, "Liveness period should be 10s"

def test_ac3_readiness_probe_configured():
    """AC-3: Deployment includes readiness probe pointing to Jaeger port 14269 with initial delay 5s, period 10s"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    readiness = container.get("readinessProbe", {})
    assert readiness, "Readiness probe not configured"
    assert readiness["httpGet"]["port"] == 14269, "Readiness probe should use port 14269"
    assert readiness["httpGet"]["path"] == "/", "Readiness probe should GET /"
    assert readiness["initialDelaySeconds"] == 5, "Readiness initial delay should be 5s"
    assert readiness["periodSeconds"] == 10, "Readiness period should be 10s"

def test_ac4_resource_requirements_set():
    """AC-4: Deployment has resource requests: 100m CPU / 256Mi memory, limits: 500m CPU / 512Mi memory"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert resources, "Resource requirements not configured"
    assert resources["requests"]["cpu"] == "100m", "CPU request should be 100m"
    assert resources["requests"]["memory"] == "256Mi", "Memory request should be 256Mi"
    assert resources["limits"]["cpu"] == "500m", "CPU limit should be 500m"
    assert resources["limits"]["memory"] == "512Mi", "Memory limit should be 512Mi"

def test_ac5_security_context_configured():
    """AC-5: Deployment security context runs as non-root user 10001, readOnlyRootFilesystem: true, allowPrivilegeEscalation: false"""
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        dep = yaml.safe_load(f)
    security_context = dep["spec"]["template"]["spec"]["containers"][0].get("securityContext", {})
    assert security_context, "Security context not configured"
    assert security_context["runAsNonRoot"] == True, "Should run as non-root user"
    assert security_context["runAsUser"] == 10001, "Should run as UID 10001"
    assert security_context["readOnlyRootFilesystem"] == True, "Root filesystem should be read-only"
    assert security_context["allowPrivilegeEscalation"] == False, "Privilege escalation should be disabled"

def test_ac6_service_exists_and_matches_selectors():
    """AC-6: Jaeger Service manifest exists at ./kubernetes/jaeger-service.yaml and matches Deployment label selectors"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    assert svc["apiVersion"] == "v1", "Service apiVersion should be v1"
    assert svc["kind"] == "Service", "Kind should be Service"
    for k, v in EXPECTED_LABELS.items():
        assert svc["metadata"]["labels"].get(k) == v, f"Service missing label {k}: {v}"
    selector = svc["spec"]["selector"]
    for k, v in EXPECTED_LABELS.items():
        assert selector.get(k) == v, f"Service selector does not match deployment label {k}: {v}"

def test_ac7_service_ports_configured():
    """AC-7: Service exposes ports 80 (UI), 4317 (OTLP gRPC), 4318 (OTLP HTTP) as ClusterIP type"""
    assert os.path.exists(SERVICE_PATH), f"Service file {SERVICE_PATH} not found"
    with open(SERVICE_PATH, "r") as f:
        svc = yaml.safe_load(f)
    assert svc["spec"]["type"] == "ClusterIP", "Service type should be ClusterIP"
    ports = {p["port"]: p for p in svc["spec"]["ports"]}
    assert 80 in ports, "Port 80 (UI) not exposed"
    assert ports[80]["targetPort"] == 16686, "Port 80 should target 16686"
    assert ports[80]["name"] == "http-ui", "Port 80 should be named http-ui"
    assert 4317 in ports, "Port 4317 (OTLP gRPC) not exposed"
    assert ports[4317]["targetPort"] == 4317, "Port 4317 should target 4317"
    assert ports[4317]["name"] == "otlp-grpc", "Port 4317 should be named otlp-grpc"
    assert 4318 in ports, "Port 4318 (OTLP HTTP) not exposed"
    assert ports[4318]["targetPort"] == 4318, "Port 4318 should target 4318"
    assert ports[4318]["name"] == "otlp-http", "Port 4318 should be named otlp-http"

def test_ac8_pod_running_after_deployment():
    """AC-8: When deployed in Kubernetes, kubectl get pods shows Jaeger pod in Running state after 60s"""
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = "default"  # Adjust if demo uses different namespace
    start = time.time()
    while time.time() - start < 60:
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=jaeger")
            if len(pods.items) > 0 and pods.items[0].status.phase == "Running":
                return
        except ApiException:
            pass
        time.sleep(5)
    assert False, "Jaeger pod not in Running state after 60s"

def test_ac9_ui_endpoint_returns_200():
    """AC-9: When port-forwarded to Jaeger UI service port 80, GET request returns 200 OK response"""
    # This test assumes port-forward is active to localhost:8080 -> service:80
    try:
        resp = requests.get("http://localhost:8080/", timeout=5)
        assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}"
    except requests.exceptions.RequestException as e:
        assert False, f"Failed to connect to Jaeger UI: {str(e)}"

def test_ac10_otlp_traces_visible_in_ui():
    """AC-10: When sending OTLP traces to service port 4317, traces are visible in Jaeger UI within 10s"""
    # This test uses opentelemetry-api to send a test trace
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    trace.set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer(__name__)
    exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
    trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(exporter))

    test_trace_id = None
    with tracer.start_as_current_span("test-jaeger-trace") as span:
        test_trace_id = format(span.get_span_context().trace_id, '032x')

    start = time.time()
    while time.time() - start < 10:
        try:
            resp = requests.get(f"http://localhost:8080/api/traces/{test_trace_id}", timeout=5)
            if resp.status_code == 200 and resp.json().get("data", {}).get("traceID") == test_trace_id:
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    assert False, f"Test trace {test_trace_id} not found in Jaeger UI after 10s"
