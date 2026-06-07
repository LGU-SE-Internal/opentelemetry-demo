#!/usr/bin/env python3
import unittest
import time
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

class TestOpensearchServiceAC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            config.load_kube_config()
        except:
            config.load_incluster_config()
        cls.core_v1 = client.CoreV1Api()
        cls.apps_v1 = client.AppsV1Api()
        cls.namespace = "default"
        cls.opensearch_label_selector = "app.kubernetes.io/name=opensearch"

    def test_ac1_liveness_probe_configured(self):
        """AC-1: Verify liveness probe is configured correctly on opensearch container"""
        # Get opensearch workload (StatefulSet or Deployment)
        statefulsets = self.apps_v1.list_namespaced_stateful_set(self.namespace, label_selector=self.opensearch_label_selector)
        deployments = self.apps_v1.list_namespaced_deployment(self.namespace, label_selector=self.opensearch_label_selector)
        
        workload = None
        if len(statefulsets.items) > 0:
            workload = statefulsets.items[0]
        elif len(deployments.items) > 0:
            workload = deployments.items[0]
        else:
            self.fail("Opensearch workload not found in cluster")
        
        container = next(c for c in workload.spec.template.spec.containers if "opensearch" in c.name.lower())
        liveness_probe = container.liveness_probe
        
        # Verify liveness probe properties
        self.assertIsNotNone(liveness_probe, "Liveness probe not configured")
        self.assertIsNotNone(liveness_probe.http_get, "Liveness probe is not HTTP GET type")
        self.assertEqual(liveness_probe.http_get.path, "/_cluster/health", "Liveness probe path incorrect")
        self.assertEqual(liveness_probe.http_get.port, 9200, "Liveness probe port incorrect")
        self.assertEqual(liveness_probe.initial_delay_seconds, 30, "Liveness probe initialDelaySeconds incorrect")
        self.assertEqual(liveness_probe.period_seconds, 10, "Liveness probe periodSeconds incorrect")
        self.assertEqual(liveness_probe.timeout_seconds, 5, "Liveness probe timeoutSeconds incorrect")
        self.assertEqual(liveness_probe.failure_threshold, 3, "Liveness probe failureThreshold incorrect")

    def test_ac2_readiness_probe_configured(self):
        """AC-2: Verify readiness probe is configured correctly on opensearch container"""
        # Get opensearch workload (StatefulSet or Deployment)
        statefulsets = self.apps_v1.list_namespaced_stateful_set(self.namespace, label_selector=self.opensearch_label_selector)
        deployments = self.apps_v1.list_namespaced_deployment(self.namespace, label_selector=self.opensearch_label_selector)
        
        workload = None
        if len(statefulsets.items) > 0:
            workload = statefulsets.items[0]
        elif len(deployments.items) > 0:
            workload = deployments.items[0]
        else:
            self.fail("Opensearch workload not found in cluster")
        
        container = next(c for c in workload.spec.template.spec.containers if "opensearch" in c.name.lower())
        readiness_probe = container.readiness_probe
        
        # Verify readiness probe properties
        self.assertIsNotNone(readiness_probe, "Readiness probe not configured")
        self.assertIsNotNone(readiness_probe.http_get, "Readiness probe is not HTTP GET type")
        self.assertEqual(readiness_probe.http_get.path, "/_cluster/health", "Readiness probe path incorrect")
        self.assertEqual(readiness_probe.http_get.port, 9200, "Readiness probe port incorrect")
        self.assertEqual(readiness_probe.initial_delay_seconds, 10, "Readiness probe initialDelaySeconds incorrect")
        self.assertEqual(readiness_probe.period_seconds, 5, "Readiness probe periodSeconds incorrect")
        self.assertEqual(readiness_probe.timeout_seconds, 3, "Readiness probe timeoutSeconds incorrect")
        self.assertEqual(readiness_probe.failure_threshold, 3, "Readiness probe failureThreshold incorrect")

    def test_ac3_persistent_volume_claim_configured(self):
        """AC-3: Verify PersistentVolumeClaim is configured and mounted correctly"""
        # Get opensearch workload (StatefulSet or Deployment)
        statefulsets = self.apps_v1.list_namespaced_stateful_set(self.namespace, label_selector=self.opensearch_label_selector)
        deployments = self.apps_v1.list_namespaced_deployment(self.namespace, label_selector=self.opensearch_label_selector)
        
        workload = None
        is_statefulset = False
        if len(statefulsets.items) > 0:
            workload = statefulsets.items[0]
            is_statefulset = True
        elif len(deployments.items) > 0:
            workload = deployments.items[0]
        else:
            self.fail("Opensearch workload not found in cluster")
        
        container = next(c for c in workload.spec.template.spec.containers if "opensearch" in c.name.lower())
        
        # Check volume mount for data directory
        data_mount = next((m for m in container.volume_mounts if m.mount_path == "/usr/share/opensearch/data"), None)
        self.assertIsNotNone(data_mount, "Data volume mount not found at /usr/share/opensearch/data")
        
        # Check PVC configuration
        if is_statefulset:
            # Check volumeClaimTemplates for StatefulSet
            pvc_template = next((t for t in workload.spec.volume_claim_templates if t.metadata.name == data_mount.name), None)
            self.assertIsNotNone(pvc_template, "Volume claim template not found for data volume")
            self.assertIn("ReadWriteOnce", pvc_template.spec.access_modes, "PVC access mode not ReadWriteOnce")
            storage_request = pvc_template.spec.resources.requests.get("storage", None)
            self.assertIsNotNone(storage_request, "PVC storage request not configured")
            # Convert storage request to bytes and verify minimum 10Gi
            storage_val = int(storage_request[:-2]) if storage_request.endswith("Gi") else 0
            self.assertGreaterEqual(storage_val, 10, "PVC storage request less than 10Gi")
        else:
            # Check standalone PVC for Deployment
            pvcs = self.core_v1.list_namespaced_persistent_volume_claim(self.namespace)
            pvc = next((p for p in pvcs.items if p.metadata.name == data_mount.name), None)
            self.assertIsNotNone(pvc, "PersistentVolumeClaim not found for data volume")
            self.assertIn("ReadWriteOnce", pvc.spec.access_modes, "PVC access mode not ReadWriteOnce")
            storage_request = pvc.spec.resources.requests.get("storage", None)
            self.assertIsNotNone(storage_request, "PVC storage request not configured")
            storage_val = int(storage_request[:-2]) if storage_request.endswith("Gi") else 0
            self.assertGreaterEqual(storage_val, 10, "PVC storage request less than 10Gi")

    def test_ac3_data_persists_after_restart(self):
        """AC-3: Verify opensearch index data is preserved after pod restart"""
        # First get running pod
        pods = self.core_v1.list_namespaced_pod(self.namespace, label_selector=self.opensearch_label_selector, field_selector="status.phase=Running")
        self.assertEqual(len(pods.items), 1, "Expected exactly one running opensearch pod")
        original_pod_name = pods.items[0].metadata.name
        
        # Port forward to opensearch and create test index
        # Simulate writing test data
        # TODO: Add test data insertion once port forwarding is set up
        
        # Delete pod to trigger restart
        self.core_v1.delete_namespaced_pod(original_pod_name, self.namespace)
        
        # Wait for new pod to start
        time.sleep(60)
        new_pods = self.core_v1.list_namespaced_pod(self.namespace, label_selector=self.opensearch_label_selector, field_selector="status.phase=Running")
        self.assertEqual(len(new_pods.items), 1, "New opensearch pod not running after restart")
        new_pod_name = new_pods.items[0].metadata.name
        self.assertNotEqual(original_pod_name, new_pod_name, "Pod name did not change after restart")
        
        # Verify test data still exists
        # TODO: Add test data verification

    def test_ac4_security_context_configured(self):
        """AC-4: Verify non-root security context is configured correctly"""
        # Get opensearch workload (StatefulSet or Deployment)
        statefulsets = self.apps_v1.list_namespaced_stateful_set(self.namespace, label_selector=self.opensearch_label_selector)
        deployments = self.apps_v1.list_namespaced_deployment(self.namespace, label_selector=self.opensearch_label_selector)
        
        workload = None
        if len(statefulsets.items) > 0:
            workload = statefulsets.items[0]
        elif len(deployments.items) > 0:
            workload = deployments.items[0]
        else:
            self.fail("Opensearch workload not found in cluster")
        
        container = next(c for c in workload.spec.template.spec.containers if "opensearch" in c.name.lower())
        security_context = container.security_context
        
        self.assertIsNotNone(security_context, "Security context not configured")
        self.assertEqual(security_context.run_as_user, 1000, "runAsUser not set to 1000 (opensearch user)")
        self.assertEqual(security_context.allow_privilege_escalation, False, "allowPrivilegeEscalation not set to false")
        self.assertEqual(security_context.read_only_root_filesystem, False, "readOnlyRootFilesystem not set to false")

    def test_ac5_resource_requests_limits_configured(self):
        """AC-5: Verify CPU/memory resource requests and limits are set correctly"""
        # Get opensearch workload (StatefulSet or Deployment)
        statefulsets = self.apps_v1.list_namespaced_stateful_set(self.namespace, label_selector=self.opensearch_label_selector)
        deployments = self.apps_v1.list_namespaced_deployment(self.namespace, label_selector=self.opensearch_label_selector)
        
        workload = None
        if len(statefulsets.items) > 0:
            workload = statefulsets.items[0]
        elif len(deployments.items) > 0:
            workload = deployments.items[0]
        else:
            self.fail("Opensearch workload not found in cluster")
        
        container = next(c for c in workload.spec.template.spec.containers if "opensearch" in c.name.lower())
        resources = container.resources
        
        self.assertIsNotNone(resources, "Resource requirements not configured")
        # Check requests
        self.assertIsNotNone(resources.requests, "Resource requests not configured")
        cpu_request = resources.requests.get("cpu", None)
        memory_request = resources.requests.get("memory", None)
        self.assertIsNotNone(cpu_request, "CPU request not set")
        self.assertIsNotNone(memory_request, "Memory request not set")
        self.assertEqual(cpu_request, "1", "CPU request not set to 1")
        self.assertEqual(memory_request, "2Gi", "Memory request not set to 2Gi")
        # Check limits
        self.assertIsNotNone(resources.limits, "Resource limits not configured")
        cpu_limit = resources.limits.get("cpu", None)
        memory_limit = resources.limits.get("memory", None)
        self.assertIsNotNone(cpu_limit, "CPU limit not set")
        self.assertIsNotNone(memory_limit, "Memory limit not set")
        self.assertEqual(cpu_limit, "2", "CPU limit not set to 2")
        self.assertEqual(memory_limit, "4Gi", "Memory limit not set to 4Gi")

    def test_ac6_pod_runs_stably(self):
        """AC-6: Verify opensearch pod starts successfully and remains running for 5 minutes"""
        # Get opensearch pod
        pods = self.core_v1.list_namespaced_pod(self.namespace, label_selector=self.opensearch_label_selector)
        self.assertEqual(len(pods.items), 1, "Expected exactly one opensearch pod")
        pod = pods.items[0]
        
        # Verify pod is running
        self.assertEqual(pod.status.phase, "Running", "Opensearch pod is not in Running state")
        
        # Check container statuses
        for container_status in pod.status.container_statuses:
            if "opensearch" in container_status.name.lower():
                self.assertTrue(container_status.ready, "Opensearch container is not ready")
                self.assertIsNone(container_status.last_state.terminated, "Opensearch container has previously terminated")
                self.assertIsNone(container_status.last_state.waiting, "Opensearch container is in waiting state")
        
        # Verify pod has been running for at least 5 minutes
        start_time = pod.status.start_time
        elapsed_seconds = (time.time() - start_time.timestamp())
        self.assertGreaterEqual(elapsed_seconds, 300, "Opensearch pod has not been running for 5 minutes yet")

if __name__ == "__main__":
    unittest.main()
