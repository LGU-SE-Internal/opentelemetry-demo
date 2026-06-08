import pytest
import subprocess
import requests
import time
from kubernetes import client, config

# Load kube config
config.load_kube_config()
k8s_apps_v1 = client.AppsV1Api()
k8s_core_v1 = client.CoreV1Api()

NAMESPACE = "default"
DEPLOYMENT_NAME = "load-generator"
CONTAINER_NAME = "load-generator"
DOCKERFILE_PATH = "src/load-generator/Dockerfile"

def get_load_generator_deployment():
    """Helper to get the load generator deployment"""
    return k8s_apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)

def get_load_generator_pods():
    """Helper to get running load generator pods"""
    pods = k8s_core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    return [p for p in pods.items if p.status.phase == "Running"]

def test_ac1_liveness_probe_configured():
    """AC-1: livenessProbe configured as HTTP GET to /health on port 8089, initialDelaySeconds=10, periodSeconds=5, failureThreshold=3"""
    deploy = get_load_generator_deployment()
    container = next(c for c in deploy.spec.template.spec.containers if c.name == CONTAINER_NAME)
    
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.http_get is not None, "Liveness probe should be HTTP GET type"
    assert container.liveness_probe.http_get.path == "/health", f"Liveness probe path should be /health, got {container.liveness_probe.http_get.path}"
    assert container.liveness_probe.http_get.port == 8089, f"Liveness probe port should be 8089, got {container.liveness_probe.http_get.port}"
    assert container.liveness_probe.initial_delay_seconds == 10, f"initialDelaySeconds should be 10, got {container.liveness_probe.initial_delay_seconds}"
    assert container.liveness_probe.period_seconds == 5, f"periodSeconds should be 5, got {container.liveness_probe.period_seconds}"
    assert container.liveness_probe.failure_threshold == 3, f"failureThreshold should be 3, got {container.liveness_probe.failure_threshold}"

def test_ac2_readiness_probe_configured():
    """AC-2: readinessProbe configured as HTTP GET to /health on port 8089, initialDelaySeconds=5, periodSeconds=3, failureThreshold=5"""
    deploy = get_load_generator_deployment()
    container = next(c for c in deploy.spec.template.spec.containers if c.name == CONTAINER_NAME)
    
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.http_get is not None, "Readiness probe should be HTTP GET type"
    assert container.readiness_probe.http_get.path == "/health", f"Readiness probe path should be /health, got {container.readiness_probe.http_get.path}"
    assert container.readiness_probe.http_get.port == 8089, f"Readiness probe port should be 8089, got {container.readiness_probe.http_get.port}"
    assert container.readiness_probe.initial_delay_seconds == 5, f"initialDelaySeconds should be 5, got {container.readiness_probe.initial_delay_seconds}"
    assert container.readiness_probe.period_seconds == 3, f"periodSeconds should be 3, got {container.readiness_probe.period_seconds}"
    assert container.readiness_probe.failure_threshold == 5, f"failureThreshold should be 5, got {container.readiness_probe.failure_threshold}"

def test_ac3_resource_limits_configured():
    """AC-3: Resource requests: CPU=100m, Memory=128Mi; limits: CPU=500m, Memory=512Mi"""
    deploy = get_load_generator_deployment()
    container = next(c for c in deploy.spec.template.spec.containers if c.name == CONTAINER_NAME)
    
    assert container.resources is not None, "Resources not configured"
    assert container.resources.requests is not None, "Resource requests not configured"
    assert container.resources.requests["cpu"] == "100m", f"CPU request should be 100m, got {container.resources.requests.get('cpu')}"
    assert container.resources.requests["memory"] == "128Mi", f"Memory request should be 128Mi, got {container.resources.requests.get('memory')}"
    assert container.resources.limits is not None, "Resource limits not configured"
    assert container.resources.limits["cpu"] == "500m", f"CPU limit should be 500m, got {container.resources.limits.get('cpu')}"
    assert container.resources.limits["memory"] == "512Mi", f"Memory limit should be 512Mi, got {container.resources.limits.get('memory')}"

def test_ac4_dockerfile_non_root_user():
    """AC-4: Dockerfile creates non-root user with UID 10001 and sets runtime user to this UID before entrypoint"""
    with open(DOCKERFILE_PATH, "r") as f:
        dockerfile_content = f.read()
    
    assert "RUN useradd -u 10001 -m locust" in dockerfile_content, "Dockerfile does not create locust user with UID 10001"
    assert "USER 10001" in dockerfile_content, "Dockerfile does not set runtime user to 10001"
    
    # Verify USER directive comes before ENTRYPOINT/CMD
    user_pos = dockerfile_content.find("USER 10001")
    entrypoint_pos = dockerfile_content.find("ENTRYPOINT")
    cmd_pos = dockerfile_content.find("CMD")
    assert user_pos > 0, "USER directive not found"
    if entrypoint_pos > 0:
        assert user_pos < entrypoint_pos, "USER directive should come before ENTRYPOINT"
    if cmd_pos > 0:
        assert user_pos < cmd_pos, "USER directive should come before CMD"

def test_ac5_deployment_security_context():
    """AC-5: securityContext set with runAsNonRoot: true, runAsUser: 10001, allowPrivilegeEscalation: false, privileged: false"""
    deploy = get_load_generator_deployment()
    template_spec = deploy.spec.template.spec
    
    # Check pod-level security context
    assert template_spec.security_context is not None, "Pod security context not configured"
    assert template_spec.security_context.run_as_non_root == True, "runAsNonRoot should be true"
    assert template_spec.security_context.run_as_user == 10001, f"runAsUser should be 10001, got {template_spec.security_context.run_as_user}"
    
    # Check container-level security context
    container = next(c for c in template_spec.containers if c.name == CONTAINER_NAME)
    assert container.security_context is not None, "Container security context not configured"
    assert container.security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation should be false"
    assert container.security_context.privileged == False, "privileged should be false"

def test_ac6_health_endpoint_returns_200():
    """AC-6: Fully initialized Locust service returns 200 OK on /health endpoint"""
    pods = get_load_generator_pods()
    assert len(pods) > 0, "No running load generator pods found"
    
    pod_ip = pods[0].status.pod_ip
    response = requests.get(f"http://{pod_ip}:8089/health", timeout=5)
    assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}"

def test_ac7_readiness_probe_fails_during_initialization():
    """AC-7: Readiness probe fails during first 5 seconds after pod start, pod is NotReady"""
    # Restart deployment to trigger new pod
    subprocess.run(
        ["kubectl", "rollout", "restart", f"deployment/{DEPLOYMENT_NAME}", "-n", NAMESPACE],
        check=True, capture_output=True
    )
    
    # Wait for new pod to start
    time.sleep(3)
    pods = get_load_generator_pods()
    assert len(pods) > 0, "No new pods created after restart"
    
    # Check readiness status within first 5 seconds
    ready = any(c.ready for c in pods[0].status.container_statuses)
    assert ready == False, "Pod should be NotReady during initialization phase"

def test_ac8_liveness_probe_triggers_restart_on_crash():
    """AC-8: When Locust service crashes, liveness probe triggers restart within 15 seconds"""
    pods = get_load_generator_pods()
    assert len(pods) > 0, "No running load generator pods found"
    pod_name = pods[0].metadata.name
    initial_restart_count = next(c.restart_count for c in pods[0].status.container_statuses if c.name == CONTAINER_NAME)
    
    # Kill the locust process inside the pod
    subprocess.run(
        ["kubectl", "exec", pod_name, "-n", NAMESPACE, "--", "pkill", "-f", "locust"],
        check=False, capture_output=True
    )
    
    # Wait up to 15 seconds for restart
    restarted = False
    for _ in range(15):
        time.sleep(1)
        pod = k8s_core_v1.read_namespaced_pod(pod_name, NAMESPACE)
        current_restart_count = next(c.restart_count for c in pod.status.container_statuses if c.name == CONTAINER_NAME)
        if current_restart_count > initial_restart_count:
            restarted = True
            break
    
    assert restarted == True, "Pod was not restarted within 15 seconds after crash"

def test_ac9_container_runs_as_non_root_user():
    """AC-9: kubectl exec whoami returns locust user, not root"""
    pods = get_load_generator_pods()
    assert len(pods) > 0, "No running load generator pods found"
    pod_name = pods[0].metadata.name
    
    result = subprocess.run(
        ["kubectl", "exec", pod_name, "-n", NAMESPACE, "--", "whoami"],
        capture_output=True, text=True, check=True
    )
    
    assert result.stdout.strip() == "locust", f"Expected user 'locust', got '{result.stdout.strip()}'"
    assert result.stdout.strip() != "root", "Container should not run as root user"
