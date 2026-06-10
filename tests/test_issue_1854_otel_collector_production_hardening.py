#!/usr/bin/env python3
import unittest
import kubernetes.client
from kubernetes.config import load_kube_config
import subprocess
import os
import time

class TestOtelCollectorProductionHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            load_kube_config()
        except:
            # Assume in-cluster config if kubeconfig not found
            kubernetes.config.load_incluster_config()
        cls.v1 = kubernetes.client.CoreV1Api()
        cls.apps_v1 = kubernetes.client.AppsV1Api()
        cls.policy_v1 = kubernetes.client.PolicyV1Api()
        cls.namespace = os.getenv("TEST_NAMESPACE", "default")
        cls.deployment_name = "otel-collector"

    def test_ac1_resource_constraints(self):
        """AC-1: Verify CPU/memory resource requests and limits are set correctly"""
        deploy = self.apps_v1.read_namespaced_deployment(self.deployment_name, self.namespace)
        container = deploy.spec.template.spec.containers[0]
        resources = container.resources
        
        self.assertIsNotNone(resources, "Resources field is missing from container spec")
        self.assertIsNotNone(resources.requests, "Resource requests are missing")
        self.assertIsNotNone(resources.limits, "Resource limits are missing")
        
        # Check CPU requests >= 200m
        cpu_req = resources.requests.get("cpu", "0")
        if cpu_req.endswith("m"):
            cpu_req_val = int(cpu_req[:-1])
        else:
            cpu_req_val = int(float(cpu_req) * 1000)
        self.assertGreaterEqual(cpu_req_val, 200, f"CPU request {cpu_req} is less than 200m")
        
        # Check CPU limits <= 2000m
        cpu_limit = resources.limits.get("cpu", "10000m")
        if cpu_limit.endswith("m"):
            cpu_limit_val = int(cpu_limit[:-1])
        else:
            cpu_limit_val = int(float(cpu_limit) * 1000)
        self.assertLessEqual(cpu_limit_val, 2000, f"CPU limit {cpu_limit} exceeds 2000m")
        
        # Check memory requests >= 512Mi
        mem_req = resources.requests.get("memory", "0Mi")
        if mem_req.endswith("Mi"):
            mem_req_val = int(mem_req[:-2])
        elif mem_req.endswith("Gi"):
            mem_req_val = int(mem_req[:-2]) * 1024
        self.assertGreaterEqual(mem_req_val, 512, f"Memory request {mem_req} is less than 512Mi")
        
        # Check memory limits <= 4Gi = 4096Mi
        mem_limit = resources.limits.get("memory", "10Gi")
        if mem_limit.endswith("Mi"):
            mem_limit_val = int(mem_limit[:-2])
        elif mem_limit.endswith("Gi"):
            mem_limit_val = int(mem_limit[:-2]) * 1024
        self.assertLessEqual(mem_limit_val, 4096, f"Memory limit {mem_limit} exceeds 4Gi")

    def test_ac2_liveness_probe(self):
        """AC-2: Verify liveness probe configuration"""
        deploy = self.apps_v1.read_namespaced_deployment(self.deployment_name, self.namespace)
        container = deploy.spec.template.spec.containers[0]
        liveness = container.liveness_probe
        
        self.assertIsNotNone(liveness, "Liveness probe is missing")
        self.assertEqual(liveness.http_get.path, "/", "Liveness probe path incorrect")
        self.assertEqual(liveness.http_get.port, 13133, "Liveness probe port incorrect")
        self.assertEqual(liveness.initial_delay_seconds, 30, "Liveness probe initial delay incorrect")
        self.assertEqual(liveness.period_seconds, 10, "Liveness probe period incorrect")
        self.assertEqual(liveness.failure_threshold, 3, "Liveness probe failure threshold incorrect")

    def test_ac3_readiness_probe(self):
        """AC-3: Verify readiness probe configuration"""
        deploy = self.apps_v1.read_namespaced_deployment(self.deployment_name, self.namespace)
        container = deploy.spec.template.spec.containers[0]
        readiness = container.readiness_probe
        
        self.assertIsNotNone(readiness, "Readiness probe is missing")
        self.assertEqual(readiness.http_get.path, "/", "Readiness probe path incorrect")
        self.assertEqual(readiness.http_get.port, 13133, "Readiness probe port incorrect")
        self.assertEqual(readiness.initial_delay_seconds, 5, "Readiness probe initial delay incorrect")
        self.assertEqual(readiness.period_seconds, 5, "Readiness probe period incorrect")
        self.assertEqual(readiness.failure_threshold, 3, "Readiness probe failure threshold incorrect")

    def test_ac4_pod_security_context(self):
        """AC-4: Verify pod-level security context configuration"""
        deploy = self.apps_v1.read_namespaced_deployment(self.deployment_name, self.namespace)
        pod_sec_ctx = deploy.spec.template.spec.security_context
        
        self.assertIsNotNone(pod_sec_ctx, "Pod security context is missing")
        self.assertEqual(pod_sec_ctx.run_as_non_root, True, "runAsNonRoot should be true")
        self.assertEqual(pod_sec_ctx.run_as_user, 10001, "runAsUser should be 10001")
        self.assertEqual(pod_sec_ctx.fs_group, 10001, "fsGroup should be 10001")

    def test_ac5_container_security_context(self):
        """AC-5: Verify container-level security context configuration"""
        deploy = self.apps_v1.read_namespaced_deployment(self.deployment_name, self.namespace)
        container = deploy.spec.template.spec.containers[0]
        container_sec_ctx = container.security_context
        
        self.assertIsNotNone(container_sec_ctx, "Container security context is missing")
        self.assertEqual(container_sec_ctx.read_only_root_filesystem, True, "readOnlyRootFilesystem should be true")
        self.assertEqual(container_sec_ctx.allow_privilege_escalation, False, "allowPrivilegeEscalation should be false")
        self.assertIn("ALL", container_sec_ctx.capabilities.drop, "Capabilities should drop ALL")

    def test_ac6_pvc_exists(self):
        """AC-6: Verify otel-collector-buffer PVC exists and is Bound"""
        pvc = self.v1.read_namespaced_persistent_volume_claim("otel-collector-buffer", self.namespace)
        self.assertEqual(pvc.status.phase, "Bound", "PVC is not in Bound state")

    def test_ac7_buffer_mount_permissions(self):
        """AC-7: Verify buffer volume is mounted correctly with proper permissions"""
        # Get collector pod name
        pods = self.v1.list_namespaced_pod(self.namespace, label_selector=f"app.kubernetes.io/name={self.deployment_name}")
        self.assertGreater(len(pods.items), 0, "No collector pods found")
        pod_name = pods.items[0].metadata.name
        
        # Test we can write to buffer directory
        result = subprocess.run(
            ["kubectl", "exec", "-n", self.namespace, pod_name, "--", "touch", "/var/lib/otelcol/buffer/testfile"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"Failed to write to buffer directory: {result.stderr}")
        
        # Test we cannot write to root filesystem
        result = subprocess.run(
            ["kubectl", "exec", "-n", self.namespace, pod_name, "--", "touch", "/testfile"],
            capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0, "Should not be able to write to root filesystem (read-only)")

    def test_ac8_pdb_exists(self):
        """AC-8: Verify PodDisruptionBudget exists with minAvailable >=1"""
        pdb = self.policy_v1.read_namespaced_pod_disruption_budget("otel-collector-pdb", self.namespace)
        self.assertIsNotNone(pdb.spec.min_available, "PDB should have minAvailable set")
        min_avail = pdb.spec.min_available
        if isinstance(min_avail, int):
            self.assertGreaterEqual(min_avail, 1, "minAvailable should be at least 1")
        else: # percentage
            # For deployments >= 2 replicas, minAvailable should be >= 50%
            self.assertEqual(min_avail, "50%", "minAvailable percentage should be 50%")

    def test_ac9_data_persistence_on_restart(self):
        """AC-9: Verify no data loss on collector restart"""
        # First, verify collector is running
        pods = self.v1.list_namespaced_pod(self.namespace, label_selector=f"app.kubernetes.io/name={self.deployment_name}")
        self.assertGreater(len(pods.items), 0, "No collector pods found")
        original_pod = pods.items[0].metadata.name
        
        # Send 100 test traces (simplified, assumes otelgen is available or mock sender)
        # Note: This part can be adjusted based on actual trace sending tooling
        send_result = subprocess.run(
            ["otelgen", "traces", "otlp", "--otlp-endpoint", "otel-collector:4317", "--count", "100", "--insecure"],
            capture_output=True, text=True
        )
        self.assertEqual(send_result.returncode, 0, f"Failed to send test traces: {send_result.stderr}")
        
        # Wait for traces to be buffered
        time.sleep(5)
        
        # Delete the pod to trigger restart
        self.v1.delete_namespaced_pod(original_pod, self.namespace)
        
        # Wait for new pod to be ready
        time.sleep(60)
        
        # Verify new pod is running
        new_pods = self.v1.list_namespaced_pod(self.namespace, label_selector=f"app.kubernetes.io/name={self.deployment_name}")
        self.assertGreater(len(new_pods.items), 0, "No collector pods found after restart")
        new_pod = new_pods.items[0].metadata.name
        self.assertNotEqual(new_pod, original_pod, "Pod was not restarted")
        
        # Verify all 100 traces were forwarded (simplified, assumes backend query tool is available)
        # Note: This part should be adjusted to query actual configured backend
        verify_result = subprocess.run(
            ["backend-query", "traces", "--count", "100", "--time-range", "5m"],
            capture_output=True, text=True
        )
        self.assertEqual(verify_result.returncode, 0, f"Failed to verify traces in backend: {verify_result.stderr}")
        self.assertIn("100 traces found", verify_result.stdout, "Not all traces were found after restart")

if __name__ == "__main__":
    unittest.main()
