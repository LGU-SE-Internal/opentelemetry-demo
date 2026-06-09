#!/usr/bin/env python3
import pytest
from kubernetes import client, config

# Load kubernetes config
config.load_kube_config()
apps_v1 = client.AppsV1Api()

POSTGRES_DEPLOYMENT_NAME = "postgresql"
NAMESPACE = "default"

def get_postgres_deployment():
    """Helper to get postgres deployment object"""
    return apps_v1.read_namespaced_deployment(name=POSTGRES_DEPLOYMENT_NAME, namespace=NAMESPACE)

@pytest.mark.ac1
def test_ac1_liveness_probe_configured_correctly():
    """AC-1: Liveness probe uses exec pg_isready -U postgres, initialDelaySeconds >=30, periodSeconds >=10"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.liveness_probe is not None, "Liveness probe not configured"
    assert container.liveness_probe.exec is not None, "Liveness probe is not exec type"
    assert "pg_isready" in container.liveness_probe.exec.command, "Liveness probe does not use pg_isready"
    assert "-U" in container.liveness_probe.exec.command, "Liveness probe does not specify user"
    assert "postgres" in container.liveness_probe.exec.command, "Liveness probe uses wrong user"
    assert container.liveness_probe.initial_delay_seconds >= 30, f"initialDelaySeconds {container.liveness_probe.initial_delay_seconds} < 30"
    assert container.liveness_probe.period_seconds >= 10, f"periodSeconds {container.liveness_probe.period_seconds} <10"

@pytest.mark.ac2
def test_ac2_readiness_probe_configured_correctly():
    """AC-2: Readiness probe uses exec pg_isready -U postgres, initialDelaySeconds >=5, periodSeconds >=5"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.readiness_probe is not None, "Readiness probe not configured"
    assert container.readiness_probe.exec is not None, "Readiness probe is not exec type"
    assert "pg_isready" in container.readiness_probe.exec.command, "Readiness probe does not use pg_isready"
    assert "-U" in container.readiness_probe.exec.command, "Readiness probe does not specify user"
    assert "postgres" in container.readiness_probe.exec.command, "Readiness probe uses wrong user"
    assert container.readiness_probe.initial_delay_seconds >= 5, f"initialDelaySeconds {container.readiness_probe.initial_delay_seconds} <5"
    assert container.readiness_probe.period_seconds >= 5, f"periodSeconds {container.readiness_probe.period_seconds} <5"

@pytest.mark.ac3
def test_ac3_resource_requests_non_zero():
    """AC-3: Deployment defines CPU and memory resource requests with non-zero values"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.resources is not None, "Resources section missing"
    assert container.resources.requests is not None, "Resource requests missing"
    assert "cpu" in container.resources.requests, "CPU request missing"
    assert container.resources.requests["cpu"] != "0" and container.resources.requests["cpu"] != 0, "CPU request is zero"
    assert "memory" in container.resources.requests, "Memory request missing"
    assert container.resources.requests["memory"] != "0" and container.resources.requests["memory"] != 0, "Memory request is zero"

@pytest.mark.ac4
def test_ac4_resource_limits_bounded_non_zero():
    """AC-4: Deployment defines CPU and memory resource limits with non-zero, bounded values"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.resources is not None, "Resources section missing"
    assert container.resources.limits is not None, "Resource limits missing"
    assert "cpu" in container.resources.limits, "CPU limit missing"
    assert container.resources.limits["cpu"] != "0" and container.resources.limits["cpu"] != 0, "CPU limit is zero"
    assert "memory" in container.resources.limits, "Memory limit missing"
    assert container.resources.limits["memory"] != "0" and container.resources.limits["memory"] != 0, "Memory limit is zero"

@pytest.mark.ac5
def test_ac5_security_context_run_as_non_root_uid_ge_1000():
    """AC-5: Pod security context runAsNonRoot: true, UID >=1000, no root processes"""
    deploy = get_postgres_deployment()
    pod_spec = deploy.spec.template.spec
    
    assert pod_spec.security_context is not None, "Pod security context missing"
    assert pod_spec.security_context.run_as_non_root == True, "runAsNonRoot not set to true"
    assert pod_spec.security_context.run_as_user >= 1000, f"runAsUser {pod_spec.security_context.run_as_user} < 1000"
    assert pod_spec.security_context.run_as_group >= 1000, f"runAsGroup {pod_spec.security_context.run_as_group} <1000"

@pytest.mark.ac6
def test_ac6_security_context_no_privilege_escalation():
    """AC-6: Security context allowPrivilegeEscalation: false, privileged: false"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.security_context is not None, "Container security context missing"
    assert container.security_context.allow_privilege_escalation == False, "allowPrivilegeEscalation not set to false"
    assert container.security_context.privileged == False, "privileged not set to false"

@pytest.mark.ac7
def test_ac7_read_only_root_filesystem_with_required_mounts():
    """AC-7: Security context readOnlyRootFilesystem: true, required emptyDir volumes mounted"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    pod_spec = deploy.spec.template.spec
    
    assert container.security_context.read_only_root_filesystem == True, "readOnlyRootFilesystem not set to true"
    
    # Check required volume mounts exist
    mount_paths = [mount.mount_path for mount in container.volume_mounts]
    assert "/var/run/postgresql" in mount_paths, "Missing mount for /var/run/postgresql"
    assert "/var/lib/postgresql/data" in mount_paths, "Missing mount for /var/lib/postgresql/data"
    
    # Check volumes are emptyDir type for writeable paths
    volume_names = {vol.name: vol for vol in pod_spec.volumes}
    for mount in container.volume_mounts:
        if mount.mount_path in ["/var/run/postgresql", "/var/lib/postgresql/data"]:
            assert volume_names[mount.name].empty_dir is not None, f"Volume {mount.name} for {mount.mount_path} is not emptyDir"

@pytest.mark.ac8
def test_ac8_no_tcp_probes_configured():
    """AC-8: Neither liveness nor readiness probe uses tcpSocket check"""
    deploy = get_postgres_deployment()
    container = deploy.spec.template.spec.containers[0]
    
    assert container.liveness_probe.tcp_socket is None, "Liveness probe uses tcpSocket (invalid)"
    assert container.readiness_probe.tcp_socket is None, "Readiness probe uses tcpSocket (invalid)"
