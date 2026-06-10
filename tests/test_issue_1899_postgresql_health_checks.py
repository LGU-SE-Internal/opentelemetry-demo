#!/usr/bin/env python3
import pytest
import docker
import time
from testcontainers.postgres import PostgresContainer
import os

TEST_SCRIPT_PATH = "/usr/local/bin/pg_healthcheck.sh"
DOCKERFILE_PATH = "src/postgresql/Dockerfile"

@pytest.fixture(scope="module")
def postgres_image():
    client = docker.from_env()
    # Build the postgres image from local Dockerfile
    image, _ = client.images.build(path="src/postgresql", tag="test-postgres-healthcheck:latest")
    yield image
    client.images.remove(image.id, force=True)

@pytest.fixture
def running_postgres_container(postgres_image):
    client = docker.from_env()
    container = client.containers.run(
        postgres_image.id,
        environment={"POSTGRES_PASSWORD": "testpass"},
        detach=True,
        ports={"5432/tcp": None}
    )
    # Wait a bit for postgres to start
    time.sleep(10)
    yield container
    container.stop()
    container.remove(force=True)

def test_ac1_healthcheck_returns_zero_when_healthy(running_postgres_container):
    # AC-1: Healthy instance returns exit code 0
    exit_code, output = running_postgres_container.exec_run(TEST_SCRIPT_PATH, user="postgres")
    assert exit_code == 0, f"Health check failed with output: {output.decode()}"

def test_ac2_healthcheck_returns_one_when_process_not_running(running_postgres_container):
    # AC-2: Process not running returns exit code 1
    # Stop postgres process first
    running_postgres_container.exec_run("pg_ctl stop -D /var/lib/postgresql/data", user="postgres")
    time.sleep(2)
    exit_code, output = running_postgres_container.exec_run(TEST_SCRIPT_PATH, user="postgres")
    assert exit_code == 1, f"Health check should fail when postgres is stopped, got exit code {exit_code}"

def test_ac3_healthcheck_returns_one_when_connection_refused(postgres_image):
    # AC-3: Connection refused returns exit code 1
    # Start container with entrypoint override to not run postgres
    client = docker.from_env()
    container = client.containers.run(
        postgres_image.id,
        entrypoint=["sleep", "infinity"],
        detach=True
    )
    time.sleep(2)
    exit_code, output = container.exec_run(TEST_SCRIPT_PATH, user="postgres")
    container.stop()
    container.remove(force=True)
    assert exit_code == 1, f"Health check should fail when no postgres is running, got exit code {exit_code}"

def test_ac4_healthcheck_returns_one_when_query_fails(running_postgres_container):
    # AC-4: Query failure returns exit code 1
    # Rename data dir to make SELECT 1 fail
    running_postgres_container.exec_run("mv /var/lib/postgresql/data /var/lib/postgresql/data_bak", user="root")
    time.sleep(2)
    exit_code, output = running_postgres_container.exec_run(TEST_SCRIPT_PATH, user="postgres")
    # Restore data dir to avoid issues with container stop
    running_postgres_container.exec_run("mv /var/lib/postgresql/data_bak /var/lib/postgresql/data", user="root")
    assert exit_code == 1, f"Health check should fail when query fails, got exit code {exit_code}"

def test_ac5_healthcheck_runs_as_unprivileged_postgres_user(running_postgres_container):
    # AC-5: Script runs as unprivileged postgres user
    # Check that we can run it as postgres, not requiring root
    exit_code, output = running_postgres_container.exec_run(f"ls -l {TEST_SCRIPT_PATH}", user="postgres")
    assert exit_code == 0, f"Cannot access health check script as postgres user"
    # Try running as non-postgres non-root user to ensure it works only for postgres? No, just check runs as postgres
    exit_code, output = running_postgres_container.exec_run(TEST_SCRIPT_PATH, user="postgres")
    assert exit_code in [0, 1], f"Script failed to execute as postgres user, exit code {exit_code}"
    # Check that it doesn't require root to execute
    exit_code, output = running_postgres_container.exec_run(f"su - postgres -c {TEST_SCRIPT_PATH}", user="root")
    assert exit_code in [0, 1], f"Script requires root privileges to execute"

def test_ac6_dockerfile_has_correct_healthcheck_instruction():
    # AC-6: Dockerfile has HEALTHCHECK with correct parameters
    with open(DOCKERFILE_PATH, "r") as f:
        dockerfile_content = f.read()
    
    assert "HEALTHCHECK" in dockerfile_content, "HEALTHCHECK instruction missing from Dockerfile"
    assert "--interval=10s" in dockerfile_content, "HEALTHCHECK missing interval=10s"
    assert "--timeout=5s" in dockerfile_content, "HEALTHCHECK missing timeout=5s"
    assert "--start-period=30s" in dockerfile_content, "HEALTHCHECK missing start-period=30s"
    assert "--retries=3" in dockerfile_content, "HEALTHCHECK missing retries=3"
    assert TEST_SCRIPT_PATH in dockerfile_content, f"HEALTHCHECK does not reference {TEST_SCRIPT_PATH}"

def test_ac7_healthy_container_reports_healthy_status(running_postgres_container):
    # AC-7: Docker inspect reports healthy status for running postgres
    # Wait for health check to run a few times
    time.sleep(20)
    inspection = running_postgres_container.inspect()
    health_status = inspection["State"]["Health"]["Status"]
    assert health_status == "healthy", f"Expected healthy status, got {health_status}"

def test_ac8_unhealthy_container_reports_unhealthy_status(running_postgres_container):
    # AC-8: Docker inspect reports unhealthy status for stopped postgres
    running_postgres_container.exec_run("pg_ctl stop -D /var/lib/postgresql/data", user="postgres")
    # Wait for health check to fail enough times
    time.sleep(40)
    inspection = running_postgres_container.inspect()
    health_status = inspection["State"]["Health"]["Status"]
    assert health_status == "unhealthy", f"Expected unhealthy status, got {health_status}"

def test_ac9_healthcheck_works_for_k8s_liveness_probe(running_postgres_container):
    # AC-9: Script works for k8s liveness probe exec context
    # Same as running the script directly, k8s exec runs it the same way
    exit_code, output = running_postgres_container.exec_run(TEST_SCRIPT_PATH, user="postgres")
    # When instance is ready, should return 0
    assert exit_code == 0, f"Liveness probe failed when instance is ready, exit code {exit_code}"

def test_ac10_healthcheck_works_for_k8s_readiness_probe(running_postgres_container):
    # AC-10: Script works for k8s readiness probe exec context
    # Same as running the script directly, k8s exec runs it the same way
    exit_code, output = running_postgres_container.exec_run(TEST_SCRIPT_PATH, user="postgres")
    # When instance is ready, should return 0
    assert exit_code == 0, f"Readiness probe failed when instance is ready, exit code {exit_code}"
