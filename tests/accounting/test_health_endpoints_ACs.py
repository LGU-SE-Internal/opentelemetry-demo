import os
import pytest
import requests
import subprocess
import time
from pathlib import Path

# Test constants from spec
HTTP_PORT = 8080
HTTPS_PORT = 8443
LIVENESS_ENDPOINT = "/health/liveness"
READINESS_ENDPOINT = "/health/readiness"
ENV_TLS_CERT = "ACCOUNTING_SERVICE_TLS_CERT_PATH"
ENV_TLS_KEY = "ACCOUNTING_SERVICE_TLS_KEY_PATH"
ENV_KAFKA_BROKER = "KAFKA_BROKER"
ENV_POSTGRES_CONN = "POSTGRES_CONNECTION_STRING"

# Helper to start accounting service with given env vars
def start_service(env_vars):
    base_env = os.environ.copy()
    base_env.update(env_vars)
    proc = subprocess.Popen(
        ["dotnet", "run", "--project", Path(__file__).parent.parent.parent / "src/accounting/AccountingService.csproj"],
        env=base_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for service to start or fail
    time.sleep(5)
    return proc

def test_ac1_liveness_returns_200_when_service_running():
    """AC-1: When service process is running normally, GET /health/liveness returns 200 OK"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: "",
        # Use valid test dependencies
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{LIVENESS_ENDPOINT}", timeout=2)
        assert resp.status_code == 200, "Liveness endpoint should return 200 when service is running"
        assert resp.headers["Content-Type"] == "application/json", "Response should be JSON"
        assert resp.json() == {"status": "Healthy"}, "Liveness response body is incorrect"
    finally:
        proc.terminate()
        proc.wait()

def test_ac2_liveness_fails_when_service_unresponsive():
    """AC-2: When service process is unresponsive/crashed, requests to liveness fail"""
    # Do not start service, simulate crash
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://localhost:{HTTP_PORT}{LIVENESS_ENDPOINT}", timeout=2)

def test_ac3_readiness_returns_200_when_all_dependencies_healthy():
    """AC-3: When Kafka and Postgres are healthy, readiness returns 200 with all checks Healthy"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: "",
        # Valid dependencies
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{READINESS_ENDPOINT}", timeout=2)
        assert resp.status_code == 200, "Readiness should return 200 when all dependencies are healthy"
        assert resp.headers["Content-Type"] == "application/json"
        body = resp.json()
        assert body["status"] == "Healthy"
        assert body["checks"]["kafka_consumer"] == "Healthy"
        assert body["checks"]["postgresql"] == "Healthy"
    finally:
        proc.terminate()
        proc.wait()

def test_ac4_readiness_returns_503_when_kafka_down():
    """AC-4: When Kafka consumer connection is down, readiness returns 503 with kafka check Unhealthy"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: "",
        # Invalid Kafka broker
        ENV_KAFKA_BROKER: "invalid.kafka.host:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{READINESS_ENDPOINT}", timeout=2)
        assert resp.status_code == 503, "Readiness should return 503 when Kafka is down"
        assert resp.headers["Content-Type"] == "application/json"
        body = resp.json()
        assert body["status"] == "Unhealthy"
        assert body["checks"]["kafka_consumer"] == "Unhealthy"
    finally:
        proc.terminate()
        proc.wait()

def test_ac5_readiness_returns_503_when_postgres_down():
    """AC-5: When PostgreSQL connection is down, readiness returns 503 with postgres check Unhealthy"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        # Invalid Postgres connection
        ENV_POSTGRES_CONN: "Host=invalid.postgres.host;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{READINESS_ENDPOINT}", timeout=2)
        assert resp.status_code == 503, "Readiness should return 503 when Postgres is down"
        assert resp.headers["Content-Type"] == "application/json"
        body = resp.json()
        assert body["status"] == "Unhealthy"
        assert body["checks"]["postgresql"] == "Unhealthy"
    finally:
        proc.terminate()
        proc.wait()

def test_ac6_health_endpoints_work_over_http_and_https():
    """AC-6: Health endpoints are accessible on same port as existing server, both with TLS enabled and disabled"""
    # First test HTTP (TLS disabled)
    env_http = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc_http = start_service(env_http)
    try:
        # Check liveness on HTTP
        resp_live_http = requests.get(f"http://localhost:{HTTP_PORT}{LIVENESS_ENDPOINT}", timeout=2)
        assert resp_live_http.status_code == 200
        # Check readiness on HTTP
        resp_ready_http = requests.get(f"http://localhost:{HTTP_PORT}{READINESS_ENDPOINT}", timeout=2)
        assert resp_ready_http.status_code in [200, 503]  # Status depends on dependencies, but endpoint should exist
    finally:
        proc_http.terminate()
        proc_http.wait()
    
    # Test HTTPS (TLS enabled)
    test_cert = Path(__file__).parent / "test_data" / "server.crt"
    test_key = Path(__file__).parent / "test_data" / "server.key"
    env_https = {
        ENV_TLS_CERT: str(test_cert),
        ENV_TLS_KEY: str(test_key),
        ENV_MTLS_CA: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc_https = start_service(env_https)
    try:
        # Check liveness on HTTPS
        resp_live_https = requests.get(f"https://localhost:{HTTPS_PORT}{LIVENESS_ENDPOINT}", verify=str(test_cert), timeout=2)
        assert resp_live_https.status_code == 200
        # Check readiness on HTTPS
        resp_ready_https = requests.get(f"https://localhost:{HTTPS_PORT}{READINESS_ENDPOINT}", verify=str(test_cert), timeout=2)
        assert resp_ready_https.status_code in [200, 503]  # Endpoint should exist
    finally:
        proc_https.terminate()
        proc_https.wait()

def test_ac7_no_additional_ports_opened_for_health_checks():
    """AC-7: No additional ports are opened for health check endpoints"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_MTLS_CA: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        # Verify endpoints only work on 8080 (HTTP) and 8443 (HTTPS), not any other port
        for port in [8081, 8082, 9000, 9090]:
            with pytest.raises(requests.exceptions.ConnectionError):
                requests.get(f"http://localhost:{port}{LIVENESS_ENDPOINT}", timeout=0.5)
            with pytest.raises(requests.exceptions.ConnectionError):
                requests.get(f"http://localhost:{port}{READINESS_ENDPOINT}", timeout=0.5)
    finally:
        proc.terminate()
        proc.wait()
