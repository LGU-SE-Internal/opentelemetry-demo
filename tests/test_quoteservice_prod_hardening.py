#!/usr/bin/env python3
import json
import subprocess
import pytest

NAMESPACE = "opentelemetry-demo"
DEPLOYMENT_NAME = "quoteservice"

def run_kubectl_jsonpath(jsonpath):
    """Run kubectl get deployment with given jsonpath, return output or None if not found"""
    try:
        cmd = [
            "kubectl", "get", "deployment", DEPLOYMENT_NAME,
            "-n", NAMESPACE,
            "-o", f"jsonpath={jsonpath}"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

def test_ac1_resource_requests_limits_set():
    """AC-1: Verify CPU/memory requests and limits are correctly set"""
    resources_json = run_kubectl_jsonpath("{.spec.template.spec.containers[0].resources}")
    assert resources_json is not None, "resources field is missing from container spec"
    
    resources = json.loads(resources_json)
    # Check requests
    assert "requests" in resources, "requests not defined in resources"
    assert resources["requests"].get("cpu") == "100m", f"Expected CPU request 100m, got {resources['requests'].get('cpu')}"
    assert resources["requests"].get("memory") == "128Mi", f"Expected memory request 128Mi, got {resources['requests'].get('memory')}"
    # Check limits
    assert "limits" in resources, "limits not defined in resources"
    assert resources["limits"].get("cpu") == "500m", f"Expected CPU limit 500m, got {resources['limits'].get('cpu')}"
    assert resources["limits"].get("memory") == "256Mi", f"Expected memory limit 256Mi, got {resources['limits'].get('memory')}"

def test_ac2_container_security_context_non_root():
    """AC-2: Verify container security context runs as non-root with no privilege escalation"""
    sc_json = run_kubectl_jsonpath("{.spec.template.spec.containers[0].securityContext}")
    assert sc_json is not None, "container securityContext is missing"
    
    sc = json.loads(sc_json)
    assert sc.get("runAsNonRoot") == True, "runAsNonRoot must be true"
    assert sc.get("runAsUser") == 10001, f"Expected runAsUser 10001, got {sc.get('runAsUser')}"
    assert sc.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem must be true"
    assert sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation must be false"
    assert sc.get("privileged") == False, "privileged must be false"

def test_ac3_pod_security_capabilities_seccomp():
    """AC-3: Verify all capabilities dropped and seccomp profile set to RuntimeDefault"""
    pod_sc_json = run_kubectl_jsonpath("{.spec.template.spec.securityContext}")
    assert pod_sc_json is not None, "pod securityContext is missing"
    
    pod_sc = json.loads(pod_sc_json)
    assert "capabilities" in pod_sc, "capabilities not defined in pod securityContext"
    assert "drop" in pod_sc["capabilities"], "capabilities.drop not defined"
    assert "ALL" in pod_sc["capabilities"]["drop"], "capabilities.drop must include ALL"
    assert "seccompProfile" in pod_sc, "seccompProfile not defined"
    assert pod_sc["seccompProfile"].get("type") == "RuntimeDefault", f"Expected seccompProfile.type RuntimeDefault, got {pod_sc['seccompProfile'].get('type')}"

def test_ac4_liveness_readiness_probes_configured():
    """AC-4: Verify liveness and readiness probes are correctly configured"""
    # Check liveness probe
    liveness_json = run_kubectl_jsonpath("{.spec.template.spec.containers[0].livenessProbe}")
    assert liveness_json is not None, "livenessProbe is missing"
    liveness = json.loads(liveness_json)
    assert "httpGet" in liveness, "livenessProbe must use HTTP GET"
    assert liveness["httpGet"].get("path") == "/health", f"Expected liveness probe path /health, got {liveness['httpGet'].get('path')}"
    assert liveness.get("initialDelaySeconds") == 5, f"Expected liveness initialDelaySeconds 5, got {liveness.get('initialDelaySeconds')}"
    assert liveness.get("periodSeconds") == 10, f"Expected liveness periodSeconds 10, got {liveness.get('periodSeconds')}"
    assert liveness.get("failureThreshold") == 3, f"Expected liveness failureThreshold 3, got {liveness.get('failureThreshold')}"

    # Check readiness probe
    readiness_json = run_kubectl_jsonpath("{.spec.template.spec.containers[0].readinessProbe}")
    assert readiness_json is not None, "readinessProbe is missing"
    readiness = json.loads(readiness_json)
    assert "httpGet" in readiness, "readinessProbe must use HTTP GET"
    assert readiness["httpGet"].get("path") == "/health", f"Expected readiness probe path /health, got {readiness['httpGet'].get('path')}"
    assert readiness.get("initialDelaySeconds") == 5, f"Expected readiness initialDelaySeconds 5, got {readiness.get('initialDelaySeconds')}"
    assert readiness.get("periodSeconds") == 10, f"Expected readiness periodSeconds 10, got {readiness.get('periodSeconds')}"
    assert readiness.get("failureThreshold") == 3, f"Expected readiness failureThreshold 3, got {readiness.get('failureThreshold')}"

def test_ac5_pod_anti_affinity_configured():
    """AC-5: Verify pod anti-affinity rule distributes replicas across nodes"""
    affinity_json = run_kubectl_jsonpath("{.spec.template.spec.affinity}")
    assert affinity_json is not None, "affinity is missing from pod spec"
    
    affinity = json.loads(affinity_json)
    assert "podAntiAffinity" in affinity, "podAntiAffinity not defined"
    paa = affinity["podAntiAffinity"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" in paa, "requiredDuringSchedulingIgnoredDuringExecution rule missing"
    
    rules = paa["requiredDuringSchedulingIgnoredDuringExecution"]
    assert len(rules) >= 1, "No anti-affinity rules defined"
    
    # Check for rule matching app: quoteservice with hostname topology key
    found_rule = False
    for rule in rules:
        if rule.get("topologyKey") == "kubernetes.io/hostname":
            exprs = rule.get("labelSelector", {}).get("matchExpressions", [])
            for expr in exprs:
                if expr.get("key") == "app" and expr.get("operator") == "In" and "quoteservice" in expr.get("values", []):
                    found_rule = True
                    break
        if found_rule:
            break
    assert found_rule, "Required anti-affinity rule for app: quoteservice with topologyKey kubernetes.io/hostname not found"
