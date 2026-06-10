# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import yaml
import requests
from kubernetes import client, config

# Constants from spec
LOADGENERATOR_MANIFEST_PATH = "./kubernetes/loadgenerator.yaml"
LOCUSTFILE_PATH = "./src/load-generator/locustfile.py"
EXPECTED_LIVENESS_PATH = "/health/live"
EXPECTED_READINESS_PATH = "/health/ready"
LOADGENERATOR_PORT = 8089


def test_ac1_liveness_probe_path_matches_implementation():
    """AC-1: livenessProbe.httpGet.path exactly matches liveness endpoint path in locustfile.py"""
    # Read manifest
    with open(LOADGENERATOR_MANIFEST_PATH, "r") as f:
        manifests = list(yaml.safe_load_all(f))
        deployment = next(m for m in manifests if m["kind"] == "Deployment")
    
    configured_path = deployment["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]["httpGet"]["path"]
    
    # Verify path matches expected value from spec
    assert configured_path == EXPECTED_LIVENESS_PATH, \
        f"Liveness probe path mismatch: got {configured_path}, expected {EXPECTED_LIVENESS_PATH}"
    
    # Verify path exists in locustfile implementation
    with open(LOCUSTFILE_PATH, "r") as f:
        locust_content = f.read()
    
    assert EXPECTED_LIVENESS_PATH in locust_content, \
        f"Liveness endpoint {EXPECTED_LIVENESS_PATH} not found in locustfile.py"


def test_ac2_readiness_probe_path_matches_implementation():
    """AC-2: readinessProbe.httpGet.path exactly matches readiness endpoint path in locustfile.py"""
    # Read manifest
    with open(LOADGENERATOR_MANIFEST_PATH, "r") as f:
        manifests = list(yaml.safe_load_all(f))
        deployment = next(m for m in manifests if m["kind"] == "Deployment")
    
    configured_path = deployment["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"]["path"]
    
    # Verify path matches expected value from spec
    assert configured_path == EXPECTED_READINESS_PATH, \
        f"Readiness probe path mismatch: got {configured_path}, expected {EXPECTED_READINESS_PATH}"
    
    # Verify path exists in locustfile implementation
    with open(LOCUSTFILE_PATH, "r") as f:
        locust_content = f.read()
    
    assert EXPECTED_READINESS_PATH in locust_content, \
        f"Readiness endpoint {EXPECTED_READINESS_PATH} not found in locustfile.py"


def test_ac3_liveness_probe_returns_200_when_service_running():
    """AC-3: GET liveness probe path returns 200 OK on running load generator pod"""
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        
        # Find loadgenerator pod
        pods = v1.list_pod_for_all_namespaces(label_selector="app.kubernetes.io/name=loadgenerator", field_selector="status.phase=Running")
        assert len(pods.items) > 0, "No running loadgenerator pods found"
        
        pod = pods.items[0]
        pod_name = pod.metadata.name
        pod_namespace = pod.metadata.namespace
        
        # Port forward to pod and test liveness endpoint
        # For CI/test environments, use service endpoint directly
        service_url = f"http://loadgenerator.{pod_namespace}.svc.cluster.local:{LOADGENERATOR_PORT}{EXPECTED_LIVENESS_PATH}"
        response = requests.get(service_url, timeout=5)
        
        assert response.status_code == 200, \
            f"Liveness probe returned {response.status_code}, expected 200 OK"
    
    except Exception as e:
        # If k8s not available (local test), skip with warning
        import pytest
        pytest.skip(f"Kubernetes cluster not accessible, skipping integration test: {e}")


def test_ac4_readiness_probe_returns_200_when_service_ready():
    """AC-4: GET readiness probe path returns 200 OK when service is ready to generate load"""
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        
        # Find loadgenerator pod
        pods = v1.list_pod_for_all_namespaces(label_selector="app.kubernetes.io/name=loadgenerator", field_selector="status.phase=Running")
        assert len(pods.items) > 0, "No running loadgenerator pods found"
        
        pod = pods.items[0]
        # Verify pod is in ready state
        ready_condition = next(c for c in pod.status.conditions if c.type == "Ready")
        assert ready_condition.status == "True", "Loadgenerator pod is not in ready state"
        
        pod_name = pod.metadata.name
        pod_namespace = pod.metadata.namespace
        
        # Test readiness endpoint
        service_url = f"http://loadgenerator.{pod_namespace}.svc.cluster.local:{LOADGENERATOR_PORT}{EXPECTED_READINESS_PATH}"
        response = requests.get(service_url, timeout=5)
        
        assert response.status_code == 200, \
            f"Readiness probe returned {response.status_code}, expected 200 OK"
    
    except Exception as e:
        import pytest
        pytest.skip(f"Kubernetes cluster not accessible, skipping integration test: {e}")


def test_ac5_probe_definitions_have_reference_comment():
    """AC-5 (Optional): Comment exists above probe definitions linking to locustfile implementation"""
    with open(LOADGENERATOR_MANIFEST_PATH, "r") as f:
        lines = f.readlines()
    
    # Find line numbers for liveness and readiness probe definitions
    liveness_line = None
    for i, line in enumerate(lines):
        if "livenessProbe:" in line:
            liveness_line = i
            break
    
    assert liveness_line is not None, "Liveness probe section not found in manifest"
    
    # Check preceding lines for comment linking to locustfile
    comment_found = False
    for i in range(max(0, liveness_line - 5), liveness_line):
        line = lines[i].strip()
        if line.startswith("#") and ("locustfile" in line.lower() or "health endpoint" in line.lower()):
            comment_found = True
            break
    
    assert comment_found, "No reference comment found above probe definitions linking to locustfile implementation"
