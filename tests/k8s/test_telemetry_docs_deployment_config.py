#!/usr/bin/env python3
import pytest
from kubernetes import client, config
import time
import subprocess

# Configure k8s client
config.load_kube_config()
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()

DEPLOYMENT_NAME = "telemetry-docs"
NAMESPACE = "default"  # Adjust if deployment uses different namespace
NGINX_PORT = 8080  # Expected Nginx exposed port


class TestTelemetryDocsDeployment:
    def test_ac1_liveness_probe_configured(self):
        """AC-1: livenessProbe exists with correct HTTP GET path, port and timings"""
        deployment = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
        container = deployment.spec.template.spec.containers[0]
        assert container.liveness_probe is not None, "livenessProbe not configured"
        assert container.liveness_probe.http_get is not None, "livenessProbe not HTTP GET type"
        assert container.liveness_probe.http_get.path == "/", "livenessProbe path not /"
        assert container.liveness_probe.http_get.port == NGINX_PORT, "livenessProbe port does not match Nginx port"
        assert container.liveness_probe.period_seconds == 10, "livenessProbe periodSeconds not default 10"
        assert container.liveness_probe.failure_threshold == 3, "livenessProbe failureThreshold not default 3"

    def test_ac2_readiness_probe_configured(self):
        """AC-2: readinessProbe exists with correct HTTP GET path, port and timings"""
        deployment = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
        container = deployment.spec.template.spec.containers[0]
        assert container.readiness_probe is not None, "readinessProbe not configured"
        assert container.readiness_probe.http_get is not None, "readinessProbe not HTTP GET type"
        assert container.readiness_probe.http_get.path == "/", "readinessProbe path not /"
        assert container.readiness_probe.http_get.port == NGINX_PORT, "readinessProbe port does not match Nginx port"
        assert container.readiness_probe.period_seconds == 10, "readinessProbe periodSeconds not default 10"
        assert container.readiness_probe.failure_threshold == 3, "readinessProbe failureThreshold not default 3"

    def test_ac3_resource_requirements_configured(self):
        """AC-3: CPU and memory resource requests/limits set to specified values"""
        deployment = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
        container = deployment.spec.template.spec.containers[0]
        assert container.resources is not None, "resources not configured"
        assert container.resources.requests is not None, "resource requests not set"
        assert container.resources.limits is not None, "resource limits not set"
        assert container.resources.requests["cpu"] == "10m", "CPU request not 10m"
        assert container.resources.limits["cpu"] == "100m", "CPU limit not 100m"
        assert container.resources.requests["memory"] == "32Mi", "Memory request not 32Mi"
        assert container.resources.limits["memory"] == "128Mi", "Memory limit not 128Mi"

    def test_ac4_security_context_configured(self):
        """AC-4: Security context enforces non-root, read-only fs, no privilege escalation, no capabilities"""
        deployment = apps_v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)
        pod_sec_ctx = deployment.spec.template.spec.security_context
        container_sec_ctx = deployment.spec.template.spec.containers[0].security_context
        
        # Check pod-level and container-level security config
        assert (pod_sec_ctx and pod_sec_ctx.run_as_non_root) or (container_sec_ctx and container_sec_ctx.run_as_non_root), "runAsNonRoot not set to true"
        assert container_sec_ctx is not None, "Container security context not configured"
        assert container_sec_ctx.read_only_root_filesystem == True, "readOnlyRootFilesystem not set to true"
        assert container_sec_ctx.allow_privilege_escalation == False, "allowPrivilegeEscalation not set to false"
        assert container_sec_ctx.capabilities is not None, "Capabilities not configured"
        assert container_sec_ctx.capabilities.drop == ["ALL"], "Not all Linux capabilities dropped"

    def test_ac5_pods_run_and_pass_probes(self):
        """AC-5: All pods reach Running status and pass probes within 60 seconds"""
        # Wait for pods to be ready
        start_time = time.time()
        while time.time() - start_time < 60:
            pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
            if len(pods.items) == 0:
                time.sleep(2)
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
            if all_ready:
                return
            time.sleep(2)
        pytest.fail("Pods did not reach Running status and pass probes within 60 seconds")

    def test_ac6_nginx_runs_as_non_root(self):
        """AC-6: Nginx process runs as non-root user (UID != 0)"""
        pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
        assert len(pods.items) > 0, "No running telemetry-docs pods found"
        pod_name = pods.items[0].metadata.name
        
        # Execute command to get Nginx UID
        exec_command = [
            "/bin/sh",
            "-c",
            "ps -o uid= $(pgrep nginx | head -1) | tr -d ' '"
        ]
        resp = core_v1.connect_get_namespaced_pod_exec(
            pod_name,
            NAMESPACE,
            command=exec_command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        nginx_uid = int(resp.strip())
        assert nginx_uid != 0, f"Nginx is running as root (UID {nginx_uid})"

    def test_ac7_no_root_write_access_or_privilege_escalation(self):
        """AC-7: No write access to root filesystem, cannot escalate privileges to root"""
        pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
        assert len(pods.items) > 0, "No running telemetry-docs pods found"
        pod_name = pods.items[0].metadata.name
        
        # Test write to root filesystem
        write_test_command = [
            "/bin/sh",
            "-c",
            "touch /test_write 2>&1 || echo 'write failed'"
        ]
        write_resp = core_v1.connect_get_namespaced_pod_exec(
            pod_name,
            NAMESPACE,
            command=write_test_command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        assert "Permission denied" in write_resp or "read-only file system" in write_resp, "Can write to root filesystem"
        
        # Test privilege escalation attempt
        su_test_command = [
            "/bin/sh",
            "-c",
            "su root 2>&1 || echo 'su failed'"
        ]
        su_resp = core_v1.connect_get_namespaced_pod_exec(
            pod_name,
            NAMESPACE,
            command=su_test_command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        assert "Permission denied" in su_resp or "not permitted" in su_resp, "Can escalate privileges to root"
