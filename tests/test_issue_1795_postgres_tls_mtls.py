#!/usr/bin/env python3
import pytest
import subprocess
import time
import os
import tempfile
import psycopg2
from pathlib import Path

POSTGRES_IMAGE_NAME = "otel/postgresql:dev"
TEST_CERT_DIR = os.path.join(os.path.dirname(__file__), "test_certs")
# Dummy test cert/key/ca values (invalid for actual use, valid for file existence checks)
DUMMY_CERT = "-----BEGIN CERTIFICATE-----\nMIIC5zCCAc+gAwIBAgIUZ7X7m5z3x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END CERTIFICATE-----"
DUMMY_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDl1M0m8z7x8z7y9x9w8v7u6t5s4r3q2p1o0n9m8l7k6j5i4h3g2f1e0d9c8b7a6\n-----END PRIVATE KEY-----"
DUMMY_CA = "-----BEGIN CERTIFICATE-----\nMIIC4DCCAcigAwIBAgIUa6s5d4f3e2d1c0b9a8z7y6x5w4v3u2t1s0r9q8p7o6n5m4l3k2j1i0h9g8f7e6\n-----END CERTIFICATE-----"
INVALID_CERT = "-----BEGIN CERTIFICATE-----\nINVALIDINVALIDINVALID\n-----END CERTIFICATE-----"

def create_test_certs():
    """Create temporary test certificate files"""
    Path(TEST_CERT_DIR).mkdir(exist_ok=True, mode=0o755)
    for fname, content in [
        ("server.crt", DUMMY_CERT),
        ("server.key", DUMMY_KEY),
        ("ca.crt", DUMMY_CA),
        ("invalid.crt", INVALID_CERT),
        ("invalid.key", INVALID_CERT)
    ]:
        with open(os.path.join(TEST_CERT_DIR, fname), "w") as f:
            f.write(content)
    os.chmod(os.path.join(TEST_CERT_DIR, "server.key"), 0o600)

def run_postgres_container(env_vars, volumes=None, wait_for_exit=True, timeout=30):
    """Helper to run postgres container with given env vars and volumes"""
    cmd = ["docker", "run", "--rm"]
    if volumes:
        for vol in volumes:
            cmd.extend(["-v", vol])
    for k, v in env_vars.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.append(POSTGRES_IMAGE_NAME)
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not wait_for_exit:
        time.sleep(10)
        return proc, None, None
    
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise Exception("Container timed out")

@pytest.fixture(scope="module", autouse=True)
def setup_test_certs():
    create_test_certs()
    yield
    import shutil
    shutil.rmtree(TEST_CERT_DIR, ignore_errors=True)

@pytest.mark.ac1
def test_ac1_tls_disabled_accepts_unencrypted_connections():
    """AC-1: When POSTGRES_TLS_ENABLED=false, PostgreSQL starts normally and accepts unencrypted connections"""
    env = {
        "POSTGRES_TLS_ENABLED": "false",
        "POSTGRES_PASSWORD": "testpass"
    }
    proc, _, _ = run_postgres_container(env, wait_for_exit=False)
    try:
        # Try unencrypted connection
        conn = psycopg2.connect(
            dbname="postgres", user="postgres", password="testpass",
            host="localhost", port=5432, sslmode="disable"
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        conn.close()
    finally:
        proc.terminate()
        proc.wait()

@pytest.mark.ac2
def test_ac2_tls_enabled_only_accepts_encrypted_connections():
    """AC-2: When TLS enabled with valid certs, Postgres only accepts encrypted TLS connections"""
    volumes = [
        f"{TEST_CERT_DIR}:/etc/postgresql/tls/certs:ro",
        f"{TEST_CERT_DIR}:/etc/postgresql/tls/private:ro"
    ]
    env = {
        "POSTGRES_TLS_ENABLED": "true",
        "POSTGRES_TLS_CERT_FILE": "/etc/postgresql/tls/certs/server.crt",
        "POSTGRES_TLS_KEY_FILE": "/etc/postgresql/tls/private/server.key",
        "POSTGRES_PASSWORD": "testpass"
    }
    proc, _, _ = run_postgres_container(env, volumes=volumes, wait_for_exit=False)
    try:
        # Test unencrypted connection fails
        with pytest.raises(psycopg2.OperationalError):
            psycopg2.connect(
                dbname="postgres", user="postgres", password="testpass",
                host="localhost", port=5432, sslmode="disable"
            )
        # Test encrypted connection succeeds
        conn = psycopg2.connect(
            dbname="postgres", user="postgres", password="testpass",
            host="localhost", port=5432, sslmode="require"
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        conn.close()
    finally:
        proc.terminate()
        proc.wait()

@pytest.mark.ac3
def test_ac3_tls_enabled_missing_cert_exits_with_config_error():
    """AC-3: When TLS enabled but missing cert/key file, exit code 1 with CONFIG_ERROR"""
    env = {
        "POSTGRES_TLS_ENABLED": "true",
        "POSTGRES_TLS_KEY_FILE": "/nonexistent/key.pem",
        "POSTGRES_PASSWORD": "testpass"
    }
    proc, stdout, stderr = run_postgres_container(env)
    assert proc.returncode == 1, f"Expected exit code 1, got {proc.returncode}"
    assert "CONFIG_ERROR" in stdout + stderr, "Expected CONFIG_ERROR in logs"
    assert "missing" in (stdout + stderr).lower(), "Expected missing file message"

@pytest.mark.ac4
def test_ac4_tls_enabled_invalid_cert_exits_with_config_error():
    """AC-4: When TLS enabled with invalid/corrupted cert/key, exit code 1 with CONFIG_ERROR"""
    volumes = [f"{TEST_CERT_DIR}:/testcerts:ro"]
    env = {
        "POSTGRES_TLS_ENABLED": "true",
        "POSTGRES_TLS_CERT_FILE": "/testcerts/invalid.crt",
        "POSTGRES_TLS_KEY_FILE": "/testcerts/invalid.key",
        "POSTGRES_PASSWORD": "testpass"
    }
    proc, stdout, stderr = run_postgres_container(env, volumes=volumes)
    assert proc.returncode == 1, f"Expected exit code 1, got {proc.returncode}"
    assert "CONFIG_ERROR" in stdout + stderr, "Expected CONFIG_ERROR in logs"
    assert "invalid" in (stdout + stderr).lower(), "Expected invalid certificate message"

@pytest.mark.ac5
def test_ac5_mtls_enabled_rejects_connections_without_client_cert():
    """AC-5: When mTLS enabled, rejects connections without valid client cert signed by CA"""
    volumes = [
        f"{TEST_CERT_DIR}:/etc/postgresql/tls/certs:ro",
        f"{TEST_CERT_DIR}:/etc/postgresql/tls/private:ro"
    ]
    env = {
        "POSTGRES_TLS_ENABLED": "true",
        "POSTGRES_TLS_CERT_FILE": "/etc/postgresql/tls/certs/server.crt",
        "POSTGRES_TLS_KEY_FILE": "/etc/postgresql/tls/private/server.key",
        "POSTGRES_TLS_MTLS_ENABLED": "true",
        "POSTGRES_TLS_CA_FILE": "/etc/postgresql/tls/certs/ca.crt",
        "POSTGRES_PASSWORD": "testpass"
    }
    proc, _, _ = run_postgres_container(env, volumes=volumes, wait_for_exit=False)
    try:
        # Connection without client cert should fail
        with pytest.raises(psycopg2.OperationalError):
            psycopg2.connect(
                dbname="postgres", user="postgres", password="testpass",
                host="localhost", port=5432, sslmode="require"
            )
    finally:
        proc.terminate()
        proc.wait()

@pytest.mark.ac6
def test_ac6_mtls_enabled_without_tls_exits_config_error():
    """AC-6: When mTLS enabled but TLS disabled, exit code 1 with CONFIG_ERROR"""
    env = {
        "POSTGRES_TLS_ENABLED": "false",
        "POSTGRES_TLS_MTLS_ENABLED": "true",
        "POSTGRES_PASSWORD": "testpass"
    }
    proc, stdout, stderr = run_postgres_container(env)
    assert proc.returncode == 1, f"Expected exit code 1, got {proc.returncode}"
    assert "CONFIG_ERROR" in stdout + stderr, "Expected CONFIG_ERROR in logs"
    assert "mtls cannot be enabled without tls" in (stdout + stderr).lower(), "Expected mTLS without TLS error message"

@pytest.mark.ac7
def test_ac7_mtls_enabled_missing_ca_exits_config_error():
    """AC-7: When mTLS enabled but missing/invalid CA file, exit code 1 with CONFIG_ERROR"""
    env = {
        "POSTGRES_TLS_ENABLED": "true",
        "POSTGRES_TLS_CERT_FILE": "/certs/server.crt",
        "POSTGRES_TLS_KEY_FILE": "/certs/server.key",
        "POSTGRES_TLS_MTLS_ENABLED": "true",
        "POSTGRES_PASSWORD": "testpass"
    }
    proc, stdout, stderr = run_postgres_container(env)
    assert proc.returncode == 1, f"Expected exit code 1, got {proc.returncode}"
    assert "CONFIG_ERROR" in stdout + stderr, "Expected CONFIG_ERROR in logs"
    assert "ca" in (stdout + stderr).lower() and ("missing" in (stdout + stderr).lower() or "invalid" in (stdout + stderr).lower()), "Expected CA missing/invalid message"

@pytest.mark.ac8
def test_ac8_dockerfile_has_correct_volume_mount_points():
    """AC-8: Dockerfile exposes correct TLS volume mount points with correct permissions"""
    # Check docker inspect for volumes
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Volumes}}", POSTGRES_IMAGE_NAME],
        capture_output=True, text=True, check=True
    )
    volumes_output = proc.stdout
    assert "/etc/postgresql/tls/certs" in volumes_output, "Missing /etc/postgresql/tls/certs volume"
    assert "/etc/postgresql/tls/private" in volumes_output, "Missing /etc/postgresql/tls/private volume"
    
    # Check permissions in container
    proc = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "stat", POSTGRES_IMAGE_NAME, "-c", "%a %U %G", "/etc/postgresql/tls/private"],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, "Private TLS directory does not exist"
    perm, user, group = proc.stdout.strip().split()
    assert perm == "700", f"Expected private dir permissions 700, got {perm}"
    assert user == "postgres", f"Expected private dir owner postgres, got {user}"
    assert group == "postgres", f"Expected private dir group postgres, got {group}"
    
    proc = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "stat", POSTGRES_IMAGE_NAME, "-c", "%a %U %G", "/etc/postgresql/tls/certs"],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, "Certs TLS directory does not exist"
    perm, user, group = proc.stdout.strip().split()
    assert perm == "755", f"Expected certs dir permissions 755, got {perm}"
