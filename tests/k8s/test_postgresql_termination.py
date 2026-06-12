import pytest
import yaml
import subprocess
import time
from kubernetes import client, config

# Load kube config
config.load_kube_config()
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()

NAMESPACE = "default"
STATEFULSET_NAME = "postgresql"
CONTAINER_NAME = "postgresql"
EXPECTED_PRESTOP_COMMAND = ["pg_ctl", "stop", "-m", "fast", "-D", "/var/lib/postgresql/data"]
MIN_GRACE_PERIOD = 60

def test_ac1_termination_grace_period_set():
    """AC-1: Verify postgresql statefulset has terminationGracePeriodSeconds >= 60"""
    try:
        sts = apps_v1.read_namespaced_stateful_set(STATEFULSET_NAME, NAMESPACE)
        grace_period = sts.spec.termination_grace_period_seconds
        assert grace_period is not None, "terminationGracePeriodSeconds is not set"
        assert grace_period >= MIN_GRACE_PERIOD, f"terminationGracePeriodSeconds is {grace_period}, expected >= {MIN_GRACE_PERIOD}"
    except client.exceptions.ApiException as e:
        if e.status == 404:
            # Check if it's a deployment instead (current repo uses deployment)
            dep = apps_v1.read_namespaced_deployment("postgres", NAMESPACE)
            grace_period = dep.spec.template.spec.termination_grace_period_seconds
            assert grace_period is not None, "terminationGracePeriodSeconds is not set"
            assert grace_period >= MIN_GRACE_PERIOD, f"terminationGracePeriodSeconds is {grace_period}, expected >= {MIN_GRACE_PERIOD}"
        else:
            raise

def test_ac2_prestop_hook_configured():
    """AC-2: Verify postgresql container has preStop hook with correct pg_ctl command"""
    try:
        sts = apps_v1.read_namespaced_stateful_set(STATEFULSET_NAME, NAMESPACE)
        container = next(c for c in sts.spec.template.spec.containers if c.name == CONTAINER_NAME)
    except client.exceptions.ApiException as e:
        if e.status == 404:
            dep = apps_v1.read_namespaced_deployment("postgres", NAMESPACE)
            container = next(c for c in dep.spec.template.spec.containers if c.name == "postgres")
        else:
            raise
    
    assert container.lifecycle is not None, "lifecycle is not configured"
    assert container.lifecycle.pre_stop is not None, "preStop hook is not configured"
    assert container.lifecycle.pre_stop.exec is not None, "preStop hook is not an exec command"
    assert container.lifecycle.pre_stop.exec.command == EXPECTED_PRESTOP_COMMAND, \
        f"preStop command is {container.lifecycle.pre_stop.exec.command}, expected {EXPECTED_PRESTOP_COMMAND}"

def test_ac3_prestop_runs_before_sigterm():
    """AC-3: Verify preStop hook completes before container receives SIGTERM"""
    # First get the pod name
    try:
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={STATEFULSET_NAME}")
        if not pods.items:
            pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app=postgres")
        pod_name = pods.items[0].metadata.name
    except IndexError:
        pytest.fail("No postgresql pod found running")
    
    # Create a test connection that would be killed on SIGTERM but closed cleanly by preStop
    test_conn_cmd = f"kubectl exec -n {NAMESPACE} {pod_name} -- su - postgres -c 'psql -c \"SELECT pg_sleep(30);\"'"
    conn_proc = subprocess.Popen(test_conn_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Wait a couple seconds for the connection to be active
    time.sleep(2)
    
    # Delete the pod to trigger termination
    delete_proc = subprocess.Popen(f"kubectl delete pod -n {NAMESPACE} {pod_name} --wait=false", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Check if the preStop hook is running by checking pg_ctl process
    time.sleep(3)
    check_prestop = subprocess.run(
        f"kubectl exec -n {NAMESPACE} {pod_name} -- ps aux | grep pg_ctl | grep -v grep",
        shell=True, capture_output=True, text=True
    )
    
    # Clean up
    conn_proc.terminate()
    delete_proc.terminate()
    
    assert check_prestop.returncode == 0, "pg_ctl process not found during termination, preStop hook did not run before SIGTERM"

def test_ac4_no_dirty_shutdown_warnings():
    """AC-4: Verify no improper shutdown warnings after controlled pod termination"""
    # Get a running pod
    try:
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={STATEFULSET_NAME}")
        if not pods.items:
            pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app=postgres")
        old_pod_name = pods.items[0].metadata.name
    except IndexError:
        pytest.fail("No postgresql pod found running")
    
    # Delete the pod to trigger clean shutdown
    subprocess.run(f"kubectl delete pod -n {NAMESPACE} {old_pod_name} --wait", shell=True, check=True)
    
    # Wait for new pod to be ready
    time.sleep(30)
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app=postgres")
    new_pod_name = pods.items[0].metadata.name
    
    # Check logs for shutdown warning
    logs = subprocess.run(
        f"kubectl logs -n {NAMESPACE} {new_pod_name} postgres",
        shell=True, capture_output=True, text=True, check=True
    ).stdout
    
    assert "database was not shut down properly" not in logs, "Found improper shutdown warning in logs"
    assert "incomplete transaction rollback" not in logs, "Found incomplete transaction rollback message in logs"

def test_ac5_connections_closed_cleanly():
    """AC-5: Verify all active connections are closed cleanly during shutdown"""
    # Get running pod
    try:
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app=postgres")
        pod_name = pods.items[0].metadata.name
    except IndexError:
        pytest.fail("No postgresql pod found running")
    
    # Create 5 test connections
    conns = []
    for i in range(5):
        proc = subprocess.Popen(
            f"kubectl exec -n {NAMESPACE} {pod_name} -- su - postgres -c 'psql -c \"SELECT pg_backend_pid(); SELECT pg_sleep(60);\"'",
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        time.sleep(1)
        conns.append(proc)
    
    # Count active connections
    active_before = subprocess.run(
        f"kubectl exec -n {NAMESPACE} {pod_name} -- su - postgres -c 'psql -t -c \"SELECT count(*) FROM pg_stat_activity WHERE state = \\'active\\' AND backend_type = \\'client backend\\';\"'",
        shell=True, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert int(active_before) >= 5, f"Expected at least 5 active connections, found {active_before}"
    
    # Delete the pod
    subprocess.run(f"kubectl delete pod -n {NAMESPACE} {pod_name} --wait", shell=True, check=True)
    
    # Wait for new pod
    time.sleep(30)
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app=postgres")
    new_pod_name = pods.items[0].metadata.name
    
    # Check no orphaned connections (there should be 0 client backends active)
    active_after = subprocess.run(
        f"kubectl exec -n {NAMESPACE} {new_pod_name} -- su - postgres -c 'psql -t -c \"SELECT count(*) FROM pg_stat_activity WHERE state = \\'active\\' AND backend_type = \\'client backend\\';\"'",
        shell=True, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert int(active_after) == 0, f"Found {active_after} orphaned active connections after restart"
    
    # Clean up connection processes
    for conn in conns:
        conn.terminate()

def test_ac6_existing_config_unchanged():
    """AC-6: Verify existing postgresql statefulset/deployment configuration remains unchanged"""
    # Read the current manifest from the repo
    with open("kubernetes/postgres/postgres-deployment.yaml", "r") as f:
        original_manifest = yaml.safe_load(f)
    
    # Check all existing fields are preserved
    # Check volumes
    assert "volumes" in original_manifest["spec"]["template"]["spec"], "No volumes found in original manifest"
    # Check resource limits
    assert "resources" in original_manifest["spec"]["template"]["spec"]["containers"][0], "No resources found in original manifest"
    # Check environment variables
    assert "env" in original_manifest["spec"]["template"]["spec"]["containers"][0], "No env vars found in original manifest"
    # Check service port remains 5432
    with open("kubernetes/postgres/postgres-service.yaml", "r") as f:
        service_manifest = yaml.safe_load(f)
    assert service_manifest["spec"]["ports"][0]["port"] == 5432, "Service port should remain 5432"
