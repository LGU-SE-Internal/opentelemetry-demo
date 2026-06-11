#!/usr/bin/env python3
import subprocess
import pytest
import os
import time

NAMESPACE = "opentelemetry-demo"
SERVICE_NAME = "quote-service"
DEPLOYMENT_NAME = "quote-service"
HPA_NAME = "quote-service"
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "quote", "k8s")


def run_kubectl(cmd, namespace=True):
    base_cmd = ["kubectl"]
    if namespace:
        base_cmd.extend(["-n", NAMESPACE])
    base_cmd.extend(cmd.split())
    result = subprocess.run(base_cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def test_ac1_deployment_resource_requirements():
    """AC-1: Deployment manifest defines resource requests: CPU=100m, Memory=128Mi; resource limits: CPU=300m, Memory=256Mi"""
    # First create namespace if it doesn't exist
    run_kubectl(f"create namespace {NAMESPACE}", namespace=False)
    
    # Apply manifests first (will fail initially as no implementation exists)
    apply_rc, _, apply_stderr = run_kubectl(f"apply -f {MANIFEST_PATH}")
    assert apply_rc == 0, f"Failed to apply manifests: {apply_stderr}"
    
    # Verify requests
    req_cpu_rc, req_cpu, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.requests.cpu}}'")
    assert req_cpu_rc == 0, f"Could not get CPU request: {req_cpu}"
    assert req_cpu.strip() == "100m", f"CPU request is {req_cpu.strip()}, expected 100m"
    
    req_mem_rc, req_mem, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.requests.memory}}'")
    assert req_mem_rc == 0, f"Could not get memory request: {req_mem}"
    assert req_mem.strip() == "128Mi", f"Memory request is {req_mem.strip()}, expected 128Mi"
    
    # Verify limits
    lim_cpu_rc, lim_cpu, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.limits.cpu}}'")
    assert lim_cpu_rc == 0, f"Could not get CPU limit: {lim_cpu}"
    assert lim_cpu.strip() == "300m", f"CPU limit is {lim_cpu.strip()}, expected 300m"
    
    lim_mem_rc, lim_mem, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].resources.limits.memory}}'")
    assert lim_mem_rc == 0, f"Could not get memory limit: {lim_mem}"
    assert lim_mem.strip() == "256Mi", f"Memory limit is {lim_mem.strip()}, expected 256Mi"


def test_ac2_liveness_probe_config():
    """AC-2: Deployment includes liveness probe configured to send GET requests to /health endpoint on container port 8080, with initialDelaySeconds=5, periodSeconds=10, failureThreshold=3"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe}}'")
    assert get_rc == 0, f"Liveness probe not found: {get_stderr}"
    
    # Check HTTP GET /health on port 8080
    path_rc, path, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.httpGet.path}}'")
    assert path_rc == 0, "Not an HTTP liveness probe"
    assert path.strip() == "/health", f"Liveness probe path is {path.strip()}, expected /health"
    
    port_rc, port, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.httpGet.port}}'")
    assert port_rc == 0
    assert port.strip() == "8080", f"Liveness probe port is {port.strip()}, expected 8080"
    
    # Check parameters
    initial_delay_rc, initial_delay, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.initialDelaySeconds}}'")
    assert initial_delay_rc == 0
    assert initial_delay.strip() == "5", f"Initial delay is {initial_delay.strip()}, expected 5"
    
    period_rc, period, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.periodSeconds}}'")
    assert period_rc == 0
    assert period.strip() == "10", f"Period is {period.strip()}, expected 10"
    
    failure_rc, failure, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].livenessProbe.failureThreshold}}'")
    assert failure_rc == 0
    assert failure.strip() == "3", f"Failure threshold is {failure.strip()}, expected 3"


def test_ac3_readiness_probe_config():
    """AC-3: Deployment includes readiness probe configured to send GET requests to /health endpoint on container port 8080, with initialDelaySeconds=2, periodSeconds=5, failureThreshold=3"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe}}'")
    assert get_rc == 0, f"Readiness probe not found: {get_stderr}"
    
    # Check HTTP GET /health on port 8080
    path_rc, path, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.httpGet.path}}'")
    assert path_rc == 0, "Not an HTTP readiness probe"
    assert path.strip() == "/health", f"Readiness probe path is {path.strip()}, expected /health"
    
    port_rc, port, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.httpGet.port}}'")
    assert port_rc == 0
    assert port.strip() == "8080", f"Readiness probe port is {port.strip()}, expected 8080"
    
    # Check parameters
    initial_delay_rc, initial_delay, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.initialDelaySeconds}}'")
    assert initial_delay_rc == 0
    assert initial_delay.strip() == "2", f"Initial delay is {initial_delay.strip()}, expected 2"
    
    period_rc, period, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.periodSeconds}}'")
    assert period_rc == 0
    assert period.strip() == "5", f"Period is {period.strip()}, expected 5"
    
    failure_rc, failure, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].readinessProbe.failureThreshold}}'")
    assert failure_rc == 0
    assert failure.strip() == "3", f"Failure threshold is {failure.strip()}, expected 3"


def test_ac4_security_context_config():
    """AC-4: Deployment pod security context is set with: runAsNonRoot: true, runAsUser: 1000, readOnlyRootFilesystem: true, allowPrivilegeEscalation: false"""
    get_rc, _, get_stderr = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext}}'")
    assert get_rc == 0, f"Security context not found: {get_stderr}"
    
    non_root_rc, non_root, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.runAsNonRoot}}'")
    assert non_root_rc == 0
    assert non_root.strip().lower() == "true", "runAsNonRoot is not true"
    
    run_as_user_rc, run_as_user, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.runAsUser}}'")
    assert run_as_user_rc == 0
    assert run_as_user.strip() == "1000", f"runAsUser is {run_as_user.strip()}, expected 1000"
    
    read_only_rc, read_only, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.securityContext.readOnlyRootFilesystem}}'")
    assert read_only_rc == 0
    assert read_only.strip().lower() == "true", "readOnlyRootFilesystem is not true"
    
    allow_priv_esc_rc, allow_priv_esc, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation}}'")
    assert allow_priv_esc_rc == 0
    assert allow_priv_esc.strip().lower() == "false", "allowPrivilegeEscalation is not false"


def test_ac5_service_config():
    """AC-5: Service manifest exposes port 80 (ClusterIP type) targeting container port 8080, with selector matching quote-service pod labels"""
    get_rc, get_stdout, get_stderr = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec.type}} {{.spec.ports[0].port}} {{.spec.ports[0].targetPort}}'")
    assert get_rc == 0, f"Service {SERVICE_NAME} not found: {get_stderr}"
    
    output = get_stdout.strip().split()
    assert output[0] == "ClusterIP", f"Service type is {output[0]}, expected ClusterIP"
    assert output[1] == "80", f"Service port is {output[1]}, expected 80"
    assert output[2] == "8080", f"Service target port is {output[2]}, expected 8080"
    
    # Check selector matches quote-service labels
    selector_rc, selector, _ = run_kubectl(f"get service {SERVICE_NAME} -o jsonpath='{{.spec.selector.app}}'")
    assert selector_rc == 0
    assert selector.strip() == "quote-service", f"Service selector is {selector.strip()}, expected quote-service"


def test_ac6_hpa_config():
    """AC-6: HorizontalPodAutoscaler manifest is configured with minReplicas=2, maxReplicas=10, target CPU utilization threshold of 70%"""
    get_rc, _, get_stderr = run_kubectl(f"get hpa {HPA_NAME} -o jsonpath='{{.spec}}'")
    assert get_rc == 0, f"HPA {HPA_NAME} not found: {get_stderr}"
    
    min_replicas_rc, min_replicas, _ = run_kubectl(f"get hpa {HPA_NAME} -o jsonpath='{{.spec.minReplicas}}'")
    assert min_replicas_rc == 0
    assert min_replicas.strip() == "2", f"HPA minReplicas is {min_replicas.strip()}, expected 2"
    
    max_replicas_rc, max_replicas, _ = run_kubectl(f"get hpa {HPA_NAME} -o jsonpath='{{.spec.maxReplicas}}'")
    assert max_replicas_rc == 0
    assert max_replicas.strip() == "10", f"HPA maxReplicas is {max_replicas.strip()}, expected 10"
    
    cpu_target_rc, cpu_target, _ = run_kubectl(f"get hpa {HPA_NAME} -o jsonpath='{{.spec.metrics[0].resource.target.averageUtilization}}'")
    assert cpu_target_rc == 0
    assert cpu_target.strip() == "70", f"HPA CPU utilization target is {cpu_target.strip()}, expected 70%"


def test_ac7_manifest_validity():
    """AC-7: All manifests are valid Kubernetes YAML that can be applied with kubectl apply -f <path> without validation errors"""
    # First dry run apply to validate manifests without actually creating resources
    validate_rc, _, validate_stderr = run_kubectl(f"apply -f {MANIFEST_PATH} --dry-run=client")
    assert validate_rc == 0, f"Manifests are invalid: {validate_stderr}"
    
    # Also validate server-side dry run
    server_validate_rc, _, server_validate_stderr = run_kubectl(f"apply -f {MANIFEST_PATH} --dry-run=server")
    assert server_validate_rc == 0, f"Server-side validation failed: {server_validate_stderr}"


def test_ac8_pods_running_ready():
    """AC-8: After applying manifests, the quote-service pods reach Running state and are marked Ready within 60 seconds"""
    # Wait for deployment to be available
    rc, _, stderr = run_kubectl(f"wait deployment {DEPLOYMENT_NAME} --for=condition=available --timeout=60s")
    assert rc == 0, f"Deployment not ready within 60 seconds: {stderr}"
    
    # Check all pods are ready
    rc, ready_replicas, _ = run_kubectl(f"get deployment {DEPLOYMENT_NAME} -o jsonpath='{{.status.readyReplicas}}'")
    assert rc == 0
    assert int(ready_replicas.strip()) >= 2, f"Only {ready_replicas.strip()} ready replicas, expected at least 2"


def test_ac9_service_reachability():
    """AC-9: After applying manifests, the quote-service is reachable from another pod in the same namespace via curl http://quote-service/health returning HTTP 200 status"""
    # Run a temporary curl pod to test connectivity
    curl_cmd = f"run --rm -i --restart=Never curl-test --image=curlimages/curl -- curl -s -o /dev/null -w '%{{http_code}}' http://{SERVICE_NAME}/health"
    rc, http_code, stderr = run_kubectl(curl_cmd)
    assert rc == 0, f"Failed to run curl test: {stderr}"
    assert http_code.strip() == "200", f"Expected HTTP 200, got {http_code.strip()}"
