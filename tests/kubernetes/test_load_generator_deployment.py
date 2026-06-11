#!/usr/bin/env python3
"""Integration tests for load-generator Kubernetes deployment (issue #2006)"""
import subprocess
import os
import pytest
import time

NAMESPACE = os.getenv("OTEL_DEMO_NAMESPACE", "opentelemetry-demo")
DEPLOYMENT_NAME = "load-generator"
POD_LABEL_SELECTOR = f"app.kubernetes.io/name={DEPLOYMENT_NAME}"
DEPLOYMENT_PATH = "kubernetes/load-generator/deployment.yaml"
NETWORK_POLICY_PATH = "kubernetes/load-generator/networkpolicy.yaml"

def run_kubectl(args, check=True, capture_output=True):
    """Helper to run kubectl commands"""
    cmd = ["kubectl", "-n", NAMESPACE] + args
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if check:
        result.check_returncode()
    return result

def get_running_pod_name():
    """Get the name of the first running load-generator pod"""
    result = run_kubectl([
        "get", "pods", "-l", POD_LABEL_SELECTOR,
        "--field-selector=status.phase=Running",
        "-o", "jsonpath={.items[0].metadata.name}"
    ])
    return result.stdout.strip()

class TestLoadGeneratorDeployment:
    def test_ac1_pod_starts_successfully(self):
        """AC-1: load-generator pod starts successfully and reports Running status within 60 seconds"""
        # Wait for deployment to be ready
        run_kubectl([
            "wait", "deployment", DEPLOYMENT_NAME,
            "--for=condition=Available",
            "--timeout=60s"
        ])
        
        # Verify at least one pod is running
        pod_name = get_running_pod_name()
        assert pod_name != "", "No running load-generator pod found"

    def test_ac2_resource_limits_set_correctly(self):
        """AC-2: CPU/memory requests and limits are set to correct values (100m/128Mi requests, 200m/256Mi limits)"""
        pod_name = get_running_pod_name()
        
        # Get resources from pod spec
        cpu_request = run_kubectl([
            "get", "pod", pod_name,
            "-o", "jsonpath={.spec.containers[0].resources.requests.cpu}"
        ]).stdout.strip()
        mem_request = run_kubectl([
            "get", "pod", pod_name,
            "-o", "jsonpath={.spec.containers[0].resources.requests.memory}"
        ]).stdout.strip()
        cpu_limit = run_kubectl([
            "get", "pod", pod_name,
            "-o", "jsonpath={.spec.containers[0].resources.limits.cpu}"
        ]).stdout.strip()
        mem_limit = run_kubectl([
            "get", "pod", pod_name,
            "-o", "jsonpath={.spec.containers[0].resources.limits.memory}"
        ]).stdout.strip()
        
        assert cpu_request == "100m", f"Expected CPU request 100m, got {cpu_request}"
        assert mem_request == "128Mi", f"Expected memory request 128Mi, got {mem_request}"
        assert cpu_limit == "200m", f"Expected CPU limit 200m, got {cpu_limit}"
        assert mem_limit == "256Mi", f"Expected memory limit 256Mi, got {mem_limit}"

    def test_ac3_security_context_configured(self):
        """AC-3: Pod runs as non-root user (UID 1000) with read-only filesystem and no privileged access"""
        pod_name = get_running_pod_name()
        
        # Check user and group ID
        id_result = run_kubectl(["exec", pod_name, "--", "id"])
        assert "uid=1000" in id_result.stdout, f"Expected UID 1000, got {id_result.stdout}"
        assert "gid=1000" in id_result.stdout, f"Expected GID 1000, got {id_result.stdout}"
        
        # Check read-only filesystem
        touch_result = run_kubectl(["exec", pod_name, "--", "touch", "/test.txt"], check=False)
        assert touch_result.returncode != 0, "Expected touch /test.txt to fail on read-only filesystem"
        assert "Read-only file system" in touch_result.stderr, f"Expected read-only error, got {touch_result.stderr}"

    def test_ac4_health_probes_function_correctly(self):
        """AC-4: Liveness and readiness probes return 200 OK on correct endpoints"""
        pod_name = get_running_pod_name()
        
        # Test liveness probe endpoint
        live_result = run_kubectl([
            "exec", pod_name, "--",
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "http://localhost:8080/health/live"
        ])
        assert live_result.stdout.strip() == "200", f"Liveness probe returned {live_result.stdout}, expected 200"
        
        # Test readiness probe endpoint
        ready_result = run_kubectl([
            "exec", pod_name, "--",
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "http://localhost:8080/health/ready"
        ])
        assert ready_result.stdout.strip() == "200", f"Readiness probe returned {ready_result.stdout}, expected 200"

    def test_ac5_environment_variables_configurable(self):
        """AC-5: All load generator tunables are configurable via environment variables"""
        pod_name = get_running_pod_name()
        
        # Get all expected env vars from pod spec
        env_vars = run_kubectl([
            "get", "pod", pod_name,
            "-o", "jsonpath={.spec.containers[0].env[*].name}"
        ]).stdout.strip().split()
        
        required_vars = [
            "LOCUST_WAIT_TIME_MIN",
            "LOCUST_WAIT_TIME_MAX",
            "LOCUST_TARGET_HOST",
            "LOCUST_USERS",
            "LOCUST_SPAWN_RATE"
        ]
        
        for var in required_vars:
            assert var in env_vars, f"Missing required environment variable {var}"

    def test_ac6_network_policy_enforces_egress_restrictions(self):
        """AC-6: Network policy restricts egress only to allowed services (frontend and DNS)"""
        pod_name = get_running_pod_name()
        
        # Test allowed egress to frontend service
        frontend_result = run_kubectl([
            "exec", pod_name, "--",
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "http://frontend:8080"
        ], check=False)
        assert frontend_result.returncode == 0, "Expected connection to frontend:8080 to succeed"
        assert frontend_result.stdout.strip() == "200", f"Expected 200 from frontend, got {frontend_result.stdout}"
        
        # Test denied egress to external site
        google_result = run_kubectl([
            "exec", pod_name, "--",
            "curl", "-m", "5", "http://google.com"
        ], check=False)
        assert google_result.returncode != 0, "Expected connection to google.com to fail"
        
        # Test denied egress to other internal service
        product_catalog_result = run_kubectl([
            "exec", pod_name, "--",
            "curl", "-m", "5", "http://productcatalogservice:3550"
        ], check=False)
        assert product_catalog_result.returncode != 0, "Expected connection to productcatalogservice to fail"
