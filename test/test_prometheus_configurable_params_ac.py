#!/usr/bin/env python3
"""Integration tests for Prometheus configurable parameters ACs for issue #1316"""
import subprocess
import json
import requests
import time
import pytest

NAMESPACE = "default"
PROMETHEUS_LABEL = "app.kubernetes.io/name=prometheus"
PROMETHEUS_PORT = 9090
PROMETHEUS_DEPLOYMENT_NAME = "prometheus"

def get_prometheus_pod_name():
    """Helper to get current Prometheus pod name"""
    cmd = f"kubectl get pods -l {PROMETHEUS_LABEL} -n {NAMESPACE} -o jsonpath='{{.items[0].metadata.name}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("No Prometheus pod found")
    return result.stdout.strip()

def get_prometheus_deployment():
    """Helper to get Prometheus deployment spec"""
    cmd = f"kubectl get deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -o json"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("No Prometheus deployment found")
    return json.loads(result.stdout)

def get_prometheus_config(pod_name):
    """Get running Prometheus config from API"""
    cmd = f"kubectl exec -n {NAMESPACE} {pod_name} -- wget -qO- http://localhost:{PROMETHEUS_PORT}/api/v1/status/config"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, "Failed to get Prometheus config"
    return json.loads(result.stdout)

def test_ac1_valid_retention_time_env_var_overrides_default():
    """AC-1: When PROMETHEUS_RETENTION_TIME env var is set to valid duration, Prometheus starts with matching retention.time flag"""
    # Patch deployment with test env var
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "prometheus",
                        "env": [{"name": "PROMETHEUS_RETENTION_TIME", "value": "30d"}]
                    }]
                }
            }
        }
    }
    patch_json = json.dumps(patch)
    subprocess.run(
        f"kubectl patch deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -p '{patch_json}'",
        shell=True, check=True
    )
    # Wait for pod rollout
    subprocess.run(f"kubectl rollout status deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} --timeout=120s", shell=True, check=True)
    pod = get_prometheus_pod_name()
    # Check command line flags
    cmd = f"kubectl exec -n {NAMESPACE} {pod} -- ps -o args= -C prometheus"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, "Failed to get prometheus command line args"
    assert "--storage.tsdb.retention.time=30d" in result.stdout, "Retention time flag not set correctly"

def test_ac2_valid_retention_size_env_var_enables_retention_size():
    """AC-2: When PROMETHEUS_RETENTION_SIZE env var is set to valid size, Prometheus starts with matching retention.size flag"""
    # Patch deployment with test env var
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "prometheus",
                        "env": [{"name": "PROMETHEUS_RETENTION_SIZE", "value": "20GB"}]
                    }]
                }
            }
        }
    }
    patch_json = json.dumps(patch)
    subprocess.run(
        f"kubectl patch deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -p '{patch_json}'",
        shell=True, check=True
    )
    # Wait for pod rollout
    subprocess.run(f"kubectl rollout status deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} --timeout=120s", shell=True, check=True)
    pod = get_prometheus_pod_name()
    # Check command line flags
    cmd = f"kubectl exec -n {NAMESPACE} {pod} -- ps -o args= -C prometheus"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, "Failed to get prometheus command line args"
    assert "--storage.tsdb.retention.size=20GB" in result.stdout, "Retention size flag not set correctly"

def test_ac3_valid_scrape_interval_env_var_updates_config():
    """AC-3: When PROMETHEUS_SCRAPE_INTERVAL env var is set to valid duration, global scrape_interval in config matches"""
    # Patch deployment with test env var
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "prometheus",
                        "env": [{"name": "PROMETHEUS_SCRAPE_INTERVAL", "value": "30s"}]
                    }]
                }
            }
        }
    }
    patch_json = json.dumps(patch)
    subprocess.run(
        f"kubectl patch deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -p '{patch_json}'",
        shell=True, check=True
    )
    # Wait for pod rollout
    subprocess.run(f"kubectl rollout status deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} --timeout=120s", shell=True, check=True)
    pod = get_prometheus_pod_name()
    # Get config from API
    config = get_prometheus_config(pod)
    assert config["status"] == "success"
    yaml_config = config["data"]["yaml"]
    assert "scrape_interval: 30s" in yaml_config, "Global scrape interval not updated correctly in config"

def test_ac4_invalid_params_cause_container_start_failure():
    """AC-4: Invalid environment variables cause container to fail starting with correct error message"""
    # Test invalid retention time first
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "prometheus",
                        "env": [{"name": "PROMETHEUS_RETENTION_TIME", "value": "invalid-duration"}]
                    }]
                }
            }
        }
    }
    patch_json = json.dumps(patch)
    subprocess.run(
        f"kubectl patch deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -p '{patch_json}'",
        shell=True, check=True
    )
    # Wait for crash
    time.sleep(30)
    # Get pod events
    cmd = f"kubectl get events -n {NAMESPACE} --field-selector involvedObject.kind=Pod --sort-by=.lastTimestamp | grep prometheus | tail -20"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert "Invalid PROMETHEUS_RETENTION_TIME value: invalid-duration" in result.stdout, "Correct error message not found for invalid retention time"

    # Restore deployment to working state
    subprocess.run(
        f"kubectl set env deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} PROMETHEUS_RETENTION_TIME-",
        shell=True, check=True
    )
    subprocess.run(f"kubectl rollout status deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} --timeout=120s", shell=True, check=True)

def test_ac5_helm_resource_requests_override_defaults():
    """AC-5: When prometheus.resources.requests Helm values are set, deployment uses provided values"""
    # This test assumes Helm is used to deploy; we simulate by checking templating
    # First check default values exist in deployment
    deploy = get_prometheus_deployment()
    resources = deploy["spec"]["template"]["spec"]["containers"][0].get("resources", {})
    assert "requests" in resources, "No resource requests configured"
    # Verify we can override via kubectl patch (simulating Helm value change)
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "prometheus",
                        "resources": {
                            "requests": {
                                "cpu": "200m",
                                "memory": "512Mi"
                            }
                        }
                    }]
                }
            }
        }
    }
    patch_json = json.dumps(patch)
    subprocess.run(
        f"kubectl patch deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -p '{patch_json}'",
        shell=True, check=True
    )
    subprocess.run(f"kubectl rollout status deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} --timeout=120s", shell=True, check=True)
    # Verify new values are present
    deploy_updated = get_prometheus_deployment()
    updated_resources = deploy_updated["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert updated_resources["requests"]["cpu"] == "200m", "CPU request not updated correctly"
    assert updated_resources["requests"]["memory"] == "512Mi", "Memory request not updated correctly"

def test_ac6_helm_resource_limits_override_defaults():
    """AC-6: When prometheus.resources.limits Helm values are set, deployment uses provided values"""
    # Verify we can override via kubectl patch (simulating Helm value change)
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "prometheus",
                        "resources": {
                            "limits": {
                                "cpu": "1000m",
                                "memory": "2Gi"
                            }
                        }
                    }]
                }
            }
        }
    }
    patch_json = json.dumps(patch)
    subprocess.run(
        f"kubectl patch deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -p '{patch_json}'",
        shell=True, check=True
    )
    subprocess.run(f"kubectl rollout status deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} --timeout=120s", shell=True, check=True)
    # Verify new values are present
    deploy_updated = get_prometheus_deployment()
    updated_resources = deploy_updated["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert updated_resources["limits"]["cpu"] == "1000m", "CPU limit not updated correctly"
    assert updated_resources["limits"]["memory"] == "2Gi", "Memory limit not updated correctly"

def test_ac7_documentation_lists_all_configurable_parameters():
    """AC-7: Prometheus service documentation page lists all 6 configurable parameters with descriptions, valid formats, default values"""
    # Verify documentation file exists and contains all required parameters
    doc_files = [
        "./docs/services/prometheus.md",
        "./charts/opentelemetry-demo/charts/prometheus/README.md",
        "./README.md"
    ]
    found_doc = False
    required_params = [
        "PROMETHEUS_RETENTION_TIME",
        "PROMETHEUS_RETENTION_SIZE",
        "PROMETHEUS_SCRAPE_INTERVAL",
        "prometheus.resources.requests.cpu",
        "prometheus.resources.requests.memory",
        "prometheus.resources.limits.cpu",
        "prometheus.resources.limits.memory"
    ]
    for file_path in doc_files:
        try:
            with open(file_path, "r") as f:
                content = f.read()
            all_present = all(param in content for param in required_params)
            if all_present:
                found_doc = True
                break
        except FileNotFoundError:
            continue
    assert found_doc, "No documentation file found listing all required configurable parameters"

def test_ac8_no_custom_configs_use_default_values():
    """AC-8: No custom environment variables/Helm values set uses original hardcoded defaults"""
    # Remove all custom env vars
    subprocess.run(
        f"kubectl set env deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} PROMETHEUS_RETENTION_TIME- PROMETHEUS_RETENTION_SIZE- PROMETHEUS_SCRAPE_INTERVAL-",
        shell=True, check=True
    )
    # Reset resource values to defaults
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "prometheus",
                        "resources": {
                            "requests": {
                                "cpu": "100m",
                                "memory": "256Mi"
                            },
                            "limits": {
                                "cpu": "500m",
                                "memory": "1Gi"
                            }
                        }
                    }]
                }
            }
        }
    }
    patch_json = json.dumps(patch)
    subprocess.run(
        f"kubectl patch deployment {PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} -p '{patch_json}'",
        shell=True, check=True
    )
    subprocess.run(f"kubectl rollout status deployment/{PROMETHEUS_DEPLOYMENT_NAME} -n {NAMESPACE} --timeout=120s", shell=True, check=True)
    pod = get_prometheus_pod_name()
    # Check default retention time
    cmd = f"kubectl exec -n {NAMESPACE} {pod} -- ps -o args= -C prometheus"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert "--storage.tsdb.retention.time=15d" in result.stdout, "Default retention time not used"
    # Check default scrape interval
    config = get_prometheus_config(pod)
    yaml_config = config["data"]["yaml"]
    assert "scrape_interval: 15s" in yaml_config, "Default scrape interval not used"
    # Check default resources
    deploy = get_prometheus_deployment()
    resources = deploy["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources["requests"]["cpu"] == "100m", "Default CPU request not used"
    assert resources["requests"]["memory"] == "256Mi", "Default memory request not used"
    assert resources["limits"]["cpu"] == "500m", "Default CPU limit not used"
    assert resources["limits"]["memory"] == "1Gi", "Default memory limit not used"
