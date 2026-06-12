#!/usr/bin/env python3
import os
import yaml
import subprocess
import time
import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

MANIFEST_PATH = "/workspace/kubernetes/kafka-collector/deployment.yaml"
EXPECTED_ENV_VARS = {
    "KAFKA_BROKERS": {"type": str, "default": ""},
    "KAFKA_TOPICS": {"type": str, "default": ""},
    "KAFKA_TLS_ENABLED": {"type": bool, "default": False},
    "KAFKA_TLS_CA_CERT_PATH": {"type": str, "default": "/etc/ssl/certs/ca-certificates.crt"},
    "METRICS_PORT": {"type": int, "default": 9090},
    "HEALTH_PORT": {"type": int, "default": 8080}
}
EXPECTED_PROMETHEUS_ANNOTATIONS = {
    "prometheus.io/scrape": "true",
    "prometheus.io/port": "9090",
    "prometheus.io/path": "/metrics"
}

def get_deployment_content():
    assert os.path.exists(MANIFEST_PATH), f"Deployment manifest missing at {MANIFEST_PATH}"
    with open(MANIFEST_PATH, "r") as f:
        content = yaml.safe_load(f)
    assert content["kind"] == "Deployment", "Manifest is not a Deployment kind"
    return content

def test_ac1_resource_limits_requests():
    dep = get_deployment_content()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    
    # Check requests
    assert "resources" in container, "Resources section missing from container spec"
    assert "requests" in container["resources"], "Resource requests missing"
    assert "cpu" in container["resources"]["requests"], "CPU request missing"
    cpu_req = container["resources"]["requests"]["cpu"]
    assert int(cpu_req.rstrip("m")) >= 100, f"CPU request {cpu_req} is less than minimum 100m"
    assert "memory" in container["resources"]["requests"], "Memory request missing"
    mem_req = container["resources"]["requests"]["memory"]
    assert int(mem_req.rstrip("Mi")) >= 128, f"Memory request {mem_req} is less than minimum 128Mi"
    
    # Check limits
    assert "limits" in container["resources"], "Resource limits missing"
    assert "cpu" in container["resources"]["limits"], "CPU limit missing"
    cpu_limit = container["resources"]["limits"]["cpu"]
    assert int(cpu_limit.rstrip("m")) <= 500, f"CPU limit {cpu_limit} exceeds maximum 500m"
    assert "memory" in container["resources"]["limits"], "Memory limit missing"
    mem_limit = container["resources"]["limits"]["memory"]
    assert int(mem_limit.rstrip("Mi")) <= 256, f"Memory limit {mem_limit} exceeds maximum 256Mi"

def test_ac2_liveness_readiness_probes():
    dep = get_deployment_content()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    
    # Check liveness probe
    assert "livenessProbe" in container, "Liveness probe missing"
    assert "httpGet" in container["livenessProbe"], "Liveness probe is not HTTP GET"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz", "Liveness probe path incorrect"
    assert container["livenessProbe"]["httpGet"]["port"] == 8080, "Liveness probe port incorrect"
    assert container["livenessProbe"]["initialDelaySeconds"] == 10, "Liveness probe initialDelaySeconds incorrect"
    assert container["livenessProbe"]["periodSeconds"] == 30, "Liveness probe periodSeconds incorrect"
    assert container["livenessProbe"]["timeoutSeconds"] == 5, "Liveness probe timeoutSeconds incorrect"
    
    # Check readiness probe
    assert "readinessProbe" in container, "Readiness probe missing"
    assert "httpGet" in container["readinessProbe"], "Readiness probe is not HTTP GET"
    assert container["readinessProbe"]["httpGet"]["path"] == "/healthz", "Readiness probe path incorrect"
    assert container["readinessProbe"]["httpGet"]["port"] == 8080, "Readiness probe port incorrect"
    assert container["readinessProbe"]["initialDelaySeconds"] == 10, "Readiness probe initialDelaySeconds incorrect"
    assert container["readinessProbe"]["periodSeconds"] == 30, "Readiness probe periodSeconds incorrect"
    assert container["readinessProbe"]["timeoutSeconds"] == 5, "Readiness probe timeoutSeconds incorrect"

def test_ac3_security_context():
    dep = get_deployment_content()
    pod_spec = dep["spec"]["template"]["spec"]
    
    assert "securityContext" in pod_spec, "Pod security context missing"
    sec_ctx = pod_spec["securityContext"]
    assert sec_ctx["runAsNonRoot"] == True, "runAsNonRoot is not true"
    assert sec_ctx["runAsUser"] == 1000, "runAsUser is not 1000"
    assert sec_ctx["readOnlyRootFilesystem"] == True, "readOnlyRootFilesystem is not true"
    assert sec_ctx["allowPrivilegeEscalation"] == False, "allowPrivilegeEscalation is not false"
    assert "capabilities" in sec_ctx, "Capabilities section missing in security context"
    assert "drop" in sec_ctx["capabilities"], "Capabilities drop list missing"
    assert "ALL" in sec_ctx["capabilities"]["drop"], "ALL capabilities not dropped"

def test_ac4_environment_variables():
    dep = get_deployment_content()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert "env" in container, "Environment variables section missing"
    
    env_vars = {e["name"]: e.get("value", None) for e in container["env"]}
    
    for var_name, expected in EXPECTED_ENV_VARS.items():
        assert var_name in env_vars, f"Environment variable {var_name} missing"
        assert isinstance(env_vars[var_name], expected["type"]), f"Environment variable {var_name} has wrong type"
        assert env_vars[var_name] == expected["default"], f"Environment variable {var_name} has wrong default value"

def test_ac5_prometheus_annotations():
    dep = get_deployment_content()
    template_metadata = dep["spec"]["template"]["metadata"]
    assert "annotations" in template_metadata, "Pod template annotations missing"
    
    annotations = template_metadata["annotations"]
    for ann_name, expected_value in EXPECTED_PROMETHEUS_ANNOTATIONS.items():
        assert ann_name in annotations, f"Prometheus annotation {ann_name} missing"
        assert annotations[ann_name] == expected_value, f"Prometheus annotation {ann_name} has wrong value"

def test_ac6_valid_kubernetes_manifest():
    assert os.path.exists(MANIFEST_PATH), f"Deployment manifest missing at {MANIFEST_PATH}"
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-f", MANIFEST_PATH],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Manifest is invalid: {result.stderr}"
    assert "configured" in result.stdout or "created" in result.stdout, "Dry run failed to create deployment"

def test_ac7_pod_ready_status():
    # This test requires a running Kubernetes cluster with Kafka available
    pytest.skip("Skipping AC7 test in offline mode; requires active cluster deployment")
    
    # Uncomment when running against an active cluster:
    # config.load_kube_config()
    # api = client.CoreV1Api()
    # namespace = "default"
    #
    # # Deploy the manifest
    # subprocess.run(["kubectl", "apply", "-f", MANIFEST_PATH, "-n", namespace], check=True)
    #
    # # Wait up to 60s for pod to be ready
    # start_time = time.time()
    # while time.time() - start_time < 60:
    #     pods = api.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=kafka-collector")
    #     if len(pods.items) > 0:
    #         pod = pods.items[0]
    #         if pod.status.phase == "Running":
    #             for condition in pod.status.conditions:
    #                 if condition.type == "Ready" and condition.status == "True":
    #                     assert True, "Pod transitioned to Ready status within 60 seconds"
    #                     return
    #     time.sleep(2)
    # assert False, "Pod did not transition to Ready status within 60 seconds"
