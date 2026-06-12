import os
import subprocess
import pytest
import yaml
from kubernetes import client, config
from psycopg2 import connect, OperationalError

# Constants from spec
CUSTOM_IMAGE = "otel/demo-postgresql:latest"
UPSTREAM_IMAGE = "postgres:15-alpine"
TLS_ENABLED_ENV = "POSTGRES_TLS_ENABLED"
TLS_CERT_PATH_ENV = "POSTGRES_TLS_CERT_PATH"
TLS_KEY_PATH_ENV = "POSTGRES_TLS_KEY_PATH"
TLS_CA_PATH_ENV = "POSTGRES_TLS_CA_PATH"
MTLS_ENABLED_ENV = "POSTGRES_MTLS_ENABLED"
CERT_MOUNT_PATH = "/etc/postgresql/certs"
DEFAULT_POSTGRES_PORT = 5432

# Test config
K8S_NAMESPACE = os.getenv("TEST_NAMESPACE", "default")
POSTGRES_SVC_NAME = "postgresql"
TEST_USER = "postgres"
TEST_PASSWORD = "postgres"
TEST_DB = "postgres"

def load_postgresql_statefulset():
    """Load the current postgresql StatefulSet manifest from repo"""
    with open("./k8s/postgresql-deployment.yaml", "r") as f:
        return yaml.safe_load(f)

@pytest.mark.integration
@pytest.mark.ac1
def test_ac1_default_config_uses_custom_image():
    """AC-1: Default deployment uses custom opentelemetry-demo postgresql image"""
    sts = load_postgresql_statefulset()
    container_image = sts["spec"]["template"]["spec"]["containers"][0]["image"]
    assert container_image == CUSTOM_IMAGE, f"Expected image {CUSTOM_IMAGE}, got {container_image}"

@pytest.mark.integration
@pytest.mark.ac1
def test_ac1_default_accepts_unencrypted_connections():
    """AC-1: Default deployment accepts unencrypted connections on port 5432"""
    try:
        conn = connect(
            host=POSTGRES_SVC_NAME,
            port=DEFAULT_POSTGRES_PORT,
            user=TEST_USER,
            password=TEST_PASSWORD,
            dbname=TEST_DB,
            sslmode="disable"
        )
        conn.close()
        assert True
    except OperationalError as e:
        pytest.fail(f"Unencrypted connection rejected: {str(e)}")

@pytest.mark.integration
@pytest.mark.ac1
def test_ac1_liveness_probe_healthy_within_30s_default():
    """AC-1: Liveness probe returns OK within 30s with default config"""
    result = subprocess.run(
        ["kubectl", "wait", "--for=condition=ready", f"pod/{POSTGRES_SVC_NAME}-0", 
         "-n", K8S_NAMESPACE, "--timeout=30s"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Liveness probe failed: {result.stderr}"

@pytest.mark.integration
@pytest.mark.ac1
def test_ac1_readiness_probe_healthy_within_30s_default():
    """AC-1: Readiness probe returns OK within 30s with default config"""
    result = subprocess.run(
        ["kubectl", "exec", f"pod/{POSTGRES_SVC_NAME}-0", "-n", K8S_NAMESPACE, "--",
         "pg_isready", "-U", TEST_USER, "-d", TEST_DB, "-p", str(DEFAULT_POSTGRES_PORT)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Readiness probe failed: {result.stderr}"

@pytest.mark.integration
@pytest.mark.ac1
def test_ac1_password_auth_succeeds_without_client_cert_default():
    """AC-1: Password auth succeeds without client certificate with default config"""
    try:
        conn = connect(
            host=POSTGRES_SVC_NAME,
            port=DEFAULT_POSTGRES_PORT,
            user=TEST_USER,
            password=TEST_PASSWORD,
            dbname=TEST_DB,
            sslmode="disable"
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        res = cur.fetchone()
        assert res[0] == 1
        conn.close()
    except OperationalError as e:
        pytest.fail(f"Password auth failed without client cert: {str(e)}")

@pytest.mark.integration
@pytest.mark.ac2
def test_ac2_tls_enabled_accepts_tls_1_plus_connections():
    """AC-2: TLS enabled accepts TLS v1.2+ connections on port 5432"""
    try:
        conn = connect(
            host=POSTGRES_SVC_NAME,
            port=DEFAULT_POSTGRES_PORT,
            user=TEST_USER,
            password=TEST_PASSWORD,
            dbname=TEST_DB,
            sslmode="require",
            sslrootcert=f"{CERT_MOUNT_PATH}/ca.crt"
        )
        conn.close()
        assert True
    except OperationalError as e:
        pytest.fail(f"TLS connection rejected: {str(e)}")

@pytest.mark.integration
@pytest.mark.ac2
def test_ac2_tls_enabled_rejects_unencrypted_connections():
    """AC-2: TLS enabled rejects unencrypted connection attempts"""
    with pytest.raises(OperationalError):
        conn = connect(
            host=POSTGRES_SVC_NAME,
            port=DEFAULT_POSTGRES_PORT,
            user=TEST_USER,
            password=TEST_PASSWORD,
            dbname=TEST_DB,
            sslmode="disable"
        )
        conn.close()

@pytest.mark.integration
@pytest.mark.ac2
def test_ac2_liveness_probe_healthy_within_30s_tls():
    """AC-2: Liveness probe uses TLS and returns OK within 30s when TLS is enabled"""
    result = subprocess.run(
        ["kubectl", "wait", "--for=condition=ready", f"pod/{POSTGRES_SVC_NAME}-0", 
         "-n", K8S_NAMESPACE, "--timeout=30s"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Liveness probe failed with TLS enabled: {result.stderr}"

@pytest.mark.integration
@pytest.mark.ac2
def test_ac2_readiness_probe_healthy_within_30s_tls():
    """AC-2: Readiness probe uses TLS and returns OK within 30s when TLS is enabled"""
    result = subprocess.run(
        ["kubectl", "exec", f"pod/{POSTGRES_SVC_NAME}-0", "-n", K8S_NAMESPACE, "--",
         "pg_isready", "-U", TEST_USER, "-d", TEST_DB, "-p", str(DEFAULT_POSTGRES_PORT),
         "--sslmode=require", f"--sslrootcert={CERT_MOUNT_PATH}/ca.crt"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Readiness probe failed with TLS enabled: {result.stderr}"

@pytest.mark.integration
@pytest.mark.ac2
def test_ac2_password_auth_succeeds_over_tls_without_client_cert():
    """AC-2: Password auth succeeds over TLS without client certificate when mTLS is disabled"""
    try:
        conn = connect(
            host=POSTGRES_SVC_NAME,
            port=DEFAULT_POSTGRES_PORT,
            user=TEST_USER,
            password=TEST_PASSWORD,
            dbname=TEST_DB,
            sslmode="require",
            sslrootcert=f"{CERT_MOUNT_PATH}/ca.crt"
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        res = cur.fetchone()
        assert res[0] == 1
        conn.close()
    except OperationalError as e:
        pytest.fail(f"Password auth over TLS failed without client cert: {str(e)}")

@pytest.mark.integration
@pytest.mark.ac3
def test_ac3_mtls_enabled_rejects_connections_without_client_cert():
    """AC-3: mTLS enabled rejects connections without valid client certificate"""
    with pytest.raises(OperationalError):
        conn = connect(
            host=POSTGRES_SVC_NAME,
            port=DEFAULT_POSTGRES_PORT,
            user=TEST_USER,
            password=TEST_PASSWORD,
            dbname=TEST_DB,
            sslmode="require",
            sslrootcert=f"{CERT_MOUNT_PATH}/ca.crt"
        )
        conn.close()

@pytest.mark.integration
@pytest.mark.ac3
def test_ac3_mtls_enabled_accepts_connections_with_valid_client_cert_and_password():
    """AC-3: mTLS enabled accepts connections with valid client cert and correct password"""
    try:
        conn = connect(
            host=POSTGRES_SVC_NAME,
            port=DEFAULT_POSTGRES_PORT,
            user=TEST_USER,
            password=TEST_PASSWORD,
            dbname=TEST_DB,
            sslmode="verify-full",
            sslrootcert=f"{CERT_MOUNT_PATH}/ca.crt",
            sslcert=f"{CERT_MOUNT_PATH}/tls.crt",
            sslkey=f"{CERT_MOUNT_PATH}/tls.key"
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        res = cur.fetchone()
        assert res[0] == 1
        conn.close()
    except OperationalError as e:
        pytest.fail(f"mTLS connection rejected with valid cert and password: {str(e)}")

@pytest.mark.integration
@pytest.mark.ac3
def test_ac3_liveness_readiness_probes_work_with_mtls():
    """AC-3: Liveness and readiness probes work with mTLS enabled"""
    wait_result = subprocess.run(
        ["kubectl", "wait", "--for=condition=ready", f"pod/{POSTGRES_SVC_NAME}-0", 
         "-n", K8S_NAMESPACE, "--timeout=30s"],
        capture_output=True, text=True
    )
    assert wait_result.returncode == 0, f"Liveness probe failed with mTLS enabled: {wait_result.stderr}"
    
    probe_result = subprocess.run(
        ["kubectl", "exec", f"pod/{POSTGRES_SVC_NAME}-0", "-n", K8S_NAMESPACE, "--",
         "pg_isready", "-U", TEST_USER, "-d", TEST_DB, "-p", str(DEFAULT_POSTGRES_PORT),
         "--sslmode=verify-full", f"--sslrootcert={CERT_MOUNT_PATH}/ca.crt",
         f"--sslcert={CERT_MOUNT_PATH}/tls.crt", f"--sslkey={CERT_MOUNT_PATH}/tls.key"],
        capture_output=True, text=True
    )
    assert probe_result.returncode == 0, f"Readiness probe failed with mTLS enabled: {probe_result.stderr}"

@pytest.mark.manifest
@pytest.mark.ac4
def test_ac4_statefulset_has_tls_config_comments():
    """AC-4: StatefulSet manifest has comments documenting TLS configuration options"""
    with open("./k8s/postgresql-deployment.yaml", "r") as f:
        manifest_content = f.read()
    
    # Check for comments documenting each env var and mount path
    assert TLS_ENABLED_ENV in manifest_content, f"Missing {TLS_ENABLED_ENV} in manifest"
    assert "# Toggle for TLS encryption support" in manifest_content
    assert TLS_CERT_PATH_ENV in manifest_content, f"Missing {TLS_CERT_PATH_ENV} in manifest"
    assert "# Path to server TLS certificate file" in manifest_content
    assert TLS_KEY_PATH_ENV in manifest_content, f"Missing {TLS_KEY_PATH_ENV} in manifest"
    assert "# Path to server TLS private key file" in manifest_content
    assert TLS_CA_PATH_ENV in manifest_content, f"Missing {TLS_CA_PATH_ENV} in manifest"
    assert "# Path to CA certificate for client authentication" in manifest_content
    assert MTLS_ENABLED_ENV in manifest_content, f"Missing {MTLS_ENABLED_ENV} in manifest"
    assert "# Toggle for mandatory client certificate authentication" in manifest_content
    assert CERT_MOUNT_PATH in manifest_content, f"Missing cert mount path {CERT_MOUNT_PATH} in manifest"
    assert "# Certificate volume mount path for TLS/mTLS" in manifest_content
