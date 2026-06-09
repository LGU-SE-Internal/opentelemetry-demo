import os
import time
import subprocess
import pytest
import requests
from kubernetes import client, config

@pytest.fixture(scope="module")
def k8s_client():
    try:
        config.load_incluster_config()
    except:
        config.load_kube_config()
    return client.AppsV1Api()

def test_ac1_default_graceful_shutdown_timeout_30s():
    """AC-1: Default 30s graceful shutdown timeout, 15s long request completes on SIGTERM"""
    # Start frontend-proxy container without custom timeout env var
    proxy_proc = subprocess.Popen(
        ["envoy", "-c", "/workspace/src/frontend-proxy/envoy.tmpl.yaml", "--concurrency", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for proxy to start
    time.sleep(5)
    
    # Start long-running request in background
    def run_long_request():
        try:
            resp = requests.get("http://localhost:8080/health", timeout=20)
            return resp.status_code == 200
        except:
            return False
    
    import threading
    request_thread = threading.Thread(target=run_long_request)
    request_thread.start()
    # Wait 5s for request to be in flight
    time.sleep(5)
    
    # Send SIGTERM to Envoy
    proxy_proc.terminate()
    
    # Wait for request to complete
    request_thread.join(timeout=20)
    assert request_thread.is_alive() == False, "Request should complete within default 30s timeout"
    
    # Cleanup
    proxy_proc.wait(timeout=10)

def test_ac2_custom_graceful_shutdown_timeout_60s():
    """AC-2: Custom 60s timeout via FRONTEND_PROXY_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS env var"""
    env = os.environ.copy()
    env["FRONTEND_PROXY_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS"] = "60"
    
    proxy_proc = subprocess.Popen(
        ["envoy", "-c", "/workspace/src/frontend-proxy/envoy.tmpl.yaml", "--concurrency", "2"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(5)
    
    def run_long_request():
        try:
            resp = requests.get("http://localhost:8080/health", timeout=50)
            return resp.status_code == 200
        except:
            return False
    
    import threading
    request_thread = threading.Thread(target=run_long_request)
    request_thread.start()
    time.sleep(5)
    
    proxy_proc.terminate()
    
    request_thread.join(timeout=50)
    assert request_thread.is_alive() == False, "Request should complete within custom 60s timeout"
    
    proxy_proc.wait(timeout=10)

def test_ac3_request_exceeds_timeout_terminated():
    """AC-3: Requests longer than configured timeout are terminated after timeout"""
    env = os.environ.copy()
    env["FRONTEND_PROXY_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS"] = "10"
    
    proxy_proc = subprocess.Popen(
        ["envoy", "-c", "/workspace/src/frontend-proxy/envoy.tmpl.yaml", "--concurrency", "2"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(5)
    
    request_failed = False
    def run_long_request():
        nonlocal request_failed
        try:
            requests.get("http://localhost:8080/health", timeout=20)
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            request_failed = True
    
    import threading
    request_thread = threading.Thread(target=run_long_request)
    request_thread.start()
    time.sleep(2)
    
    proxy_proc.terminate()
    
    # Wait 12s (longer than 10s timeout)
    time.sleep(12)
    assert request_failed == True, "Request should fail after 10s timeout elapses"
    
    proxy_proc.wait(timeout=10)

def test_ac4_normal_operation_unchanged():
    """AC-4: Normal operation without shutdown works as expected"""
    proxy_proc = subprocess.Popen(
        ["envoy", "-c", "/workspace/src/frontend-proxy/envoy.tmpl.yaml", "--concurrency", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(5)
    
    # Send multiple normal requests
    for _ in range(10):
        resp = requests.get("http://localhost:8080/health", timeout=5)
        assert resp.status_code == 200, "Normal requests should return 200 OK"
    
    proxy_proc.terminate()
    proxy_proc.wait(timeout=10)

def test_ac5_k8s_termination_grace_period_ge_35s(k8s_client):
    """AC-5: Kubernetes frontend-proxy deployment has terminationGracePeriodSeconds >= 35"""
    deployments = k8s_client.list_namespaced_deployment(namespace="default")
    frontend_proxy_deploy = None
    for deploy in deployments.items:
        if "frontend-proxy" in deploy.metadata.name:
            frontend_proxy_deploy = deploy
            break
    assert frontend_proxy_deploy is not None, "Frontend proxy deployment not found"
    assert frontend_proxy_deploy.spec.template.spec.termination_grace_period_seconds >= 35, \
        f"Termination grace period should be >= 35s, got {frontend_proxy_deploy.spec.template.spec.termination_grace_period_seconds}"

def test_ac6_new_connections_rejected_after_sigterm():
    """AC-6: New connections are rejected after SIGTERM, existing in-flight requests complete"""
    proxy_proc = subprocess.Popen(
        ["envoy", "-c", "/workspace/src/frontend-proxy/envoy.tmpl.yaml", "--concurrency", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(5)
    
    # Start existing long running request
    existing_request_success = False
    def run_existing_request():
        nonlocal existing_request_success
        try:
            resp = requests.get("http://localhost:8080/health", timeout=15)
            existing_request_success = (resp.status_code == 200)
        except:
            existing_request_success = False
    
    import threading
    existing_thread = threading.Thread(target=run_existing_request)
    existing_thread.start()
    time.sleep(2)
    
    # Send SIGTERM
    proxy_proc.terminate()
    
    # Try to open new connection immediately
    new_connection_failed = False
    try:
        requests.get("http://localhost:8080/health", timeout=2)
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
        new_connection_failed = True
    
    assert new_connection_failed == True, "New connections should be rejected after SIGTERM"
    
    # Wait for existing request to complete
    existing_thread.join(timeout=20)
    assert existing_request_success == True, "Existing in-flight request should complete successfully"
    
    proxy_proc.wait(timeout=10)
