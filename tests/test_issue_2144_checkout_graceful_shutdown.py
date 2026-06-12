#!/usr/bin/env python3
import unittest
import yaml
import subprocess
import time
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

CHECKOUT_DEPLOYMENT_PATH = "./kubernetes/checkout-service.deployment.yaml"
NAMESPACE = "default"
DEPLOYMENT_NAME = "checkout-service"

class TestCheckoutGracefulShutdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load kubernetes config
        try:
            config.load_kube_config()
        except:
            config.load_incluster_config()
        cls.apps_v1 = client.AppsV1Api()
        cls.core_v1 = client.CoreV1Api()

    def test_ac1_termination_grace_period_set_to_60s(self):
        """AC-1: Checkout service Kubernetes deployment explicitly has terminationGracePeriodSeconds set to 60"""
        # Check manifest file first
        with open(CHECKOUT_DEPLOYMENT_PATH, "r") as f:
            manifest = yaml.safe_load(f)
        self.assertIn("terminationGracePeriodSeconds", manifest["spec"]["template"]["spec"])
        self.assertEqual(manifest["spec"]["template"]["spec"]["terminationGracePeriodSeconds"], 60)
        
        # Check running deployment (if exists)
        try:
            deploy = self.apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
            self.assertEqual(deploy.spec.template.spec.termination_grace_period_seconds, 60)
        except ApiException as e:
            if e.status != 404:
                raise

    def test_ac2_prestop_hook_configured_with_sleep_10(self):
        """AC-2: Checkout service container in deployment has a preStop lifecycle hook configured to execute the `sleep 10` command"""
        # Check manifest file first
        with open(CHECKOUT_DEPLOYMENT_PATH, "r") as f:
            manifest = yaml.safe_load(f)
        containers = manifest["spec"]["template"]["spec"]["containers"]
        checkout_container = next(c for c in containers if c["name"] == "checkout-service")
        self.assertIn("lifecycle", checkout_container)
        self.assertIn("preStop", checkout_container["lifecycle"])
        self.assertIn("exec", checkout_container["lifecycle"]["preStop"])
        self.assertEqual(checkout_container["lifecycle"]["preStop"]["exec"]["command"], ["sleep", "10"])
        
        # Check running deployment (if exists)
        try:
            deploy = self.apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
            checkout_container = next(c for c in deploy.spec.template.spec.containers if c.name == "checkout-service")
            self.assertIsNotNone(checkout_container.lifecycle)
            self.assertIsNotNone(checkout_container.lifecycle.pre_stop)
            self.assertEqual(checkout_container.lifecycle.pre_stop.exec.command, ["sleep", "10"])
        except ApiException as e:
            if e.status != 404:
                raise

    def test_ac3_in_flight_request_completes_on_termination(self):
        """AC-3: When a checkout pod receives a termination signal while processing an order request that takes <50s to complete: Request completes successfully, no 5xx error returned to client"""
        # This test requires a running deployment, skip if not present
        try:
            pods = self.core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
            if not pods.items:
                self.skipTest("No checkout service pods running for integration test")
            
            # Get checkout service endpoint
            svc = self.core_v1.read_namespaced_service(DEPLOYMENT_NAME, NAMESPACE)
            endpoint = f"http://{svc.spec.cluster_ip}:8080/api/checkout"
            
            # Send a long-running order request (simulated 40s processing time)
            order_payload = {
                "user_id": "test-user-123",
                "items": [{"product_id": "product-1", "quantity": 1}],
                "simulate_delay_ms": 40000
            }
            request_start = time.time()
            response = None
            
            def send_request():
                nonlocal response
                try:
                    response = requests.post(endpoint, json=order_payload, timeout=55)
                except Exception as e:
                    response = e
            
            import threading
            t = threading.Thread(target=send_request)
            t.start()
            
            # Wait 2 seconds for request to be processing, then delete the pod
            time.sleep(2)
            target_pod = pods.items[0].metadata.name
            self.core_v1.delete_namespaced_pod(target_pod, NAMESPACE)
            
            # Wait for request to complete
            t.join(timeout=60)
            
            # Verify request completed successfully
            self.assertIsNotNone(response)
            if isinstance(response, Exception):
                self.fail(f"Request failed with exception: {str(response)}")
            self.assertLess(response.status_code, 500)
            self.assertGreaterEqual(response.status_code, 200)
            self.assertLess(time.time() - request_start, 55)
            
        except ApiException as e:
            self.skipTest(f"Kubernetes API error: {str(e)}")

    def test_ac4_kafka_messages_flushed_on_termination(self):
        """AC-4: When a checkout pod receives a termination signal while there are pending Kafka messages to publish: All pending messages are successfully committed to the configured Kafka topic before pod exit"""
        # This test requires running deployment and Kafka access, skip if not present
        try:
            from kafka import KafkaConsumer, TopicPartition
            
            # Get checkout service endpoint and Kafka config
            svc = self.core_v1.read_namespaced_service(DEPLOYMENT_NAME, NAMESPACE)
            endpoint = f"http://{svc.spec.cluster_ip}:8080/api/checkout"
            
            # Get Kafka bootstrap servers from deployment env
            deploy = self.apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
            checkout_container = next(c for c in deploy.spec.template.spec.containers if c.name == "checkout-service")
            kafka_bootstrap = next(e.value for e in checkout_container.env if e.name == "KAFKA_BOOTSTRAP_SERVERS")
            order_topic = next(e.value for e in checkout_container.env if e.name == "KAFKA_ORDER_TOPIC")
            
            # Create consumer to count existing messages
            consumer = KafkaConsumer(
                bootstrap_servers=kafka_bootstrap,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                group_id="test-ac4-group"
            )
            tp = TopicPartition(order_topic, 0)
            consumer.assign([tp])
            start_offset = consumer.end_offsets([tp])[tp]
            
            # Send 10 order requests that produce Kafka messages
            order_payload = {
                "user_id": "test-user-456",
                "items": [{"product_id": "product-2", "quantity": 1}],
                "simulate_kafka_delay_ms": 20000
            }
            
            responses = []
            def send_requests():
                for _ in range(10):
                    try:
                        res = requests.post(endpoint, json=order_payload, timeout=55)
                        responses.append(res)
                    except:
                        responses.append(None)
                    time.sleep(0.1)
            
            import threading
            t = threading.Thread(target=send_requests)
            t.start()
            
            # Wait 3 seconds for requests to be processing and messages queued, then delete the pod
            time.sleep(3)
            pods = self.core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
            target_pod = pods.items[0].metadata.name
            self.core_v1.delete_namespaced_pod(target_pod, NAMESPACE)
            
            # Wait for all requests to complete
            t.join(timeout=60)
            
            # Check all 10 messages are in Kafka
            time.sleep(5) # Allow time for messages to be committed
            end_offset = consumer.end_offsets([tp])[tp]
            self.assertEqual(end_offset - start_offset, 10)
            
            # Verify all requests succeeded
            self.assertEqual(len(responses), 10)
            for res in responses:
                self.assertIsNotNone(res)
                self.assertLess(res.status_code, 500)
            
        except (ApiException, ImportError) as e:
            self.skipTest(f"Test dependencies not met: {str(e)}")

    def test_ac5_prestop_hook_executes_full_10s_before_sigterm(self):
        """AC-5: PreStop hook executes for a full 10 seconds before the SIGTERM signal is delivered to the checkout service process"""
        # This test requires running deployment, skip if not present
        try:
            pods = self.core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
            if not pods.items:
                self.skipTest("No checkout service pods running for integration test")
            
            target_pod = pods.items[0].metadata.name
            
            # Replace checkout command temporarily to log when SIGTERM is received
            # First, get current pod creation timestamp
            pod = self.core_v1.read_namespaced_pod(target_pod, NAMESPACE)
            create_time = pod.metadata.creation_timestamp
            
            # Delete pod and measure time between preStop start and SIGTERM receipt
            delete_start = time.time()
            self.core_v1.delete_namespaced_pod(target_pod, NAMESPACE)
            
            # Wait for pod to terminate completely
            while True:
                try:
                    pod = self.core_v1.read_namespaced_pod(target_pod, NAMESPACE)
                    if pod.status.phase == "Succeeded" or pod.status.phase == "Failed":
                        break
                except ApiException as e:
                    if e.status == 404:
                        break
                time.sleep(0.5)
            
            # Get pod termination logs that include preStop and SIGTERM timings
            logs = subprocess.run(
                ["kubectl", "logs", target_pod, "-n", NAMESPACE, "--previous"],
                capture_output=True, text=True
            ).stdout
            
            # Parse timestamps from logs (service should log when SIGTERM is received)
            import re
            prestop_match = re.search(r"preStop hook started at (\d+\.\d+)", logs)
            sigterm_match = re.search(r"SIGTERM received at (\d+\.\d+)", logs)
            
            if prestop_match and sigterm_match:
                prestop_time = float(prestop_match.group(1))
                sigterm_time = float(sigterm_match.group(1))
                time_diff = sigterm_time - prestop_time
                # Allow +/- 1s tolerance
                self.assertGreaterEqual(time_diff, 9)
                self.assertLessEqual(time_diff, 11)
            else:
                # Fallback: measure total termination time should be at least 10s
                termination_duration = time.time() - delete_start
                self.assertGreaterEqual(termination_duration, 10)
            
        except ApiException as e:
            self.skipTest(f"Kubernetes API error: {str(e)}")

    def test_ac6_existing_deployment_config_unchanged(self):
        """AC-6: All existing deployment configuration (health checks, resource limits, environment variables, volume mounts, etc.) remains unchanged after applying the updates"""
        # Read current manifest
        with open(CHECKOUT_DEPLOYMENT_PATH, "r") as f:
            current = yaml.safe_load(f)
        
        # Get original manifest from git (main branch)
        original_content = subprocess.run(
            ["git", "show", f"origin/main:{CHECKOUT_DEPLOYMENT_PATH.lstrip('./')}"],
            capture_output=True, text=True
        ).stdout
        original = yaml.safe_load(original_content)
        
        # Remove the fields we explicitly change for comparison
        current_spec = current["spec"]["template"]["spec"]
        original_spec = original["spec"]["template"]["spec"]
        
        # Check terminationGracePeriodSeconds is the only change at pod spec level
        current_pod_keys = set(current_spec.keys())
        original_pod_keys = set(original_spec.keys())
        extra_keys = current_pod_keys - original_pod_keys
        if "terminationGracePeriodSeconds" not in original_spec:
            self.assertEqual(extra_keys, {"terminationGracePeriodSeconds"})
        else:
            self.assertEqual(extra_keys, set())
        
        # Check containers: only the lifecycle field is added/changed
        current_containers = current_spec["containers"]
        original_containers = original_spec["containers"]
        for curr_c, orig_c in zip(current_containers, original_containers):
            if curr_c["name"] != "checkout-service":
                # Other containers should be completely unchanged
                self.assertEqual(curr_c, orig_c)
                continue
            # Compare checkout container, ignoring lifecycle field
            curr_copy = curr_c.copy()
            orig_copy = orig_c.copy()
            curr_copy.pop("lifecycle", None)
            orig_copy.pop("lifecycle", None)
            self.assertEqual(curr_copy, orig_copy)

if __name__ == "__main__":
    unittest.main()
