#!/usr/bin/env python3
import subprocess
import pytest
import os
import time

NAMESPACE = "default"
SERVICE_NAME = "llm"
DEPLOYMENT_NAME = "llm"
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "..", "k8s")


def run_kubectl(cmd, namespace=True):
    base_cmd = ["kubectl"]
    if namespace:
        base_cmd.extend(["-n", NAMESPACE])
    base_cmd.extend(cmd.split())
    result = subprocess.run(base_cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def test_ac1_deployment_resource_requirements():
    """AC-1: Deployment has correct CPU/memory requests and limits: 100m/128Mi request, 500m/256Mi limit"""
    # First check if manifests directory exists
    assert os.path.exists(MANIFEST_PATH), f"K8s manifests directory {MANIFEST_PATH} does not exist"
    
    # Try to apply manifests
    apply_rc, _, apply_stderr = run_kubectl(f"apply -f {MANIFEST_PATH}")
    assert apply_rc == 0, f"Failed to apply manifests: {apply_stderr}"
    
    # Check resource requests
    req_cpu_rc, req_cpu, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.requests.cpu}}'")
    assert req_cpu_rc == 0
    assert req_cpu.strip() == "100m", f"CPU request is {req_cpu.strip()}, expected 100m"
    
    req_mem_rc, req_mem, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.requests.memory}}'")
    assert req_mem_rc == 0
    assert req_mem.strip() == "128Mi", f"Memory request is {req_mem.strip()}, expected 128Mi"
    
    # Check resource limits
    lim_cpu_rc, lim_cpu, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.limits.cpu}}'")
    assert lim_cpu_rc == 0
    assert lim_cpu.strip() == "500m", f"CPU limit is {lim_cpu.strip()}, expected 500m"
    
    lim_mem_rc, lim_mem, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.limits.memory}}'")
    assert lim_mem_rc == 0
    assert lim_mem.strip() == "256Mi", f"Memory limit is {lim_mem.strip()}, expected 256Mi"


def test_ac2_liveness_probe_config():
    """AC-2: Liveness probe targets HTTP GET /healthz on port 8000 with correct parameters: initialDelay 5s, period 10s, timeout 3s, failureThreshold 3"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe}}'")
    assert get_rc == 0, f"Liveness probe not found: {get_stderr}"
    
    # Check HTTP GET /healthz on port 8000
    path_rc, path, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.httpGet.path}}'")
    assert path_rc == 0, "Not an HTTP liveness probe"
    assert path.strip() == "/healthz", f"Liveness probe path is {path.strip()}, expected /healthz"
    
    port_rc, port, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.httpGet.port}}'")
    assert port_rc == 0
    assert port.strip() == "8000", f"Liveness probe port is {port.strip()}, expected 8000"
    
    # Check parameters
    initial_delay_rc, initial_delay, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.initialDelaySeconds}}'")
    assert initial_delay_rc == 0
    assert initial_delay.strip() == "5", f"Initial delay is {initial_delay.strip()}, expected 5"
    
    period_rc, period, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.periodSeconds}}'")
    assert period_rc == 0
    assert period.strip() == "10", f"Period is {period.strip()}, expected 10"
    
    timeout_rc, timeout, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.timeoutSeconds}}'")
    assert timeout_rc == 0
    assert timeout.strip() == "3", f"Timeout is {timeout.strip()}, expected 3"
    
    failure_rc, failure, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.failureThreshold}}'")
    assert failure_rc == 0
    assert failure.strip() == "3", f"Failure threshold is {failure.strip()}, expected 3"


def test_ac3_readiness_probe_config():
    """AC-3: Readiness probe targets HTTP GET /readyz on port 8000 with correct parameters: initialDelay 2s, period 5s, timeout 3s, failureThreshold 2"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe}}'")
    assert get_rc == 0, f"Readiness probe not found: {get_stderr}"
    
    # Check HTTP GET /readyz on port 8000
    path_rc, path, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.httpGet.path}}'")
    assert path_rc == 0, "Not an HTTP readiness probe"
    assert path.strip() == "/readyz", f"Readiness probe path is {path.strip()}, expected /readyz"
    
    port_rc, port, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.httpGet.port}}'")
    assert port_rc == 0
    assert port.strip() == "8000", f"Readiness probe port is {port.strip()}, expected 8000"
    
    # Check parameters
    initial_delay_rc, initial_delay, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.initialDelaySeconds}}'")
    assert initial_delay_rc == 0
    assert initial_delay.strip() == "2", f"Initial delay is {initial_delay.strip()}, expected 2"
    
    period_rc, period, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.periodSeconds}}'")
    assert period_rc == 0
    assert period.strip() == "5", f"Period is {period.strip()}, expected 5"
    
    timeout_rc, timeout, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.timeoutSeconds}}'")
    assert timeout_rc == 0
    assert timeout.strip() == "3", f"Timeout is {timeout.strip()}, expected 3"
    
    failure_rc, failure, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.failureThreshold}}'")
    assert failure_rc == 0
    assert failure.strip() == "2", f"Failure threshold is {failure.strip()}, expected 2"


def test_ac4_container_security_context():
    """AC-4: Container security context configured correctly: runAsNonRoot=true, runAsUser=1000, readOnlyRootFilesystem=true, allowPrivilegeEscalation=false, all capabilities dropped"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].securityContext}}'")
    assert get_rc == 0, f"Container security context not found: {get_stderr}"
    
    # Check non-root
    non_root_rc, non_root, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].securityContext.runAsNonRoot}}'")
    assert non_root_rc == 0
    assert non_root.strip().lower() == "true", "runAsNonRoot is not true"
    
    run_as_user_rc, run_as_user, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].securityContext.runAsUser}}'")
    assert run_as_user_rc == 0
    assert run_as_user.strip() == "1000", f"runAsUser is {run_as_user.strip()}, expected 1000"
    
    read_only_rc, read_only, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].securityContext.readOnlyRootFilesystem}}'")
    assert read_only_rc == 0
    assert read_only.strip().lower() == "true", "readOnlyRootFilesystem is not true"
    
    allow_priv_esc_rc, allow_priv_esc, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation}}'")
    assert allow_priv_esc_rc == 0
    assert allow_priv_esc.strip().lower() == "false", "allowPrivilegeEscalation is not false"
    
    # Check dropped capabilities
    caps_rc, caps, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].securityContext.capabilities.drop}}'")
    assert caps_rc == 0
    assert "ALL" in caps, "ALL capabilities are not dropped"


def test_ac5_environment_variables_exposed():
    """AC-5: All configurable service parameters are exposed as environment variables with correct default values"""
    required_env_vars = {
        "LLM_SERVICE_SHUTDOWN_TIMEOUT": "30",
        "RATE_LIMIT_MAX_REQUESTS": "100",
        "RATE_LIMIT_WINDOW_SECONDS": "60",
        "FLAGD_HOST": "flagd",
        "FLAGD_PORT": "8013",
        "OTEL_SERVICE_NAME": "llm-service",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otelcol:4317",
        "LOG_LEVEL": "info"
    }
    
    for env_var, expected_value in required_env_vars.items():
        rc, value, stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].env[?(@.name==\"{env_var}\")].value}}'")
        assert rc == 0, f"Environment variable {env_var} not found: {stderr}"
        assert value.strip() == expected_value, f"Environment variable {env_var} has value {value.strip()}, expected {expected_value}"


def test_ac6_service_configuration():
    """AC-6: Service is ClusterIP type, exposes port 8080, targets container port 8000, selector matches deployment labels"""
    get_rc, _, get_stderr = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec}}'")
    assert get_rc == 0, f"Service {SERVICE_NAME} not found: {get_stderr}"
    
    # Check service type is ClusterIP
    type_rc, type_val, _ = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec.type}}'")
    assert type_rc == 0
    assert type_val.strip() == "ClusterIP", f"Service type is {type_val.strip()}, expected ClusterIP"
    
    # Check port 8080 exposed
    port_rc, port_val, _ = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec.ports[0].port}}'")
    assert port_rc == 0
    assert port_val.strip() == "8080", f"Service port is {port_val.strip()}, expected 8080"
    
    # Check target port is 8000
    target_port_rc, target_port_val, _ = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec.ports[0].targetPort}}'")
    assert target_port_rc == 0
    assert target_port_val.strip() == "8000", f"Target port is {target_port_val.strip()}, expected 8000"
    
    # Check selector matches deployment labels
    selector_rc, selector_val, _ = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec.selector.\"app.kubernetes.io/name\"}}'")
    assert selector_rc == 0
    assert selector_val.strip() == "llm", f"Service selector app.kubernetes.io/name is {selector_val.strip()}, expected llm"


def test_ac7_deployment_running_and_accessible():
    """AC-7: At least 1 pod reaches Running/Ready status within 60 seconds, service is reachable at http://llm.default.svc.cluster.local:8080"""
    # Wait for deployment to be ready
    rc, _, stderr = run_kubectl(f"wait deployment {DEPLOYMENT_NAME} --for=condition=available --timeout=60s")
    assert rc == 0, f"Deployment not ready within 60 seconds: {stderr}"
    
    # Check at least 1 ready replica
    rc, ready_replicas, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.status.readyReplicas}}'")
    assert rc == 0
    assert int(ready_replicas.strip()) >= 1, f"Only {ready_replicas.strip()} ready replicas, expected at least 1"
    
    # Create a temporary pod to test service connectivity
    run_kubectl("run --rm -i --restart=Never test-llm-connect --image=curlimages/curl --command -- sleep 120")
    # Wait for test pod to be ready
    time.sleep(10)
    # Test connectivity to service
    connect_rc, _, connect_stderr = run_kubectl("exec test-llm-connect -- curl -s -o /dev/null -w '%{http_code}' http://llm.default.svc.cluster.local:8080/healthz")
    # Clean up test pod
    run_kubectl("delete pod test-llm-connect --grace-period=0 --force")
    assert connect_rc == 0, f"Failed to connect to llm service: {connect_stderr}"
