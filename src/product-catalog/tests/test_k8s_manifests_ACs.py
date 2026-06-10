#!/usr/bin/env python3
import subprocess
import pytest
import os
import time

NAMESPACE = "opentelemetry-demo"
SERVICE_ACCOUNT_NAME = "opentelemetry-demo-productcatalogservice"
SERVICE_NAME = "opentelemetry-demo-productcatalogservice"
DEPLOYMENT_NAME = "opentelemetry-demo-productcatalogservice"
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "..", "k8s")


def run_kubectl(cmd, namespace=True):
    base_cmd = ["kubectl"]
    if namespace:
        base_cmd.extend(["-n", NAMESPACE])
    base_cmd.extend(cmd.split())
    result = subprocess.run(base_cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def test_ac1_serviceaccount_created():
    """AC-1: ServiceAccount is created with automountServiceAccountToken = true"""
    # First check if namespace exists, create if needed
    run_kubectl(f"create namespace {NAMESPACE}", namespace=False)
    
    # Try to apply manifests first (they don't exist yet, so this will fail)
    apply_rc, _, apply_stderr = run_kubectl(f"apply -f {MANIFEST_PATH}")
    assert apply_rc == 0, f"Failed to apply manifests: {apply_stderr}"
    
    # Check service account exists
    get_rc, get_stdout, get_stderr = run_kubectl(f"get serviceaccount {SERVICE_ACCOUNT_NAME} -o jsonpath='{{.automountServiceAccountToken}}'")
    assert get_rc == 0, f"ServiceAccount {SERVICE_ACCOUNT_NAME} not found: {get_stderr}"
    assert get_stdout.strip() == "true", "automountServiceAccountToken is not set to true"


def test_ac2_service_created():
    """AC-2: ClusterIP Service exists exposing ports 8080 and 8081"""
    get_rc, get_stdout, get_stderr = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec.type}} {{.spec.ports[*].port}}'")
    assert get_rc == 0, f"Service {SERVICE_NAME} not found: {get_stderr}"
    
    output = get_stdout.strip().split()
    assert output[0] == "ClusterIP", "Service type is not ClusterIP"
    ports = output[1:]
    assert "8080" in ports, "Port 8080 not exposed"
    assert "8081" in ports, "Port 8081 not exposed"


def test_ac3_deployment_resource_requirements():
    """AC-3: Deployment has correct CPU/memory requests and limits"""
    get_rc, get_stdout, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources}}'")
    assert get_rc == 0, f"Deployment {DEPLOYMENT_NAME} not found: {get_stderr}"
    
    # Verify requests
    req_cpu_rc, req_cpu, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.requests.cpu}}'")
    assert req_cpu_rc == 0
    assert req_cpu.strip() == "100m", f"CPU request is {req_cpu.strip()}, expected 100m"
    
    req_mem_rc, req_mem, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.requests.memory}}'")
    assert req_mem_rc == 0
    assert req_mem.strip() == "128Mi", f"Memory request is {req_mem.strip()}, expected 128Mi"
    
    # Verify limits
    lim_cpu_rc, lim_cpu, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.limits.cpu}}'")
    assert lim_cpu_rc == 0
    assert lim_cpu.strip() == "200m", f"CPU limit is {lim_cpu.strip()}, expected 200m"
    
    lim_mem_rc, lim_mem, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.limits.memory}}'")
    assert lim_mem_rc == 0
    assert lim_mem.strip() == "256Mi", f"Memory limit is {lim_mem.strip()}, expected 256Mi"


def test_ac4_liveness_probe_config():
    """AC-4: Liveness probe targets gRPC port 8080 with correct parameters"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe}}'")
    assert get_rc == 0, f"Liveness probe not found: {get_stderr}"
    
    # Check gRPC probe on port 8080
    port_rc, port, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.grpc.port}}'")
    assert port_rc == 0, "Not a gRPC liveness probe"
    assert port.strip() == "8080", f"Liveness probe port is {port.strip()}, expected 8080"
    
    # Check parameters
    initial_delay_rc, initial_delay, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.initialDelaySeconds}}'")
    assert initial_delay_rc == 0
    assert initial_delay.strip() == "5", f"Initial delay is {initial_delay.strip()}, expected 5"
    
    period_rc, period, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.periodSeconds}}'")
    assert period_rc == 0
    assert period.strip() == "10", f"Period is {period.strip()}, expected 10"
    
    timeout_rc, timeout, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.timeoutSeconds}}'")
    assert timeout_rc == 0
    assert timeout.strip() == "1", f"Timeout is {timeout.strip()}, expected 1"
    
    failure_rc, failure, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.failureThreshold}}'")
    assert failure_rc == 0
    assert failure.strip() == "3", f"Failure threshold is {failure.strip()}, expected 3"


def test_ac5_readiness_probe_config():
    """AC-5: Readiness probe targets HTTP /health endpoint on port 8081 with correct parameters"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe}}'")
    assert get_rc == 0, f"Readiness probe not found: {get_stderr}"
    
    # Check HTTP GET /health on port 8081
    path_rc, path, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.httpGet.path}}'")
    assert path_rc == 0, "Not an HTTP readiness probe"
    assert path.strip() == "/health", f"Readiness probe path is {path.strip()}, expected /health"
    
    port_rc, port, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.httpGet.port}}'")
    assert port_rc == 0
    assert port.strip() == "8081", f"Readiness probe port is {port.strip()}, expected 8081"
    
    # Check parameters
    initial_delay_rc, initial_delay, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.initialDelaySeconds}}'")
    assert initial_delay_rc == 0
    assert initial_delay.strip() == "2", f"Initial delay is {initial_delay.strip()}, expected 2"
    
    period_rc, period, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.periodSeconds}}'")
    assert period_rc == 0
    assert period.strip() == "5", f"Period is {period.strip()}, expected 5"
    
    timeout_rc, timeout, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.timeoutSeconds}}'")
    assert timeout_rc == 0
    assert timeout.strip() == "1", f"Timeout is {timeout.strip()}, expected 1"
    
    failure_rc, failure, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.failureThreshold}}'")
    assert failure_rc == 0
    assert failure.strip() == "2", f"Failure threshold is {failure.strip()}, expected 2"


def test_ac6_pod_security_context():
    """AC-6: Pod security context configured with non-root, read-only fs, dropped capabilities"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext}}'")
    assert get_rc == 0, f"Security context not found: {get_stderr}"
    
    # Check non-root
    non_root_rc, non_root, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.runAsNonRoot}}'")
    assert non_root_rc == 0
    assert non_root.strip().lower() == "true", "runAsNonRoot is not true"
    
    run_as_user_rc, run_as_user, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.runAsUser}}'")
    assert run_as_user_rc == 0
    assert run_as_user.strip() == "1000", f"runAsUser is {run_as_user.strip()}, expected 1000"
    
    run_as_group_rc, run_as_group, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.runAsGroup}}'")
    assert run_as_group_rc == 0
    assert run_as_group.strip() == "1000", f"runAsGroup is {run_as_group.strip()}, expected 1000"
    
    read_only_rc, read_only, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.readOnlyRootFilesystem}}'")
    assert read_only_rc == 0
    assert read_only.strip().lower() == "true", "readOnlyRootFilesystem is not true"
    
    # Check dropped capabilities
    caps_rc, caps, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.capabilities.drop}}'")
    assert caps_rc == 0
    assert "ALL" in caps, "ALL capabilities are not dropped"


def test_ac7_environment_variables():
    """AC-7: All required runtime parameters exposed as environment variables"""
    required_env_vars = [
        "POSTGRES_CONNECTION_STRING",
        "TLS_ENABLED",
        "TLS_CERT_PATH",
        "TLS_KEY_PATH",
        "FEATURE_FLAG_ENDPOINT"
    ]
    
    for env_var in required_env_vars:
        rc, _, stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].env[?(@.name==\"{env_var}\")].name}}'")
        assert rc == 0, f"Environment variable {env_var} not found: {stderr}"


def test_ac8_standard_labels():
    """AC-8: All resources include standard observability labels"""
    required_labels = [
        "app.kubernetes.io/name=productcatalogservice",
        "app.kubernetes.io/part-of=opentelemetry-demo",
        "app.kubernetes.io/component=service"
    ]
    
    # Check service account
    for label in required_labels:
        key = label.split("=")[0]
        rc, value, _ = run_kubectl(f"get serviceaccount {SERVICE_ACCOUNT_NAME} -o jsonpath='{{.metadata.labels.{key.replace('.', '\\.')}}}'")
        assert rc == 0, f"Label {key} not found on ServiceAccount"
        assert value.strip() == label.split("=")[1], f"Label {key} value {value.strip()} does not match expected {label.split('=')[1]}"
    
    # Check service
    for label in required_labels:
        key = label.split("=")[0]
        rc, value, _ = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.metadata.labels.{key.replace('.', '\\.')}}}'")
        assert rc == 0, f"Label {key} not found on Service"
        assert value.strip() == label.split("=")[1], f"Label {key} value {value.strip()} does not match expected {label.split('=')[1]}"
    
    # Check deployment
    for label in required_labels:
        key = label.split("=")[0]
        rc, value, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.metadata.labels.{key.replace('.', '\\.')}}}'")
        assert rc == 0, f"Label {key} not found on Deployment"
        assert value.strip() == label.split("=")[1], f"Label {key} value {value.strip()} does not match expected {label.split('=')[1]}"


def test_ac9_pods_running_and_healthy():
    """AC-9: Pods reach Running state and pass probes within 30 seconds"""
    # Wait for deployment to be ready
    rc, _, stderr = run_kubectl(f"wait deployment {DEPLOYMENT_NAME} --for=condition=available --timeout=30s")
    assert rc == 0, f"Deployment not ready within 30 seconds: {stderr}"
    
    # Check all pods are ready
    rc, ready_replicas, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.status.readyReplicas}}'")
    assert rc == 0
    assert ready_replicas.strip() == "2", f"Only {ready_replicas.strip()} ready replicas, expected 2"
