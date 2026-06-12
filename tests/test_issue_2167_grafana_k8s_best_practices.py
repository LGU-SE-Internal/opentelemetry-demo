#!/usr/bin/env python3
import os
import yaml
import pytest
from unittest import mock

# Default values from spec
DEFAULT_GRAFANA_CPU_REQUEST = "100m"
DEFAULT_GRAFANA_CPU_LIMIT = "500m"
DEFAULT_GRAFANA_MEMORY_REQUEST = "256Mi"
DEFAULT_GRAFANA_MEMORY_LIMIT = "1Gi"
DEFAULT_GRAFANA_LIVENESS_PROBE_INITIAL_DELAY = 30
DEFAULT_GRAFANA_LIVENESS_PROBE_PERIOD = 10
DEFAULT_GRAFANA_READINESS_PROBE_INITIAL_DELAY = 5
DEFAULT_GRAFANA_READINESS_PROBE_PERIOD = 5
DEFAULT_GRAFANA_RUN_AS_NON_ROOT = True
DEFAULT_GRAFANA_RUN_AS_USER = 472
DEFAULT_GRAFANA_ALLOW_PRIVILEGE_ESCALATION = False

DEPLOYMENT_PATH = "./kubernetes/grafana/deployment.yaml"


def load_deployment():
    assert os.path.exists(DEPLOYMENT_PATH), f"Deployment file {DEPLOYMENT_PATH} not found"
    with open(DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)


def test_ac1_resource_requests_limits_defaults():
    """AC-1: Grafana container spec includes CPU and memory requests/limits using default values when env vars are not set"""
    dep = load_deployment()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    
    assert "requests" in resources, "Resource requests missing"
    assert "limits" in resources, "Resource limits missing"
    
    assert resources["requests"].get("cpu") == DEFAULT_GRAFANA_CPU_REQUEST, f"Default CPU request should be {DEFAULT_GRAFANA_CPU_REQUEST}"
    assert resources["limits"].get("cpu") == DEFAULT_GRAFANA_CPU_LIMIT, f"Default CPU limit should be {DEFAULT_GRAFANA_CPU_LIMIT}"
    assert resources["requests"].get("memory") == DEFAULT_GRAFANA_MEMORY_REQUEST, f"Default memory request should be {DEFAULT_GRAFANA_MEMORY_REQUEST}"
    assert resources["limits"].get("memory") == DEFAULT_GRAFANA_MEMORY_LIMIT, f"Default memory limit should be {DEFAULT_GRAFANA_MEMORY_LIMIT}"


def test_ac1_resource_requests_limits_env_var_override():
    """AC-1: CPU and memory requests/limits are overridden by corresponding environment variables when set"""
    test_cpu_request = "200m"
    test_cpu_limit = "1000m"
    test_mem_request = "512Mi"
    test_mem_limit = "2Gi"
    
    with mock.patch.dict(os.environ, {
        "GRAFANA_CPU_REQUEST": test_cpu_request,
        "GRAFANA_CPU_LIMIT": test_cpu_limit,
        "GRAFANA_MEMORY_REQUEST": test_mem_request,
        "GRAFANA_MEMORY_LIMIT": test_mem_limit
    }):
        # Re-generate deployment with env vars set (assuming generation uses env vars)
        # This test assumes the deployment generation script reads env vars at render time
        dep = load_deployment()
        container = dep["spec"]["template"]["spec"]["containers"][0]
        resources = container.get("resources", {})
        
        assert resources["requests"].get("cpu") == test_cpu_request, f"CPU request should be overridden to {test_cpu_request}"
        assert resources["limits"].get("cpu") == test_cpu_limit, f"CPU limit should be overridden to {test_cpu_limit}"
        assert resources["requests"].get("memory") == test_mem_request, f"Memory request should be overridden to {test_mem_request}"
        assert resources["limits"].get("memory") == test_mem_limit, f"Memory limit should be overridden to {test_mem_limit}"


def test_ac2_liveness_probe_defaults():
    """AC-2: Grafana container includes liveness probe targeting /api/health:3000 with default delay and period values"""
    dep = load_deployment()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    liveness = container.get("livenessProbe", {})
    
    assert liveness, "Liveness probe not configured"
    assert liveness["httpGet"]["port"] == 3000, "Liveness probe should use port 3000"
    assert liveness["httpGet"]["path"] == "/api/health", "Liveness probe should target /api/health"
    assert liveness["initialDelaySeconds"] == DEFAULT_GRAFANA_LIVENESS_PROBE_INITIAL_DELAY, f"Default liveness initial delay should be {DEFAULT_GRAFANA_LIVENESS_PROBE_INITIAL_DELAY}"
    assert liveness["periodSeconds"] == DEFAULT_GRAFANA_LIVENESS_PROBE_PERIOD, f"Default liveness period should be {DEFAULT_GRAFANA_LIVENESS_PROBE_PERIOD}"
    assert liveness["httpGet"]["scheme"] == "HTTP", "Liveness probe should use HTTP"
    assert liveness["successThreshold"] == 1, "Liveness success threshold should be 1"
    assert liveness["failureThreshold"] == 3, "Liveness failure threshold should be 3"


def test_ac2_liveness_probe_env_var_override():
    """AC-2: Liveness probe initial delay and period are overridden by corresponding environment variables when set"""
    test_initial_delay = 60
    test_period = 30
    
    with mock.patch.dict(os.environ, {
        "GRAFANA_LIVENESS_PROBE_INITIAL_DELAY": str(test_initial_delay),
        "GRAFANA_LIVENESS_PROBE_PERIOD": str(test_period)
    }):
        dep = load_deployment()
        container = dep["spec"]["template"]["spec"]["containers"][0]
        liveness = container.get("livenessProbe", {})
        
        assert liveness["initialDelaySeconds"] == test_initial_delay, f"Liveness initial delay should be overridden to {test_initial_delay}"
        assert liveness["periodSeconds"] == test_period, f"Liveness period should be overridden to {test_period}"


def test_ac3_readiness_probe_defaults():
    """AC-3: Grafana container includes readiness probe targeting /api/health:3000 with default delay and period values"""
    dep = load_deployment()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    readiness = container.get("readinessProbe", {})
    
    assert readiness, "Readiness probe not configured"
    assert readiness["httpGet"]["port"] == 3000, "Readiness probe should use port 3000"
    assert readiness["httpGet"]["path"] == "/api/health", "Readiness probe should target /api/health"
    assert readiness["initialDelaySeconds"] == DEFAULT_GRAFANA_READINESS_PROBE_INITIAL_DELAY, f"Default readiness initial delay should be {DEFAULT_GRAFANA_READINESS_PROBE_INITIAL_DELAY}"
    assert readiness["periodSeconds"] == DEFAULT_GRAFANA_READINESS_PROBE_PERIOD, f"Default readiness period should be {DEFAULT_GRAFANA_READINESS_PROBE_PERIOD}"
    assert readiness["httpGet"]["scheme"] == "HTTP", "Readiness probe should use HTTP"
    assert readiness["successThreshold"] == 1, "Readiness success threshold should be 1"
    assert readiness["failureThreshold"] == 3, "Readiness failure threshold should be 3"


def test_ac3_readiness_probe_env_var_override():
    """AC-3: Readiness probe initial delay and period are overridden by corresponding environment variables when set"""
    test_initial_delay = 20
    test_period = 10
    
    with mock.patch.dict(os.environ, {
        "GRAFANA_READINESS_PROBE_INITIAL_DELAY": str(test_initial_delay),
        "GRAFANA_READINESS_PROBE_PERIOD": str(test_period)
    }):
        dep = load_deployment()
        container = dep["spec"]["template"]["spec"]["containers"][0]
        readiness = container.get("readinessProbe", {})
        
        assert readiness["initialDelaySeconds"] == test_initial_delay, f"Readiness initial delay should be overridden to {test_initial_delay}"
        assert readiness["periodSeconds"] == test_period, f"Readiness period should be overridden to {test_period}"


def test_ac4_pod_security_context_defaults():
    """AC-4: Pod spec has securityContext.runAsNonRoot set to default true and runAsUser set to default 472"""
    dep = load_deployment()
    pod_spec = dep["spec"]["template"]["spec"]
    security_context = pod_spec.get("securityContext", {})
    
    assert security_context, "Pod security context not configured"
    assert security_context["runAsNonRoot"] == DEFAULT_GRAFANA_RUN_AS_NON_ROOT, f"Default runAsNonRoot should be {DEFAULT_GRAFANA_RUN_AS_NON_ROOT}"
    assert security_context["runAsUser"] == DEFAULT_GRAFANA_RUN_AS_USER, f"Default runAsUser should be {DEFAULT_GRAFANA_RUN_AS_USER}"
    assert security_context["runAsGroup"] == DEFAULT_GRAFANA_RUN_AS_USER, f"Default runAsGroup should be {DEFAULT_GRAFANA_RUN_AS_USER}"


def test_ac4_pod_security_context_env_var_override():
    """AC-4: Pod security context values are overridden by corresponding environment variables when set"""
    test_run_as_non_root = False
    test_run_as_user = 1000
    
    with mock.patch.dict(os.environ, {
        "GRAFANA_RUN_AS_NON_ROOT": str(test_run_as_non_root).lower(),
        "GRAFANA_RUN_AS_USER": str(test_run_as_user)
    }):
        dep = load_deployment()
        pod_spec = dep["spec"]["template"]["spec"]
        security_context = pod_spec.get("securityContext", {})
        
        assert security_context["runAsNonRoot"] == test_run_as_non_root, f"runAsNonRoot should be overridden to {test_run_as_non_root}"
        assert security_context["runAsUser"] == test_run_as_user, f"runAsUser should be overridden to {test_run_as_user}"


def test_ac5_container_security_context_defaults():
    """AC-5: Grafana container spec has securityContext.allowPrivilegeEscalation set to default false"""
    dep = load_deployment()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    security_context = container.get("securityContext", {})
    
    assert security_context, "Container security context not configured"
    assert security_context["allowPrivilegeEscalation"] == DEFAULT_GRAFANA_ALLOW_PRIVILEGE_ESCALATION, f"Default allowPrivilegeEscalation should be {DEFAULT_GRAFANA_ALLOW_PRIVILEGE_ESCALATION}"
    assert security_context.get("privileged", False) == False, "Container should not run in privileged mode by default"
    assert security_context.get("readOnlyRootFilesystem", True) == True, "Root filesystem should be read-only by default"


def test_ac5_container_security_context_env_var_override():
    """AC-5: Container securityContext.allowPrivilegeEscalation is overridden by environment variable when set"""
    test_allow_privilege_escalation = True
    
    with mock.patch.dict(os.environ, {
        "GRAFANA_ALLOW_PRIVILEGE_ESCALATION": str(test_allow_privilege_escalation).lower()
    }):
        dep = load_deployment()
        container = dep["spec"]["template"]["spec"]["containers"][0]
        security_context = container.get("securityContext", {})
        
        assert security_context["allowPrivilegeEscalation"] == test_allow_privilege_escalation, f"allowPrivilegeEscalation should be overridden to {test_allow_privilege_escalation}"


def test_ac6_all_parameters_env_configurable():
    """AC-6: All configuration parameters can be overridden by setting corresponding environment variables"""
    # Test all env vars together
    test_env = {
        "GRAFANA_CPU_REQUEST": "150m",
        "GRAFANA_CPU_LIMIT": "750m",
        "GRAFANA_MEMORY_REQUEST": "384Mi",
        "GRAFANA_MEMORY_LIMIT": "1.5Gi",
        "GRAFANA_LIVENESS_PROBE_INITIAL_DELAY": "45",
        "GRAFANA_LIVENESS_PROBE_PERIOD": "15",
        "GRAFANA_READINESS_PROBE_INITIAL_DELAY": "10",
        "GRAFANA_READINESS_PROBE_PERIOD": "7",
        "GRAFANA_RUN_AS_NON_ROOT": "true",
        "GRAFANA_RUN_AS_USER": "1001",
        "GRAFANA_ALLOW_PRIVILEGE_ESCALATION": "false"
    }
    
    with mock.patch.dict(os.environ, test_env):
        dep = load_deployment()
        container = dep["spec"]["template"]["spec"]["containers"][0]
        pod_spec = dep["spec"]["template"]["spec"]
        
        # Verify all overrides apply
        assert container["resources"]["requests"]["cpu"] == test_env["GRAFANA_CPU_REQUEST"]
        assert container["resources"]["limits"]["cpu"] == test_env["GRAFANA_CPU_LIMIT"]
        assert container["resources"]["requests"]["memory"] == test_env["GRAFANA_MEMORY_REQUEST"]
        assert container["resources"]["limits"]["memory"] == test_env["GRAFANA_MEMORY_LIMIT"]
        
        assert container["livenessProbe"]["initialDelaySeconds"] == int(test_env["GRAFANA_LIVENESS_PROBE_INITIAL_DELAY"])
        assert container["livenessProbe"]["periodSeconds"] == int(test_env["GRAFANA_LIVENESS_PROBE_PERIOD"])
        
        assert container["readinessProbe"]["initialDelaySeconds"] == int(test_env["GRAFANA_READINESS_PROBE_INITIAL_DELAY"])
        assert container["readinessProbe"]["periodSeconds"] == int(test_env["GRAFANA_READINESS_PROBE_PERIOD"])
        
        assert pod_spec["securityContext"]["runAsNonRoot"] == (test_env["GRAFANA_RUN_AS_NON_ROOT"].lower() == "true")
        assert pod_spec["securityContext"]["runAsUser"] == int(test_env["GRAFANA_RUN_AS_USER"])
        
        assert container["securityContext"]["allowPrivilegeEscalation"] == (test_env["GRAFANA_ALLOW_PRIVILEGE_ESCALATION"].lower() == "true")


def test_ac7_pod_starts_successfully_default_config():
    """AC-7: Grafana pod starts successfully with default config, passes probes, and runs as non-root user without privilege escalation"""
    # This test assumes a running k8s cluster with deployment applied
    import time
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    
    try:
        config.load_kube_config()
    except:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    namespace = os.getenv("NAMESPACE", "default")
    
    # Wait for pod to be running and ready
    start_time = time.time()
    pod = None
    while time.time() - start_time < 120:
        try:
            pods = v1.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=grafana")
            if len(pods.items) > 0:
                pod = pods.items[0]
                if pod.status.phase == "Running":
                    # Check ready condition
                    for cond in pod.status.conditions:
                        if cond.type == "Ready" and cond.status == "True":
                            break
                    else:
                        time.sleep(5)
                        continue
                    break
        except ApiException:
            pass
        time.sleep(5)
    
    assert pod is not None, "Grafana pod not found"
    assert pod.status.phase == "Running", f"Grafana pod not running, phase: {pod.status.phase}"
    
    # Verify running as correct user
    assert pod.spec.security_context.run_as_non_root == DEFAULT_GRAFANA_RUN_AS_NON_ROOT, "Pod not running as non-root"
    assert pod.spec.security_context.run_as_user == DEFAULT_GRAFANA_RUN_AS_USER, f"Pod running as wrong user, expected {DEFAULT_GRAFANA_RUN_AS_USER}"
    
    # Verify container security context
    container = pod.spec.containers[0]
    assert container.security_context.allow_privilege_escalation == DEFAULT_GRAFANA_ALLOW_PRIVILEGE_ESCALATION, "Privilege escalation not disabled"
    
    # Verify probes exist
    assert container.liveness_probe is not None, "Liveness probe missing on running pod"
    assert container.readiness_probe is not None, "Readiness probe missing on running pod"
