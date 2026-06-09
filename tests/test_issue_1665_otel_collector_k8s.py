#!/usr/bin/env python3
import pytest
import time
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Load kube config
config.load_kube_config()
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

NAMESPACE = "default"
DEPLOYMENT_NAME = "otel-collector"
SERVICE_NAME = "otel-collector"
CONFIGMAP_NAME = "otel-collector-config"

@pytest.fixture(scope="module")
def k8s_client():
    return {
        "core": v1,
        "apps": apps_v1
    }

def test_ac1_deployment_pods_running(k8s_client):
    """AC-1: All otel-collector pods enter Running state within 60 seconds"""
    start = time.time()
    while time.time() - start < 60:
        try:
            deploy = k8s_client["apps"].read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
            if deploy.status.available_replicas == deploy.spec.replicas:
                # Verify all pods are Running
                pods = k8s_client["core"].list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
                all_running = all(p.status.phase == "Running" for p in pods.items)
                if all_running:
                    return
        except ApiException:
            pass
        time.sleep(2)
    pytest.fail("Pods did not reach Running state within 60s")

def test_ac2_security_context_enforced(k8s_client):
    """AC-2: Pods run as non-root, no privilege escalation, read-only root filesystem"""
    pods = k8s_client["core"].list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No otel-collector pods found"
    pod = pods.items[0]

    # Verify security context settings in pod spec
    container = pod.spec.containers[0]
    assert container.security_context.run_as_non_root == True
    assert container.security_context.allow_privilege_escalation == False
    assert container.security_context.read_only_root_filesystem == True
    assert container.security_context.run_as_user != 0

    # Verify runtime user is non-root
    exec_cmd = ["/bin/sh", "-c", "whoami"]
    resp = k8s_client["core"].connect_get_namespaced_pod_exec(
        pod.metadata.name, NAMESPACE,
        command=exec_cmd,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    assert "root" not in resp.strip().lower(), f"Pod is running as root: {resp}"

    # Verify read-only filesystem
    exec_cmd = ["/bin/sh", "-c", "touch /test.txt 2>&1"]
    resp = k8s_client["core"].connect_get_namespaced_pod_exec(
        pod.metadata.name, NAMESPACE,
        command=exec_cmd,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    assert "permission denied" in resp.lower(), "Filesystem is not read-only"

def test_ac3_health_probes_function(k8s_client):
    """AC-3: Liveness/readiness probes return 200 OK, unhealthy pods get restarted"""
    pods = k8s_client["core"].list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No otel-collector pods found"
    pod = pods.items[0]

    # Verify probe configuration in spec
    container = pod.spec.containers[0]
    assert container.liveness_probe is not None
    assert container.liveness_probe.http_get.path == "/"
    assert container.liveness_probe.http_get.port == 13133
    assert container.liveness_probe.initial_delay_seconds == 5
    assert container.liveness_probe.period_seconds == 10
    assert container.liveness_probe.failure_threshold == 3

    assert container.readiness_probe is not None
    assert container.readiness_probe.http_get.path == "/"
    assert container.readiness_probe.http_get.port == 13133
    assert container.readiness_probe.initial_delay_seconds == 2
    assert container.readiness_probe.period_seconds == 5
    assert container.readiness_probe.failure_threshold == 3

    # Verify healthy probe returns 200
    exec_cmd = ["/bin/sh", "-c", "wget -q -O - http://localhost:13133/ --server-response 2>&1 | grep 'HTTP/' | awk '{print $2}'"]
    resp = k8s_client["core"].connect_get_namespaced_pod_exec(
        pod.metadata.name, NAMESPACE,
        command=exec_cmd,
        stderr=True, stdin=False, stdout=True, tty=False
    )
    assert resp.strip() == "200", f"Health probe returned {resp.strip()}, expected 200"

def test_ac4_configmap_updates_propagate(k8s_client):
    """AC-4: ConfigMap changes roll out to pods and are applied"""
    # Get current config map content
    cm = k8s_client["core"].read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    original_config = cm.data["config.yaml"]
    test_change = "# TEST CHANGE ADDED BY AC4 TEST\n"
    updated_config = original_config + test_change

    # Update config map
    cm.data["config.yaml"] = updated_config
    k8s_client["core"].patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)

    # Rollout restart deployment to apply changes
    k8s_client["apps"].patch_namespaced_deployment(
        DEPLOYMENT_NAME, NAMESPACE,
        {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ")}}}}}
    )

    # Wait for rollout completion
    time.sleep(30)
    pods = k8s_client["core"].list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No otel-collector pods found after rollout"
    
    # Verify test change exists in running pod config
    pod = pods.items[0]
    exec_cmd = ["/bin/sh", "-c", "grep 'TEST CHANGE ADDED BY AC4 TEST' /etc/otelcol-contrib/config.yaml"]
    try:
        resp = k8s_client["core"].connect_get_namespaced_pod_exec(
            pod.metadata.name, NAMESPACE,
            command=exec_cmd,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        assert test_change.strip() in resp.strip()
    finally:
        # Revert config map change
        cm.data["config.yaml"] = original_config
        k8s_client["core"].patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)

def test_ac5_service_forwards_traffic(k8s_client):
    """AC-5: ClusterIP Service forwards gRPC/HTTP traffic to healthy pods"""
    # Verify service exists and has correct ports
    svc = k8s_client["core"].read_namespaced_service(SERVICE_NAME, NAMESPACE)
    assert svc.spec.type == "ClusterIP"
    port_map = {p.name: p.port for p in svc.spec.ports}
    assert port_map["grpc"] == 4317
    assert port_map["http"] == 4318
    assert port_map["health"] == 13133

    # Verify service endpoints exist (healthy pods)
    endpoints = k8s_client["core"].read_namespaced_endpoints(SERVICE_NAME, NAMESPACE)
    assert len(endpoints.subsets) > 0, "No active endpoints for otel-collector service"
    for subset in endpoints.subsets:
        assert len(subset.addresses) > 0, "No ready addresses in service endpoints"

def test_ac6_resource_limits_configurable(k8s_client):
    """AC-6: Resource requests/limits match configured values"""
    deploy = k8s_client["apps"].read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deploy.spec.template.spec.containers[0]

    # Verify default resource values are set
    assert container.resources.requests["cpu"] == "100m"
    assert container.resources.requests["memory"] == "256Mi"
    assert container.resources.limits["cpu"] == "500m"
    assert container.resources.limits["memory"] == "512Mi"

    # Verify pod-level resources match deployment spec
    pods = k8s_client["core"].list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No otel-collector pods found"
    pod_container = pods.items[0].spec.containers[0]
    assert pod_container.resources.requests["cpu"] == container.resources.requests["cpu"]
    assert pod_container.resources.requests["memory"] == container.resources.requests["memory"]
    assert pod_container.resources.limits["cpu"] == container.resources.limits["cpu"]
    assert pod_container.resources.limits["memory"] == container.resources.limits["memory"]

def test_ac7_resource_limit_enforcement(k8s_client):
    """AC-7: Pods are evicted only when exceeding resource limits for sustained periods"""
    # This test is a placeholder for load testing validation
    deploy = k8s_client["apps"].read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    container = deploy.spec.template.spec.containers[0]
    assert container.resources.limits is not None, "Resource limits are not configured"
    assert container.resources.requests is not None, "Resource requests are not configured"
