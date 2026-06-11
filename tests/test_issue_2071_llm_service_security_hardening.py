import pytest
import kubernetes
from kubernetes.client import CoreV1Api, AppsV1Api
from kubernetes.stream import stream
import requests
import time

NAMESPACE = "default"  # Change if using a different namespace
SERVICE_ACCOUNT_NAME = "llm-service-sa"
DEPLOYMENT_NAME = "llm-service"
SERVICE_PORT = 8080  # Update if llm service uses a different port

@pytest.fixture(scope="module")
def k8s_clients():
    kubernetes.config.load_kube_config()
    core_api = CoreV1Api()
    apps_api = AppsV1Api()
    return core_api, apps_api

def test_ac1_service_account_exists_with_no_permissions(k8s_clients):
    """AC-1: Dedicated ServiceAccount named llm-service-sa exists with no assigned Roles/ClusterRoles"""
    core_api, _ = k8s_clients
    
    # Check service account exists
    try:
        sa = core_api.read_namespaced_service_account(name=SERVICE_ACCOUNT_NAME, namespace=NAMESPACE)
        assert sa.metadata.name == SERVICE_ACCOUNT_NAME
    except kubernetes.client.exceptions.ApiException as e:
        assert False, f"Service account {SERVICE_ACCOUNT_NAME} not found: {e}"
    
    # Verify no roles are bound to this service account
    rbac_api = kubernetes.client.RbacAuthorizationV1Api()
    role_bindings = rbac_api.list_namespaced_role_binding(namespace=NAMESPACE)
    cluster_role_bindings = rbac_api.list_cluster_role_binding()
    
    sa_subject = f"system:serviceaccount:{NAMESPACE}:{SERVICE_ACCOUNT_NAME}"
    for rb in role_bindings.items:
        if rb.subjects:
            for sub in rb.subjects:
                if sub.kind == "ServiceAccount" and sub.name == SERVICE_ACCOUNT_NAME and sub.namespace == NAMESPACE:
                    assert False, f"Service account {SERVICE_ACCOUNT_NAME} has unwanted RoleBinding: {rb.metadata.name}"
    
    for crb in cluster_role_bindings.items:
        if crb.subjects:
            for sub in crb.subjects:
                if sub.kind == "ServiceAccount" and sub.name == SERVICE_ACCOUNT_NAME and sub.namespace == NAMESPACE:
                    assert False, f"Service account {SERVICE_ACCOUNT_NAME} has unwanted ClusterRoleBinding: {crb.metadata.name}"

def test_ac2_deployment_uses_correct_service_account(k8s_clients):
    """AC-2: llm service deployment serviceAccountName is set to llm-service-sa"""
    _, apps_api = k8s_clients
    
    deployment = apps_api.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    assert deployment.spec.template.spec.service_account_name == SERVICE_ACCOUNT_NAME, f"Expected serviceAccountName {SERVICE_ACCOUNT_NAME}, got {deployment.spec.template.spec.service_account_name}"

def test_ac3_automount_service_account_token_false(k8s_clients):
    """AC-3: llm service deployment automountServiceAccountToken is explicitly set to false"""
    _, apps_api = k8s_clients
    
    deployment = apps_api.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    assert deployment.spec.template.spec.automount_service_account_token is False, f"Expected automountServiceAccountToken = False, got {deployment.spec.template.spec.automount_service_account_token}"

def test_ac4_pod_security_context_configured(k8s_clients):
    """AC-4: llm service deployment has pod-level security context with all required values"""
    _, apps_api = k8s_clients
    
    deployment = apps_api.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
    pod_spec = deployment.spec.template.spec
    security_context = pod_spec.security_context
    
    assert security_context is not None, "Pod-level security context not found"
    assert security_context.privileged is False, f"Expected privileged=False, got {security_context.privileged}"
    assert security_context.run_as_user == 10001, f"Expected runAsUser=10001, got {security_context.run_as_user}"
    assert security_context.run_as_group == 10001, f"Expected runAsGroup=10001, got {security_context.run_as_group}"
    assert security_context.run_as_non_root is True, f"Expected runAsNonRoot=True, got {security_context.run_as_non_root}"
    assert security_context.allow_privilege_escalation is False, f"Expected allowPrivilegeEscalation=False, got {security_context.allow_privilege_escalation}"
    assert security_context.seccomp_profile is not None, "seccompProfile not configured"
    assert security_context.seccomp_profile.type == "RuntimeDefault", f"Expected seccompProfile.type=RuntimeDefault, got {security_context.seccomp_profile.type}"
    assert security_context.capabilities is not None, "capabilities not configured"
    assert "ALL" in security_context.capabilities.drop, "ALL capabilities not dropped"
    assert security_context.read_only_root_filesystem is True, f"Expected readOnlyRootFilesystem=True, got {security_context.read_only_root_filesystem}"

def test_ac5_pods_running_ready(k8s_clients):
    """AC-5: All llm service pods come up in Ready state with restarts < 1 over 5 minutes"""
    core_api, apps_api = k8s_clients
    
    # Wait up to 5 minutes for pods to be ready
    timeout = 300
    start_time = time.time()
    while time.time() - start_time < timeout:
        pods = core_api.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
        if not pods.items:
            time.sleep(10)
            continue
        
        all_ready = True
        for pod in pods.items:
            if pod.status.phase != "Running":
                all_ready = False
                break
            for condition in pod.status.conditions:
                if condition.type == "Ready" and condition.status != "True":
                    all_ready = False
                    break
            for container_status in pod.status.container_statuses:
                if container_status.restart_count > 0:
                    assert False, f"Pod {pod.metadata.name} has {container_status.restart_count} restarts (expected < 1)"
        
        if all_ready:
            break
        time.sleep(10)
    else:
        assert False, "Timed out waiting for all llm service pods to be ready"

def test_ac6_health_check_succeeds(k8s_clients):
    """AC-6: llm service responds successfully to GET /healthz with HTTP 200"""
    core_api, _ = k8s_clients
    
    # Get a pod IP
    pods = core_api.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No llm service pods found"
    pod_ip = pods.items[0].status.pod_ip
    
    # Make health check request
    try:
        response = requests.get(f"http://{pod_ip}:{SERVICE_PORT}/healthz", timeout=5)
        assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}"
    except requests.exceptions.RequestException as e:
        assert False, f"Health check request failed: {e}"

def test_ac7_no_service_account_token_mounted(k8s_clients):
    """AC-7: No service account token is mounted inside containers at /var/run/secrets/kubernetes.io/serviceaccount/token"""
    core_api, _ = k8s_clients
    
    pods = core_api.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    assert len(pods.items) > 0, "No llm service pods found"
    pod_name = pods.items[0].metadata.name
    container_name = pods.items[0].spec.containers[0].name
    
    # Try to list the service account mount directory
    try:
        exec_command = [
            "/bin/sh",
            "-c",
            "ls /var/run/secrets/kubernetes.io/serviceaccount/token 2>/dev/null || echo 'NOT FOUND'"
        ]
        resp = stream(core_api.connect_get_namespaced_pod_exec,
                      pod_name,
                      NAMESPACE,
                      command=exec_command,
                      container=container_name,
                      stderr=True, stdin=False,
                      stdout=True, tty=False)
        
        output = resp.strip()
        assert "NOT FOUND" in output, f"Service account token found at path: {output}"
    except kubernetes.client.exceptions.ApiException as e:
        # If the directory doesn't exist at all, that's also good
        assert "no such file or directory" in str(e).lower(), f"Unexpected error checking for service account token: {e}"

def test_ac8_service_account_has_no_permissions(k8s_clients):
    """AC-8: kubectl auth can-i --list --as=system:serviceaccount:<namespace>:llm-service-sa returns No for all verbs/resources"""
    import subprocess
    
    sa_subject = f"system:serviceaccount:{NAMESPACE}:{SERVICE_ACCOUNT_NAME}"
    result = subprocess.run(
        ["kubectl", "auth", "can-i", "--list", f"--as={sa_subject}"],
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0, f"kubectl auth can-i command failed: {result.stderr}"
    output = result.stdout.strip()
    # The output should either be empty or only contain "No" lines
    for line in output.splitlines():
        if line.strip() and not line.strip().startswith("No"):
            assert False, f"Service account has unexpected permissions: {line}"
