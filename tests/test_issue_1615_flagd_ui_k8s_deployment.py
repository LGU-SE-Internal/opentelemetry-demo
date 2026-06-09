#!/usr/bin/env python3
import unittest
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
import requests

class TestFlagdUIK8sDeployment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        cls.apps_v1 = client.AppsV1Api()
        cls.core_v1 = client.CoreV1Api()
        cls.namespace = "default"  # Replace with test namespace if needed
        cls.deployment_name = "flagd-ui"
        cls.service_name = "flagd-ui"

    def test_ac1_deployment_exists_with_ready_pod(self):
        """AC-1: Deployment named flagd-ui exists with exactly 1 ready pod after startup"""
        # Check if deployment exists
        try:
            deployment = self.apps_v1.read_namespaced_deployment(
                name=self.deployment_name,
                namespace=self.namespace
            )
        except ApiException as e:
            self.fail(f"Deployment {self.deployment_name} not found: {e}")

        # Verify replica count is 1
        self.assertEqual(deployment.spec.replicas, 1, "Default replica count should be 1")

        # Wait up to 2 minutes for pod to be ready
        ready_pods = 0
        for _ in range(24):
            pods = self.core_v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"app.kubernetes.io/name={self.deployment_name}"
            )
            ready_pods = sum(1 for p in pods.items if all(c.ready for c in p.status.container_statuses or []))
            if ready_pods == 1:
                break
            time.sleep(5)
        
        self.assertEqual(ready_pods, 1, "Should have exactly 1 ready pod after startup")

    def test_ac2_probes_configured_correctly(self):
        """AC-2: Liveness and readiness probes point to /health on port 4000"""
        deployment = self.apps_v1.read_namespaced_deployment(
            name=self.deployment_name,
            namespace=self.namespace
        )
        container = deployment.spec.template.spec.containers[0]
        self.assertEqual(container.name, self.deployment_name)

        # Check liveness probe
        self.assertIsNotNone(container.liveness_probe, "Liveness probe missing")
        self.assertIsNotNone(container.liveness_probe.http_get, "Liveness probe should be HTTP GET")
        self.assertEqual(container.liveness_probe.http_get.path, "/health", "Liveness probe path should be /health")
        self.assertEqual(container.liveness_probe.http_get.port, 4000, "Liveness probe port should be 4000")
        self.assertEqual(container.liveness_probe.initial_delay_seconds, 10, "Liveness initial delay should be 10s")
        self.assertEqual(container.liveness_probe.period_seconds, 30, "Liveness period should be 30s")

        # Check readiness probe
        self.assertIsNotNone(container.readiness_probe, "Readiness probe missing")
        self.assertIsNotNone(container.readiness_probe.http_get, "Readiness probe should be HTTP GET")
        self.assertEqual(container.readiness_probe.http_get.path, "/health", "Readiness probe path should be /health")
        self.assertEqual(container.readiness_probe.http_get.port, 4000, "Readiness probe port should be 4000")
        self.assertEqual(container.readiness_probe.initial_delay_seconds, 5, "Readiness initial delay should be 5s")
        self.assertEqual(container.readiness_probe.period_seconds, 10, "Readiness period should be 10s")

    def test_ac3_resource_requests_limits_configured(self):
        """AC-3: CPU/memory requests and limits are set correctly"""
        deployment = self.apps_v1.read_namespaced_deployment(
            name=self.deployment_name,
            namespace=self.namespace
        )
        container = deployment.spec.template.spec.containers[0]
        resources = container.resources

        # Check requests
        self.assertIsNotNone(resources.requests, "Resource requests missing")
        self.assertEqual(resources.requests.get("cpu"), "100m", "CPU request should be 100m")
        self.assertEqual(resources.requests.get("memory"), "128Mi", "Memory request should be 128Mi")

        # Check limits
        self.assertIsNotNone(resources.limits, "Resource limits missing")
        self.assertEqual(resources.limits.get("cpu"), "500m", "CPU limit should be 500m")
        self.assertEqual(resources.limits.get("memory"), "256Mi", "Memory limit should be 256Mi")

    def test_ac4_security_context_hardened(self):
        """AC-4: Security context enforces non-root, no privilege escalation, dropped capabilities, RuntimeDefault seccomp"""
        deployment = self.apps_v1.read_namespaced_deployment(
            name=self.deployment_name,
            namespace=self.namespace
        )
        container = deployment.spec.template.spec.containers[0]
        security_context = container.security_context

        self.assertIsNotNone(security_context, "Security context missing")
        self.assertTrue(security_context.run_as_non_root, "runAsNonRoot should be true")
        self.assertEqual(security_context.run_as_user, 1000, "runAsUser should be 1000")
        self.assertFalse(security_context.allow_privilege_escalation, "allowPrivilegeEscalation should be false")
        self.assertEqual(security_context.capabilities.drop, ["ALL"], "All capabilities should be dropped")
        self.assertIsNotNone(security_context.seccomp_profile, "Seccomp profile missing")
        self.assertEqual(security_context.seccomp_profile.type, "RuntimeDefault", "Seccomp profile should be RuntimeDefault")

    def test_ac5_service_exists_correct_config(self):
        """AC-5: ClusterIP Service exists listening on port 80, targetPort 4000"""
        try:
            service = self.core_v1.read_namespaced_service(
                name=self.service_name,
                namespace=self.namespace
            )
        except ApiException as e:
            self.fail(f"Service {self.service_name} not found: {e}")

        self.assertEqual(service.spec.type, "ClusterIP", "Service type should be ClusterIP")
        port = service.spec.ports[0]
        self.assertEqual(port.port, 80, "Service port should be 80")
        self.assertEqual(port.target_port, 4000, "Target port should be 4000")
        self.assertEqual(port.name, "http", "Port name should be http")
        self.assertEqual(service.spec.selector.get("app.kubernetes.io/name"), self.deployment_name, "Service selector should match deployment pods")

    def test_ac6_service_reachable_internally(self):
        """AC-6: Service is reachable internally via cluster DNS"""
        # Test by sending request to service DNS
        url = f"http://{self.service_name}.{self.namespace}.svc.cluster.local/health"
        try:
            response = requests.get(url, timeout=10)
            self.assertEqual(response.status_code, 200, "Service should respond with 200 OK")
        except Exception as e:
            self.fail(f"Failed to reach service at {url}: {e}")

    def test_ac7_health_endpoint_returns_200(self):
        """AC-7: /health endpoint returns HTTP 200 status code"""
        # Get pod IP
        pods = self.core_v1.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=f"app.kubernetes.io/name={self.deployment_name}"
        )
        self.assertTrue(len(pods.items) > 0, "No pods found for flagd-ui")
        pod_ip = pods.items[0].status.pod_ip

        # Query health endpoint directly from pod
        url = f"http://{pod_ip}:4000/health"
        try:
            response = requests.get(url, timeout=10)
            self.assertEqual(response.status_code, 200, "/health endpoint should return 200 OK")
        except Exception as e:
            self.fail(f"Failed to query /health endpoint at {url}: {e}")

if __name__ == "__main__":
    unittest.main()
