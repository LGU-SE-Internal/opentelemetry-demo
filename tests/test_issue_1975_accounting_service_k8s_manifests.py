#!/usr/bin/env python3
import os
import yaml
import kubernetes
from kubernetes.client import AppsV1Api, CoreV1Api
from kubernetes.config import load_kube_config
import pytest
import time

# Test AC-1: Deployment manifest exists at correct path with valid YAML syntax
def test_ac1_deployment_exists_and_valid_schema():
    deployment_path = "./kubernetes/accounting-service/deployment.yaml"
    assert os.path.exists(deployment_path), "Deployment manifest does not exist at expected path"
    
    with open(deployment_path, "r") as f:
        try:
            deployment = yaml.safe_load(f)
        except yaml.YAMLError as e:
            pytest.fail(f"Deployment manifest has invalid YAML syntax: {e}")
    
    assert deployment["apiVersion"] == "apps/v1", "Deployment API version must be apps/v1"
    assert deployment["kind"] == "Deployment", "Manifest kind must be Deployment"

# Test AC-2: Correct resource requests and limits
def test_ac2_resource_requirements():
    deployment_path = "./kubernetes/accounting-service/deployment.yaml"
    with open(deployment_path, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    resources = container["resources"]
    
    # Check requests
    assert "requests" in resources, "Resource requests must be defined"
    cpu_request = resources["requests"]["cpu"]
    # Convert to millicores
    if cpu_request.endswith("m"):
        cpu_request_m = int(cpu_request.rstrip("m"))
    else:
        cpu_request_m = int(float(cpu_request) * 1000)
    assert cpu_request_m >= 100, "CPU request must be at least 100m"
    
    mem_request = resources["requests"]["memory"]
    if mem_request.endswith("Mi"):
        mem_request_mi = int(mem_request.rstrip("Mi"))
    elif mem_request.endswith("Gi"):
        mem_request_mi = int(mem_request.rstrip("Gi")) * 1024
    assert mem_request_mi >= 128, "Memory request must be at least 128Mi"
    
    # Check limits
    assert "limits" in resources, "Resource limits must be defined"
    cpu_limit = resources["limits"]["cpu"]
    if cpu_limit.endswith("m"):
        cpu_limit_m = int(cpu_limit.rstrip("m"))
    else:
        cpu_limit_m = int(float(cpu_limit) * 1000)
    assert cpu_limit_m <= 500, "CPU limit must be at most 500m"
    
    mem_limit = resources["limits"]["memory"]
    if mem_limit.endswith("Mi"):
        mem_limit_mi = int(mem_limit.rstrip("Mi"))
    elif mem_limit.endswith("Gi"):
        mem_limit_mi = int(mem_limit.rstrip("Gi")) * 1024
    assert mem_limit_mi <= 512, "Memory limit must be at most 512Mi"

# Test AC-3: Liveness probe configuration
def test_ac3_liveness_probe_config():
    deployment_path = "./kubernetes/accounting-service/deployment.yaml"
    with open(deployment_path, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    liveness_probe = container["livenessProbe"]
    
    assert liveness_probe["httpGet"]["path"] == "/health/live", "Liveness probe path must be /health/live"
    assert liveness_probe["httpGet"]["port"] == 8080, "Liveness probe port must be 8080"
    assert liveness_probe["initialDelaySeconds"] == 30, "Liveness probe initialDelaySeconds must be 30"
    assert liveness_probe["periodSeconds"] == 10, "Liveness probe periodSeconds must be 10"

# Test AC-4: Readiness probe configuration
def test_ac4_readiness_probe_config():
    deployment_path = "./kubernetes/accounting-service/deployment.yaml"
    with open(deployment_path, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    readiness_probe = container["readinessProbe"]
    
    assert readiness_probe["httpGet"]["path"] == "/health/ready", "Readiness probe path must be /health/ready"
    assert readiness_probe["httpGet"]["port"] == 8080, "Readiness probe port must be 8080"
    assert readiness_probe["initialDelaySeconds"] == 5, "Readiness probe initialDelaySeconds must be 5"
    assert readiness_probe["periodSeconds"] == 5, "Readiness probe periodSeconds must be 5"

# Test AC-5: Security context configuration
def test_ac5_security_context():
    deployment_path = "./kubernetes/accounting-service/deployment.yaml"
    with open(deployment_path, "r") as f:
        deployment = yaml.safe_load(f)
    
    security_context = deployment["spec"]["template"]["spec"]["securityContext"]
    container_security_context = deployment["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    
    assert security_context["runAsNonRoot"] == True, "Security context must have runAsNonRoot: true"
    assert security_context["runAsUser"] == 1000, "Security context must have runAsUser: 1000"
    assert container_security_context["readOnlyRootFilesystem"] == True, "Security context must have readOnlyRootFilesystem: true"
    assert "ALL" in container_security_context["capabilities"]["drop"], "Security context must drop ALL capabilities"

# Test AC-6: Required environment variables
def test_ac6_environment_variables():
    deployment_path = "./kubernetes/accounting-service/deployment.yaml"
    with open(deployment_path, "r") as f:
        deployment = yaml.safe_load(f)
    
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_vars = [env["name"] for env in container["env"]]
    
    required_vars = ["KAFKA_BROKER", "KAFKA_TOPIC", "POSTGRES_CONNECTION_STRING", "SERVICE_NAME", "OTEL_EXPORTER_OTLP_ENDPOINT"]
    for var in required_vars:
        assert var in env_vars, f"Required environment variable {var} is missing"

# Test AC-7: Service manifest exists at correct path with valid YAML syntax
def test_ac7_service_exists_and_valid_schema():
    service_path = "./kubernetes/accounting-service/service.yaml"
    assert os.path.exists(service_path), "Service manifest does not exist at expected path"
    
    with open(service_path, "r") as f:
        try:
            service = yaml.safe_load(f)
        except yaml.YAMLError as e:
            pytest.fail(f"Service manifest has invalid YAML syntax: {e}")
    
    assert service["apiVersion"] == "v1", "Service API version must be v1"
    assert service["kind"] == "Service", "Manifest kind must be Service"

# Test AC-8: Service configuration
def test_ac8_service_config():
    service_path = "./kubernetes/accounting-service/service.yaml"
    with open(service_path, "r") as f:
        service = yaml.safe_load(f)
    
    assert service["spec"]["type"] == "ClusterIP", "Service type must be ClusterIP"
    
    ports = service["spec"]["ports"]
    port_found = False
    for port in ports:
        if port["port"] == 8080 and port["targetPort"] == 8080:
            port_found = True
            break
    assert port_found, "Service must expose port 8080 mapping to container port 8080"
    
    selector = service["spec"]["selector"]
    assert selector["app.kubernetes.io/name"] == "accounting-service", "Service selector must match accounting service pod labels"
    assert selector["app.kubernetes.io/part-of"] == "opentelemetry-demo", "Service selector must match part-of label"

# Test AC-9: Pod enters Running state after deployment
@pytest.mark.cluster
def test_ac9_pod_running_after_deploy():
    load_kube_config()
    apps_api = AppsV1Api()
    core_api = CoreV1Api()
    
    namespace = os.getenv("TEST_NAMESPACE", "default")
    
    # Wait up to 120s for pod to be ready
    start_time = time.time()
    while time.time() - start_time < 120:
        pods = core_api.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=accounting-service")
        if len(pods.items) == 1:
            pod = pods.items[0]
            if pod.status.phase == "Running":
                ready = [c.ready for c in pod.status.container_statuses if c.name == "accounting-service"]
                if ready and ready[0] == True:
                    return
        time.sleep(5)
    
    pytest.fail("Accounting service pod did not enter Running state with 1/1 ready containers within 120 seconds")

# Test AC-10: Readiness probe fails when dependencies are unhealthy
@pytest.mark.cluster
def test_ac10_readiness_probe_failure_on_dependency_issue():
    load_kube_config()
    core_api = CoreV1Api()
    namespace = os.getenv("TEST_NAMESPACE", "default")
    
    # First check pod is ready
    pods = core_api.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=accounting-service")
    assert len(pods.items) == 1, "Accounting service pod not found"
    pod = pods.items[0]
    assert pod.status.phase == "Running"
    
    # Simulate Kafka/PostgreSQL failure (test assumes test infrastructure handles this)
    # Wait up to 10s for readiness probe to fail
    start_time = time.time()
    while time.time() - start_time < 10:
        pods = core_api.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=accounting-service")
        pod = pods.items[0]
        ready = [c.ready for c in pod.status.container_statuses if c.name == "accounting-service"]
        if ready and ready[0] == False:
            # Restore dependencies
            # Wait up to 10s for probe to pass again
            restore_start = time.time()
            while time.time() - restore_start < 10:
                pods = core_api.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=accounting-service")
                pod = pods.items[0]
                ready = [c.ready for c in pod.status.container_statuses if c.name == "accounting-service"]
                if ready and ready[0] == True:
                    return
                time.sleep(1)
            pytest.fail("Readiness probe did not pass after dependencies were restored")
        time.sleep(1)
    
    pytest.fail("Readiness probe did not fail when dependencies were unhealthy")

# Test AC-11: Container runs as non-root user with read-only root filesystem
@pytest.mark.cluster
def test_ac11_security_context_enforced():
    load_kube_config()
    core_api = CoreV1Api()
    namespace = os.getenv("TEST_NAMESPACE", "default")
    
    pods = core_api.list_namespaced_pod(namespace, label_selector="app.kubernetes.io/name=accounting-service")
    assert len(pods.items) == 1, "Accounting service pod not found"
    pod_name = pods.items[0].metadata.name
    
    # Check running user
    exec_command = ["/bin/sh", "-c", "id -u"]
    resp = kubernetes.stream.stream(core_api.connect_get_namespaced_pod_exec,
                                   pod_name,
                                   namespace,
                                   command=exec_command,
                                   stderr=True, stdin=False,
                                   stdout=True, tty=False)
    assert resp.strip() == "1000", f"Container is running as UID {resp.strip()}, expected 1000"
    
    # Check read-only root filesystem
    exec_command = ["/bin/sh", "-c", "touch /test.txt 2>&1 || echo 'RO'"]
    resp = kubernetes.stream.stream(core_api.connect_get_namespaced_pod_exec,
                                   pod_name,
                                   namespace,
                                   command=exec_command,
                                   stderr=True, stdin=False,
                                   stdout=True, tty=False)
    assert "RO" in resp or "Read-only file system" in resp, "Root filesystem is not read-only"
