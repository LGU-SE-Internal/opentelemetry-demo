"""
Integration tests for Kubernetes deployment of Kafka service
Verifies all acceptance criteria from issue #1881 spec
"""
import os
import subprocess
import yaml
import time

KAFKA_MANIFEST_DIR = "./kubernetes/kafka/"
SERVICE_NAME = "kafka"
TEST_NAMESPACE = os.getenv("TEST_NAMESPACE", "default")

def run_cmd(cmd, shell=True, check=True):
    """Run shell command and return output"""
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {cmd}\nStderr: {result.stderr}\nStdout: {result.stdout}")
    return result

def load_all_manifests():
    """Load all YAML manifests from kafka directory"""
    manifests = []
    if not os.path.exists(KAFKA_MANIFEST_DIR):
        raise Exception(f"Kafka manifest directory {KAFKA_MANIFEST_DIR} does not exist")
    for filename in os.listdir(KAFKA_MANIFEST_DIR):
        if not filename.endswith(".yaml") and not filename.endswith(".yml"):
            continue
        filepath = os.path.join(KAFKA_MANIFEST_DIR, filename)
        with open(filepath, 'r') as f:
            docs = list(yaml.safe_load_all(f))
            for doc in docs:
                if doc:
                    manifests.append(doc)
    return manifests

def get_manifest_by_kind(kind, name=None):
    """Get manifest of specified kind, optionally matching name"""
    manifests = load_all_manifests()
    for doc in manifests:
        if doc.get("kind") == kind:
            if name is None or doc.get("metadata", {}).get("name") == name:
                return doc
    raise Exception(f"Manifest of kind {kind} {'with name ' + name if name else ''} not found")

def get_running_pod_name():
    """Get name of running kafka pod"""
    cmd = f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].metadata.name}}'"
    result = run_cmd(cmd)
    return result.stdout.strip()

def test_ac1_three_manifest_files_exist():
    """AC-1: Three Kubernetes manifest files (deployment.yaml, service.yaml, pvc.yaml) are present in the ./kubernetes/kafka directory"""
    required_files = ["deployment.yaml", "service.yaml", "pvc.yaml"]
    for file in required_files:
        filepath = os.path.join(KAFKA_MANIFEST_DIR, file)
        assert os.path.exists(filepath), f"Required manifest file {file} not found in {KAFKA_MANIFEST_DIR}"
    
    # Verify each file has correct kind
    with open(os.path.join(KAFKA_MANIFEST_DIR, "deployment.yaml")) as f:
        deploy = yaml.safe_load(f)
        assert deploy["kind"] == "Deployment", "deployment.yaml is not a Deployment resource"
    
    with open(os.path.join(KAFKA_MANIFEST_DIR, "service.yaml")) as f:
        svc = yaml.safe_load(f)
        assert svc["kind"] == "Service", "service.yaml is not a Service resource"
    
    with open(os.path.join(KAFKA_MANIFEST_DIR, "pvc.yaml")) as f:
        pvc = yaml.safe_load(f)
        assert pvc["kind"] == "PersistentVolumeClaim", "pvc.yaml is not a PersistentVolumeClaim resource"

def test_ac2_liveness_probe_configured_correctly():
    """AC-2: Deployment manifest includes liveness probe executing /kafka-liveness-probe.sh with correct timing parameters"""
    deploy = get_manifest_by_kind("Deployment")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "livenessProbe" in container, "Liveness probe missing from deployment"
    lp = container["livenessProbe"]
    
    assert "exec" in lp, "Liveness probe must be exec type"
    assert "command" in lp["exec"], "Liveness probe command missing"
    assert lp["exec"]["command"] == ["/kafka-liveness-probe.sh"], "Liveness probe uses incorrect command"
    
    # Verify timing config
    assert lp["initialDelaySeconds"] == 30, "Liveness probe initialDelaySeconds should be 30"
    assert lp["periodSeconds"] == 10, "Liveness probe periodSeconds should be 10"
    assert lp["timeoutSeconds"] == 5, "Liveness probe timeoutSeconds should be 5"
    assert lp["failureThreshold"] == 3, "Liveness probe failureThreshold should be 3"

def test_ac3_readiness_probe_configured_correctly():
    """AC-3: Deployment manifest includes readiness probe executing /kafka-readiness-probe.sh with correct timing parameters"""
    deploy = get_manifest_by_kind("Deployment")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "readinessProbe" in container, "Readiness probe missing from deployment"
    rp = container["readinessProbe"]
    
    assert "exec" in rp, "Readiness probe must be exec type"
    assert "command" in rp["exec"], "Readiness probe command missing"
    assert rp["exec"]["command"] == ["/kafka-readiness-probe.sh"], "Readiness probe uses incorrect command"
    
    # Verify timing config
    assert rp["initialDelaySeconds"] == 10, "Readiness probe initialDelaySeconds should be 10"
    assert rp["periodSeconds"] == 5, "Readiness probe periodSeconds should be 5"
    assert rp["timeoutSeconds"] == 5, "Readiness probe timeoutSeconds should be 5"
    assert rp["failureThreshold"] == 3, "Readiness probe failureThreshold should be 3"

def test_ac4_pvc_configured_correctly():
    """AC-4: PersistentVolumeClaim manifest requests minimum 10Gi storage with ReadWriteOnce access mode"""
    pvc = get_manifest_by_kind("PersistentVolumeClaim")
    
    assert "spec" in pvc
    assert "accessModes" in pvc["spec"], "PVC accessModes missing"
    assert "ReadWriteOnce" in pvc["spec"]["accessModes"], "PVC must have ReadWriteOnce access mode"
    
    assert "resources" in pvc["spec"]
    assert "requests" in pvc["spec"]["resources"]
    assert "storage" in pvc["spec"]["resources"]["requests"], "PVC storage request missing"
    
    storage_request = pvc["spec"]["resources"]["requests"]["storage"]
    # Parse storage value to numeric Gi
    if storage_request.endswith("Gi"):
        storage_gb = int(storage_request.replace("Gi", ""))
    elif storage_request.endswith("G"):
        storage_gb = int(storage_request.replace("G", ""))
    else:
        raise Exception(f"Unsupported storage unit in PVC: {storage_request}")
    
    assert storage_gb >= 10, f"PVC storage request {storage_request} is less than minimum required 10Gi"

def test_ac5_resource_requests_limits_configured():
    """AC-5: Deployment defines CPU and memory resource requests and limits within specified ranges"""
    deploy = get_manifest_by_kind("Deployment")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    
    assert "resources" in container, "Resources section missing from container spec"
    assert "requests" in container["resources"], "Resource requests missing"
    assert "limits" in container["resources"], "Resource limits missing"
    
    # Check CPU requests >=500m, limits <=2000m
    cpu_request = container["resources"]["requests"]["cpu"]
    if cpu_request.endswith("m"):
        cpu_request_m = int(cpu_request.replace("m", ""))
    else:
        cpu_request_m = int(float(cpu_request) * 1000)
    assert cpu_request_m >= 500, f"CPU request {cpu_request} is less than minimum required 500m"
    
    cpu_limit = container["resources"]["limits"]["cpu"]
    if cpu_limit.endswith("m"):
        cpu_limit_m = int(cpu_limit.replace("m", ""))
    else:
        cpu_limit_m = int(float(cpu_limit) * 1000)
    assert cpu_limit_m <= 2000, f"CPU limit {cpu_limit} exceeds maximum allowed 2000m"
    
    # Check memory requests >=1Gi, limits <=4Gi
    mem_request = container["resources"]["requests"]["memory"]
    if mem_request.endswith("Gi"):
        mem_request_gb = int(mem_request.replace("Gi", ""))
    elif mem_request.endswith("G"):
        mem_request_gb = int(mem_request.replace("G", ""))
    elif mem_request.endswith("Mi"):
        mem_request_gb = int(mem_request.replace("Mi", "")) / 1024
    else:
        raise Exception(f"Unsupported memory unit in requests: {mem_request}")
    assert mem_request_gb >= 1, f"Memory request {mem_request} is less than minimum required 1Gi"
    
    mem_limit = container["resources"]["limits"]["memory"]
    if mem_limit.endswith("Gi"):
        mem_limit_gb = int(mem_limit.replace("Gi", ""))
    elif mem_limit.endswith("G"):
        mem_limit_gb = int(mem_limit.replace("G", ""))
    elif mem_limit.endswith("Mi"):
        mem_limit_gb = int(mem_limit.replace("Mi", "")) / 1024
    else:
        raise Exception(f"Unsupported memory unit in limits: {mem_limit}")
    assert mem_limit_gb <= 4, f"Memory limit {mem_limit} exceeds maximum allowed 4Gi"

def test_ac6_security_context_configured():
    """AC-6: Deployment includes security context with non-root user, read-only root filesystem, no privilege escalation"""
    deploy = get_manifest_by_kind("Deployment")
    pod_spec = deploy["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    
    # Check security context (either pod-level or container-level is acceptable)
    sc = None
    if "securityContext" in container:
        sc = container["securityContext"]
    elif "securityContext" in pod_spec:
        sc = pod_spec["securityContext"]
    else:
        raise Exception("Security context missing from pod or container spec")
    
    assert sc.get("runAsNonRoot") == True, "Security context must have runAsNonRoot: true"
    assert sc.get("runAsUser") == 1000, f"Security context must run as UID 1000, got {sc.get('runAsUser')}"
    assert sc.get("runAsGroup") == 1000, f"Security context must run as GID 1000, got {sc.get('runAsGroup')}"
    assert sc.get("readOnlyRootFilesystem") == True, "Security context must have readOnlyRootFilesystem: true"
    assert sc.get("allowPrivilegeEscalation") == False, "Security context must have allowPrivilegeEscalation: false"
    
    # Verify required emptyDir volumes exist for writable paths
    volumes = {v["name"]: v for v in pod_spec.get("volumes", [])}
    volume_mounts = {vm["name"]: vm for vm in container.get("volumeMounts", [])}
    
    required_paths = ["/tmp", "/var/lib/kafka/runtime"]
    for path in required_paths:
        found = False
        for vm_name, vm in volume_mounts.items():
            if vm["mountPath"] == path:
                assert vm_name in volumes, f"Volume {vm_name} for mount path {path} not defined"
                assert "emptyDir" in volumes[vm_name], f"Volume for {path} must be emptyDir type"
                found = True
                break
        assert found, f"Missing volume mount for required writable path {path}"

def test_ac7_service_exposes_required_ports():
    """AC-7: Service exposes both port 9092 (plaintext) and 9093 (TLS) matching entrypoint configuration"""
    svc = get_manifest_by_kind("Service")
    
    assert svc["spec"]["type"] == "ClusterIP", "Service type must be ClusterIP"
    
    ports = {p["name"]: p for p in svc["spec"]["ports"]}
    
    # Check plaintext port 9092
    assert "plaintext" in ports, "Port named 'plaintext' missing from service"
    assert ports["plaintext"]["port"] == 9092, "Plaintext port should be 9092"
    assert ports["plaintext"]["targetPort"] == 9092, "Plaintext targetPort should be 9092"
    
    # Check TLS port 9093
    assert "tls" in ports, "Port named 'tls' missing from service"
    assert ports["tls"]["port"] == 9093, "TLS port should be 9093"
    assert ports["tls"]["targetPort"] == 9093, "TLS targetPort should be 9093"
    
    # Check deployment container exposes same ports
    deploy = get_manifest_by_kind("Deployment")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    container_ports = {p["containerPort"] for p in container.get("ports", [])}
    assert 9092 in container_ports, "Container does not expose port 9092"
    assert 9093 in container_ports, "Container does not expose port 9093"

def test_ac8_pod_runs_and_probes_pass():
    """AC-8: When applied to Kubernetes, Kafka pod reaches Running state and passes probes within 2 minutes"""
    try:
        # Apply manifests
        run_cmd(f"kubectl apply -f {KAFKA_MANIFEST_DIR} -n {TEST_NAMESPACE}")
        
        # Wait up to 2 minutes for pod to be running
        start_time = time.time()
        pod_running = False
        pod_name = None
        while time.time() - start_time < 120:
            result = run_cmd(f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].status.phase}}'", check=False)
            if result.returncode == 0 and result.stdout.strip() == "Running":
                pod_name = run_cmd(f"kubectl get pods -n {TEST_NAMESPACE} -l app={SERVICE_NAME} -o jsonpath='{{.items[0].metadata.name}}'").stdout.strip()
                pod_running = True
                break
            time.sleep(5)
        assert pod_running, "Pod did not enter Running state within 2 minutes"
        
        # Wait for probes to stabilize
        time.sleep(30)
        
        # Check probe status
        result = run_cmd(f"kubectl describe pod -n {TEST_NAMESPACE} {pod_name}")
        assert "Liveness probe failed" not in result.stdout, "Liveness probe is failing"
        assert "Readiness probe failed" not in result.stdout, "Readiness probe is failing"
        
        # Verify pod is ready
        result = run_cmd(f"kubectl get pod -n {TEST_NAMESPACE} {pod_name} -o jsonpath='{{.status.containerStatuses[0].ready}}'")
        assert result.stdout.strip() == "true", "Pod is not in ready state"
        
    finally:
        # Clean up resources
        run_cmd(f"kubectl delete -f {KAFKA_MANIFEST_DIR} -n {TEST_NAMESPACE}", check=False)
