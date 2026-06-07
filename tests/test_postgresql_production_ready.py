#!/usr/bin/env python3
import pytest
import subprocess
import time
import psycopg2
from kubernetes import client, config
from kubernetes.stream import stream

# Load kubernetes config
config.load_kube_config()
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

POSTGRES_LABEL_SELECTOR = "app.kubernetes.io/name=postgresql"
POSTGRES_DB = "postgres"
POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = "postgres"
TEST_TABLE_NAME = "test_persistence_table"

def get_postgres_pod():
    """Helper to get running postgres pod"""
    pods = v1.list_namespaced_pod(namespace="default", label_selector=POSTGRES_LABEL_SELECTOR)
    for pod in pods.items:
        if pod.status.phase == "Running":
            return pod
    return None

def port_forward_postgres(local_port=5432):
    """Helper to port forward to postgres pod"""
    pod = get_postgres_pod()
    if not pod:
        raise Exception("No running postgres pod found")
    proc = subprocess.Popen(
        ["kubectl", "port-forward", pod.metadata.name, f"{local_port}:5432"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    # Wait for port forward to be ready
    time.sleep(5)
    return proc

@pytest.mark.ac1
def test_ac1_liveness_readiness_probes():
    """Test AC1: Liveness and readiness probes report success/failure correctly on port 5432"""
    # Get deployment
    deploy = apps_v1.read_namespaced_deployment(name="postgresql", namespace="default")
    
    # Check probes exist
    assert deploy.spec.template.spec.containers[0].liveness_probe is not None, "No liveness probe configured"
    assert deploy.spec.template.spec.containers[0].readiness_probe is not None, "No readiness probe configured"
    
    # Check probes are tcpSocket on port 5432
    liveness = deploy.spec.template.spec.containers[0].liveness_probe
    assert liveness.tcp_socket is not None, "Liveness probe is not TCP socket type"
    assert liveness.tcp_socket.port == 5432, "Liveness probe not targeting port 5432"
    
    readiness = deploy.spec.template.spec.containers[0].readiness_probe
    assert readiness.tcp_socket is not None, "Readiness probe is not TCP socket type"
    assert readiness.tcp_socket.port == 5432, "Readiness probe not targeting port 5432"
    
    # Check probe configuration values match baseline
    for probe in [liveness, readiness]:
        assert probe.initial_delay_seconds >= 30, "Initial delay too short for postgres startup"
        assert probe.period_seconds >= 10, "Probe period too short"
        assert probe.timeout_seconds >=5, "Probe timeout too short"
        assert probe.failure_threshold >=3, "Failure threshold too low"
    
    # Verify pod status shows probes passing
    pod = get_postgres_pod()
    assert pod is not None, "No running postgres pod found"
    for condition in pod.status.conditions:
        if condition.type == "Ready":
            assert condition.status == "True", "Postgres pod is not ready, probes failing"

@pytest.mark.ac2
def test_ac2_data_persistence_on_pod_restart():
    """Test AC2: Data persists after pod deletion and recreation"""
    # First create test data
    pf_proc = port_forward_postgres()
    try:
        conn = psycopg2.connect(
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host="localhost",
            port=5432
        )
        cur = conn.cursor()
        
        # Create test table and insert data
        cur.execute(f"CREATE TABLE IF NOT EXISTS {TEST_TABLE_NAME} (id SERIAL PRIMARY KEY, test_value VARCHAR(255))")
        test_value = f"test_persist_{int(time.time())}"
        cur.execute(f"INSERT INTO {TEST_TABLE_NAME} (test_value) VALUES ('{test_value}')")
        conn.commit()
        
        # Get inserted ID
        cur.execute(f"SELECT id FROM {TEST_TABLE_NAME} WHERE test_value = '{test_value}'")
        inserted_id = cur.fetchone()[0]
        cur.close()
        conn.close()
    finally:
        pf_proc.terminate()
        pf_proc.wait()
    
    # Delete the pod
    pod = get_postgres_pod()
    assert pod is not None
    v1.delete_namespaced_pod(name=pod.metadata.name, namespace="default", body=client.V1DeleteOptions())
    
    # Wait for pod to be recreated and running
    time.sleep(30)
    new_pod = None
    for _ in range(10):
        new_pod = get_postgres_pod()
        if new_pod and new_pod.metadata.uid != pod.metadata.uid and new_pod.status.phase == "Running":
            break
        time.sleep(10)
    assert new_pod is not None, "New postgres pod not created after deletion"
    
    # Verify data still exists
    pf_proc_new = port_forward_postgres()
    try:
        conn = psycopg2.connect(
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host="localhost",
            port=5432
        )
        cur = conn.cursor()
        cur.execute(f"SELECT test_value FROM {TEST_TABLE_NAME} WHERE id = {inserted_id}")
        result = cur.fetchone()
        assert result is not None, "Test data lost after pod restart"
        assert result[0] == test_value, "Test data corrupted after pod restart"
        cur.execute(f"DROP TABLE {TEST_TABLE_NAME}")
        conn.commit()
        cur.close()
        conn.close()
    finally:
        pf_proc_new.terminate()
        pf_proc_new.wait()

@pytest.mark.ac3
def test_ac3_non_root_user_execution():
    """Test AC3: PostgreSQL process runs as non-root user (UID != 0)"""
    pod = get_postgres_pod()
    assert pod is not None
    
    # Execute id -u in pod
    exec_command = [
        "/bin/sh",
        "-c",
        "id -u"
    ]
    resp = stream(v1.connect_get_namespaced_pod_exec,
                  pod.metadata.name,
                  "default",
                  command=exec_command,
                  stderr=True, stdin=False,
                  stdout=True, tty=False)
    
    uid = int(resp.strip())
    assert uid != 0, f"Postgres running as root user (UID={uid})"
    assert uid == 999, f"Postgres running as wrong non-root UID: {uid}, expected 999 (upstream standard)"

@pytest.mark.ac4
def test_ac4_data_directory_permissions():
    """Test AC4: /var/lib/postgresql/data has correct read/write permissions for non-root user"""
    pod = get_postgres_pod()
    assert pod is not None
    
    # Check directory owner and permissions
    exec_command = [
        "/bin/sh",
        "-c",
        "stat -c '%u %a' /var/lib/postgresql/data"
    ]
    resp = stream(v1.connect_get_namespaced_pod_exec,
                  pod.metadata.name,
                  "default",
                  command=exec_command,
                  stderr=True, stdin=False,
                  stdout=True, tty=False)
    
    uid, perms = resp.strip().split()
    uid = int(uid)
    perms = int(perms)
    
    assert uid == 999, f"Data directory owned by wrong UID: {uid}, expected 999"
    assert (perms & 0o700) == 0o700, f"Data directory has insufficient permissions: {oct(perms)}, need read/write/execute for owner"
    
    # Verify we can write to the directory as non-root user
    test_file = "/var/lib/postgresql/data/test_perms.tmp"
    exec_command_write = [
        "/bin/sh",
        "-c",
        f"echo 'test' > {test_file} && cat {test_file} && rm {test_file}"
    ]
    resp_write = stream(v1.connect_get_namespaced_pod_exec,
                       pod.metadata.name,
                       "default",
                       command=exec_command_write,
                       stderr=True, stdin=False,
                       stdout=True, tty=False)
    assert "test" in resp_write, "Cannot write to data directory as non-root user"

@pytest.mark.ac5
def test_ac5_resource_requests_limits_configured():
    """Test AC5: Explicit CPU and memory requests and limits are configured"""
    deploy = apps_v1.read_namespaced_deployment(name="postgresql", namespace="default")
    resources = deploy.spec.template.spec.containers[0].resources
    
    assert resources is not None, "No resources field configured"
    assert resources.requests is not None, "No resource requests configured"
    assert resources.limits is not None, "No resource limits configured"
    
    # Check CPU requests/limits
    assert "cpu" in resources.requests, "No CPU request configured"
    assert "cpu" in resources.limits, "No CPU limit configured"
    
    # Check memory requests/limits
    assert "memory" in resources.requests, "No memory request configured"
    assert "memory" in resources.limits, "No memory limit configured"
    
    # Check baseline values (at least the minimum recommended)
    def parse_cpu(cpu_val):
        if cpu_val.endswith("m"):
            return int(cpu_val[:-1])
        return int(float(cpu_val) * 1000)
    
    def parse_mem(mem_val):
        if mem_val.endswith("Mi"):
            return int(mem_val[:-2])
        if mem_val.endswith("Gi"):
            return int(mem_val[:-2]) * 1024
        return int(mem_val) / (1024 * 1024)
    
    assert parse_cpu(resources.requests["cpu"]) >= 256, "CPU request too low, minimum 256m"
    assert parse_cpu(resources.limits["cpu"]) >= 500, "CPU limit too low, minimum 500m"
    assert parse_mem(resources.requests["memory"]) >= 512, "Memory request too low, minimum 512Mi"
    assert parse_mem(resources.limits["memory"]) >= 1024, "Memory limit too low, minimum 1Gi"

@pytest.mark.ac6
def test_ac6_dependent_services_functionality():
    """Test AC6: All dependent services connecting to PostgreSQL function normally"""
    # Get all pods connecting to postgres service
    postgres_svc = v1.read_namespaced_service(name="postgresql", namespace="default")
    assert postgres_svc is not None, "PostgreSQL service not found"
    
    # Test all other services can connect to postgres port
    dependent_services = [
        "checkoutservice",
        "paymentservice",
        "orderservice",
        "productservice"
    ]
    
    for svc in dependent_services:
        try:
            # Get pod for service
            pods = v1.list_namespaced_pod(namespace="default", label_selector=f"app.kubernetes.io/name={svc}")
            if not pods.items:
                continue
            test_pod = next(p for p in pods.items if p.status.phase == "Running")
            
            # Test connection to postgres service port 5432
            exec_cmd = [
                "/bin/sh",
                "-c",
                f"nc -zv postgresql.default.svc.cluster.local 5432 -w 5"
            ]
            resp = stream(v1.connect_get_namespaced_pod_exec,
                          test_pod.metadata.name,
                          "default",
                          command=exec_cmd,
                          stderr=True, stdin=False,
                          stdout=True, tty=False)
            assert "succeeded" in resp.lower() or "open" in resp.lower(), f"Service {svc} cannot connect to PostgreSQL"
        except Exception as e:
            pytest.fail(f"Service {svc} failed PostgreSQL connectivity check: {str(e)}")
