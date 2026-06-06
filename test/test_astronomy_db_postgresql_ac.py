#!/usr/bin/env python3
import subprocess
import time
import psycopg2
import pytest

TEST_DB_NAME = "astronomy"
TEST_DB_USER = "postgres"
TEST_DB_PASSWORD = "postgres"
TEST_DB_PORT = 5432
TEST_TABLE_NAME = "test_persistence_table"
TEST_INSERT_VALUE = "test_data_1234"

def run_cmd(cmd, shell=True, check=False):
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()

@pytest.fixture(scope="function")
def cleanup_test_table():
    # Cleanup before test
    run_cmd(f"docker exec astronomy-db psql -U {TEST_DB_USER} -d {TEST_DB_NAME} -c 'DROP TABLE IF EXISTS {TEST_TABLE_NAME};'")
    yield
    # Cleanup after test
    run_cmd(f"docker exec astronomy-db psql -U {TEST_DB_USER} -d {TEST_DB_NAME} -c 'DROP TABLE IF EXISTS {TEST_TABLE_NAME};'")

def test_ac1_healthcheck_returns_healthy_within_60s():
    """AC-1: docker inspect shows health status healthy within 60s of start"""
    start_time = time.time()
    max_wait = 60
    healthy = False
    
    while time.time() - start_time < max_wait:
        ret, stdout, stderr = run_cmd("docker inspect --format '{{.State.Health.Status}}' astronomy-db")
        if ret == 0 and stdout == "healthy":
            healthy = True
            break
        time.sleep(2)
    
    assert healthy, f"PostgreSQL container did not become healthy within {max_wait} seconds. Last status: {stdout}, error: {stderr}"

def test_ac2_data_persists_after_container_recreate(cleanup_test_table):
    """AC-2: Data persists after container is stopped, removed and recreated"""
    # First insert test data
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=TEST_DB_NAME,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            host="localhost",
            port=TEST_DB_PORT
        )
        cur = conn.cursor()
        cur.execute(f"CREATE TABLE {TEST_TABLE_NAME} (value TEXT);")
        cur.execute(f"INSERT INTO {TEST_TABLE_NAME} VALUES ('{TEST_INSERT_VALUE}');")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        pytest.fail(f"Failed to insert test data: {str(e)}")
    
    # Stop and remove container
    run_cmd("docker stop astronomy-db", check=True)
    run_cmd("docker rm astronomy-db", check=True)
    
    # Recreate container
    run_cmd("docker compose up -d astronomy-db", check=True)
    
    # Wait for container to be ready
    time.sleep(10)
    
    # Verify data exists
    conn = None
    data_found = False
    try:
        conn = psycopg2.connect(
            dbname=TEST_DB_NAME,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            host="localhost",
            port=TEST_DB_PORT
        )
        cur = conn.cursor()
        cur.execute(f"SELECT value FROM {TEST_TABLE_NAME} WHERE value = '{TEST_INSERT_VALUE}';")
        result = cur.fetchone()
        if result and result[0] == TEST_INSERT_VALUE:
            data_found = True
        cur.close()
        conn.close()
    except Exception as e:
        pytest.fail(f"Failed to read data after container recreate: {str(e)}")
    
    assert data_found, "Test data not found after container recreate, persistence is not working"

def test_ac3_runs_as_postgres_non_root_user():
    """AC-3: docker exec whoami returns postgres user"""
    ret, stdout, stderr = run_cmd("docker exec astronomy-db whoami")
    assert ret == 0, f"Failed to execute whoami in container: {stderr}"
    assert stdout == "postgres", f"Container running as user '{stdout}' instead of expected 'postgres'"

def test_ac4_data_path_mounted_as_persistent_volume():
    """AC-4: /var/lib/postgresql/data is backed by a named volume"""
    ret, stdout, stderr = run_cmd("docker inspect astronomy-db | jq -r '.[].Mounts[] | select(.Destination == \"/var/lib/postgresql/data\") | .Type'")
    assert ret == 0, f"Failed to inspect container mounts: {stderr}"
    assert stdout == "volume", f"Data path mount type is '{stdout}' instead of expected 'volume'"
