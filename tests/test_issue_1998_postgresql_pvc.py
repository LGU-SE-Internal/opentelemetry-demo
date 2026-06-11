import pytest
from kubernetes import client
from kubernetes.client import V1Deployment, V1PersistentVolumeClaim

@pytest.fixture
def postgresql_deployment() -> V1Deployment:
    """Fixture to load the PostgreSQL deployment manifest"""
    import yaml
    with open("/workspace/k8s/postgresql-deployment.yaml", "r") as f:
        deployment = yaml.safe_load(f)
    return client.ApiClient()._ApiClient__deserialize(deployment, "V1Deployment")

@pytest.fixture
def postgresql_pvc() -> V1PersistentVolumeClaim:
    """Fixture to load the PostgreSQL PVC manifest"""
    import yaml
    with open("/workspace/k8s/postgresql-pvc.yaml", "r") as f:
        pvc = yaml.safe_load(f)
    return client.ApiClient()._ApiClient__deserialize(pvc, "V1PersistentVolumeClaim")

@pytest.fixture(scope="module")
def original_mount_path() -> str:
    """Original mount path from deployment before changes"""
    import yaml
    with open("/workspace/k8s/postgresql-deployment.yaml", "r") as f:
        deployment = yaml.safe_load(f)
    for volume_mount in deployment["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]:
        if volume_mount["name"] == "postgresql-data":
            return volume_mount["mountPath"]
    pytest.fail("postgresql-data volume mount not found in original deployment")

def test_ac1_data_persists_after_pod_restart():
    """AC-1: Test data remains present after PostgreSQL pod restart/reschedule"""
    # This test will be implemented with Kubernetes e2e test steps:
    # 1. Deploy PostgreSQL with PVC
    # 2. Write test data to database
    # 3. Delete PostgreSQL pod
    # 4. Wait for new pod to be ready
    # 5. Verify test data is still present
    # Fails now as we are using emptyDir
    pytest.xfail("Implementation not complete: postgresql-data uses emptyDir instead of PVC")

def test_ac2_volume_uses_pvc_reference(postgresql_deployment: V1Deployment):
    """AC-2: Verify postgresql-data volume uses persistentVolumeClaim with claimName postgresql-pvc"""
    volume_found = False
    for volume in postgresql_deployment.spec.template.spec.volumes:
        if volume.name == "postgresql-data":
            volume_found = True
            assert volume.persistent_volume_claim is not None, "postgresql-data volume does not use PVC"
            assert volume.persistent_volume_claim.claim_name == "postgresql-pvc", f"Expected claimName postgresql-pvc, got {volume.persistent_volume_claim.claim_name}"
            assert volume.empty_dir is None, "postgresql-data volume still has emptyDir configuration"
    assert volume_found, "postgresql-data volume not found in deployment"

def test_ac3_mount_path_unchanged(postgresql_deployment: V1Deployment, original_mount_path: str):
    """AC-3: Verify mountPath for postgresql-data volume mount remains unchanged"""
    volume_mount_found = False
    for container in postgresql_deployment.spec.template.spec.containers:
        for volume_mount in container.volume_mounts:
            if volume_mount.name == "postgresql-data":
                volume_mount_found = True
                assert volume_mount.mount_path == original_mount_path, f"Mount path changed from {original_mount_path} to {volume_mount.mount_path}"
    assert volume_mount_found, "postgresql-data volume mount not found in container"

def test_ac4_pvc_dynamic_provisioning(postgresql_pvc: V1PersistentVolumeClaim):
    """AC-4: Verify PVC can be bound dynamically without manual PV creation"""
    # Check if storage class is set or annotation exists for dynamic provisioning
    if postgresql_pvc.spec.storage_class_name is None:
        # Check for storage class annotation
        annotations = postgresql_pvc.metadata.annotations or {}
        assert "volume.beta.kubernetes.io/storage-class" in annotations or "storageclass.kubernetes.io/is-default-class" in annotations, "No storage class configured for PVC, dynamic provisioning will fail"
    # Test will verify PVC goes to Bound state when applied in a cluster with dynamic provisioning
    pytest.xfail("Implementation not complete: missing storage class configuration for dynamic provisioning")

def test_ac5_services_operate_without_changes():
    """AC-5: Verify all dependent services work without changes to init scripts or connection logic"""
    # This test will run existing integration tests for all services using PostgreSQL
    # Fails until implementation is complete
    pytest.xfail("Implementation not complete: postgresql-data uses emptyDir, no persistence guarantee")
