import yaml
import subprocess
import pytest

DEPLOYMENT_PATH = "k8s/jaeger-deployment.yaml"
PVC_PATH = "k8s/jaeger-pvc.yaml"

@pytest.fixture
def deployment_manifest():
    with open(DEPLOYMENT_PATH, "r") as f:
        return list(yaml.safe_load_all(f))[0]

@pytest.fixture
def pvc_manifest():
    with open(PVC_PATH, "r") as f:
        return list(yaml.safe_load_all(f))[0]

@pytest.fixture
def jaeger_container(deployment_manifest):
    containers = deployment_manifest["spec"]["template"]["spec"]["containers"]
    for c in containers:
        if c["name"] == "jaeger":
            return c
    pytest.fail("Jaeger container not found in deployment manifest")

def test_ac1_resource_limits_and_requests(jaeger_container):
    # AC-1: Explicit resource requests (min 0.5 CPU, 512Mi memory) and limits (max 2 CPU, 2Gi memory)
    assert "resources" in jaeger_container, "resources field missing from container"
    resources = jaeger_container["resources"]
    
    assert "requests" in resources, "resources.requests missing"
    requests = resources["requests"]
    assert "cpu" in requests, "cpu request missing"
    # Convert CPU value to cores (handle m units)
    cpu_req = requests["cpu"]
    if cpu_req.endswith("m"):
        cpu_core_req = int(cpu_req.rstrip("m")) / 1000
    else:
        cpu_core_req = float(cpu_req)
    assert cpu_core_req >= 0.5, f"CPU request {cpu_req} is less than minimum 0.5 cores"
    
    assert "memory" in requests, "memory request missing"
    mem_req = requests["memory"]
    if mem_req.endswith("Mi"):
        mem_mib_req = int(mem_req.rstrip("Mi"))
    elif mem_req.endswith("Gi"):
        mem_mib_req = int(mem_req.rstrip("Gi")) * 1024
    else:
        pytest.fail(f"Unsupported memory request unit: {mem_req}")
    assert mem_mib_req >= 512, f"Memory request {mem_req} is less than minimum 512Mi"
    
    assert "limits" in resources, "resources.limits missing"
    limits = resources["limits"]
    assert "cpu" in limits, "cpu limit missing"
    cpu_limit = limits["cpu"]
    if cpu_limit.endswith("m"):
        cpu_core_limit = int(cpu_limit.rstrip("m")) / 1000
    else:
        cpu_core_limit = float(cpu_limit)
    assert cpu_core_limit <= 2.0, f"CPU limit {cpu_limit} exceeds maximum 2 cores"
    
    assert "memory" in limits, "memory limit missing"
    mem_limit = limits["memory"]
    if mem_limit.endswith("Mi"):
        mem_mib_limit = int(mem_limit.rstrip("Mi"))
    elif mem_limit.endswith("Gi"):
        mem_mib_limit = int(mem_limit.rstrip("Gi")) * 1024
    else:
        pytest.fail(f"Unsupported memory limit unit: {mem_limit}")
    assert mem_mib_limit <= 2048, f"Memory limit {mem_limit} exceeds maximum 2Gi"

def test_ac2_security_context_non_root(jaeger_container, deployment_manifest):
    # AC-2: Security context sets runAsNonRoot: true, runAsUser: 10001, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true
    assert "securityContext" in jaeger_container, "container securityContext missing"
    sc = jaeger_container["securityContext"]
    
    assert sc.get("runAsNonRoot") == True, "runAsNonRoot is not set to true"
    assert sc.get("runAsUser") == 10001, f"runAsUser is {sc.get('runAsUser')}, expected 10001"
    assert sc.get("allowPrivilegeEscalation") == False, "allowPrivilegeEscalation is not set to false"
    assert sc.get("readOnlyRootFilesystem") == True, "readOnlyRootFilesystem is not set to true"

def test_ac3_health_probes(jaeger_container):
    # AC-3: Liveness and readiness probes targeting port 14269 /health, initialDelay=30, period=10, failureThreshold=3
    for probe_type in ["livenessProbe", "readinessProbe"]:
        assert probe_type in jaeger_container, f"{probe_type} missing from container"
        probe = jaeger_container[probe_type]
        
        assert "httpGet" in probe, f"{probe_type} does not use httpGet"
        http_get = probe["httpGet"]
        assert http_get.get("port") == 14269, f"{probe_type} port is {http_get.get('port')}, expected 14269"
        assert http_get.get("path") == "/health", f"{probe_type} path is {http_get.get('path')}, expected /health"
        
        assert probe.get("initialDelaySeconds") == 30, f"{probe_type} initialDelaySeconds is {probe.get('initialDelaySeconds')}, expected 30"
        assert probe.get("periodSeconds") == 10, f"{probe_type} periodSeconds is {probe.get('periodSeconds')}, expected 10"
        assert probe.get("failureThreshold") == 3, f"{probe_type} failureThreshold is {probe.get('failureThreshold')}, expected 3"

def test_ac4_persistent_volume_claim(deployment_manifest, jaeger_container, pvc_manifest):
    # AC-4: PVC with ReadWriteOnce access mode, min 10Gi storage, mounted to /data
    # Check PVC spec
    assert "spec" in pvc_manifest, "PVC spec missing"
    pvc_spec = pvc_manifest["spec"]
    assert "accessModes" in pvc_spec, "PVC accessModes missing"
    assert "ReadWriteOnce" in pvc_spec["accessModes"], "PVC accessModes does not include ReadWriteOnce"
    
    assert "resources" in pvc_spec, "PVC resources missing"
    assert "requests" in pvc_spec["resources"], "PVC resources.requests missing"
    assert "storage" in pvc_spec["resources"]["requests"], "PVC storage request missing"
    storage_req = pvc_spec["resources"]["requests"]["storage"]
    if storage_req.endswith("Gi"):
        storage_gib = int(storage_req.rstrip("Gi"))
    elif storage_req.endswith("Mi"):
        storage_gib = int(storage_req.rstrip("Mi")) / 1024
    else:
        pytest.fail(f"Unsupported storage unit: {storage_req}")
    assert storage_gib >= 10, f"PVC storage request {storage_req} is less than minimum 10Gi"
    
    # Check volume in deployment
    volumes = deployment_manifest["spec"]["template"]["spec"]["volumes"]
    pvc_volume = None
    for vol in volumes:
        if "persistentVolumeClaim" in vol and vol["persistentVolumeClaim"]["claimName"] == pvc_manifest["metadata"]["name"]:
            pvc_volume = vol
            break
    assert pvc_volume is not None, "PVC not referenced in deployment volumes"
    
    # Check volume mount in container
    assert "volumeMounts" in jaeger_container, "container volumeMounts missing"
    volume_mounts = jaeger_container["volumeMounts"]
    data_mount = None
    for mount in volume_mounts:
        if mount["mountPath"] == "/data":
            data_mount = mount
            break
    assert data_mount is not None, "Volume not mounted to /data path in container"
    assert data_mount["name"] == pvc_volume["name"], "Mounted volume name does not match PVC volume name"

def test_ac5_container_hardening(jaeger_container, deployment_manifest):
    # AC-5: privileged: false, seccomp and AppArmor annotations
    assert "securityContext" in jaeger_container, "container securityContext missing"
    sc = jaeger_container["securityContext"]
    assert sc.get("privileged") == False, "privileged is not set to false"
    
    annotations = deployment_manifest["metadata"].get("annotations", {})
    assert "seccomp.security.alpha.kubernetes.io/pod" in annotations, "seccomp pod annotation missing"
    assert annotations["seccomp.security.alpha.kubernetes.io/pod"] == "runtime/default", "seccomp annotation value incorrect"
    
    assert "container.apparmor.security.beta.kubernetes.io/jaeger" in annotations, "AppArmor container annotation missing"
    assert annotations["container.apparmor.security.beta.kubernetes.io/jaeger"] == "runtime/default", "AppArmor annotation value incorrect"

def test_ac6_valid_kubernetes_yaml():
    # AC-6: Manifest applies successfully with kubectl --dry-run=client
    result = subprocess.run(
        ["kubectl", "apply", "-f", DEPLOYMENT_PATH, "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl apply dry-run failed: {result.stderr}"
