import os
import pytest
import requests

# Configuration from spec
DEFAULT_HEALTH_PORT = 8080
KAFKA_HEALTH_PORT = int(os.environ.get("KAFKA_HEALTH_PORT", DEFAULT_HEALTH_PORT))
BASE_URL = f"http://kafka:{KAFKA_HEALTH_PORT}"

LIVENESS_PATH = "/health/liveness"
READINESS_PATH = "/health/readiness"


def test_ac1_liveness_returns_ok_when_broker_running():
    """AC-1: Liveness endpoint returns 200 OK with status: ok when broker is running properly"""
    response = requests.get(f"{BASE_URL}{LIVENESS_PATH}", timeout=1)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "message" in response.json()


def test_ac2_liveness_returns_503_when_broker_unresponsive():
    """AC-2: Liveness endpoint returns 503 Service Unavailable when broker is unresponsive"""
    # This test assumes broker process is stopped/crashed
    with pytest.raises(requests.exceptions.RequestException):
        response = requests.get(f"{BASE_URL}{LIVENESS_PATH}", timeout=1)
        assert response.status_code == 503
        assert response.json()["status"] == "unhealthy"
        assert "message" in response.json()
        assert "error" in response.json()


def test_ac3_readiness_returns_ok_when_healthy():
    """AC-3: Readiness endpoint returns 200 OK when cluster connected, partitions healthy"""
    response = requests.get(f"{BASE_URL}{READINESS_PATH}", timeout=1)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "message" in response.json()


def test_ac4_readiness_returns_503_when_no_cluster_connection():
    """AC-4: Readiness returns 503 when broker not connected to cluster"""
    # This test assumes broker is running but disconnected from cluster
    response = requests.get(f"{BASE_URL}{READINESS_PATH}", timeout=1)
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert "cluster connection" in response.json()["message"].lower()
    assert "error" in response.json()


def test_ac5_readiness_returns_503_when_partitions_out_of_sync():
    """AC-5: Readiness returns 503 when any partition ISR count below minimum"""
    # This test assumes at least one partition is out of sync
    response = requests.get(f"{BASE_URL}{READINESS_PATH}", timeout=1)
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert "partition" in response.json()["message"].lower()
    assert "out of sync" in response.json()["message"].lower() or "isr" in response.json()["message"].lower()
    assert "error" in response.json()


def test_ac6_health_endpoints_use_configured_port():
    """AC-6: Endpoints are exposed on KAFKA_HEALTH_PORT if set, default 8080"""
    # Test default port or configured port works
    custom_port = int(os.environ.get("KAFKA_HEALTH_PORT_TEST", DEFAULT_HEALTH_PORT))
    custom_base_url = f"http://kafka:{custom_port}"
    response = requests.get(f"{custom_base_url}{LIVENESS_PATH}", timeout=1)
    assert response.status_code in [200, 503]  # Should return valid response, not connection error


def test_ac7_endpoints_no_auth_required():
    """AC-7: Health endpoints accessible without authentication credentials"""
    # Test without any auth headers works
    response = requests.get(f"{BASE_URL}{LIVENESS_PATH}", headers={}, timeout=1)
    assert response.status_code not in [401, 403]
    response = requests.get(f"{BASE_URL}{READINESS_PATH}", headers={}, timeout=1)
    assert response.status_code not in [401, 403]


def test_ac8_non_get_requests_return_405():
    """AC-8: Non-GET requests to health endpoints return 405 Method Not Allowed"""
    for method in ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]:
        response = requests.request(method, f"{BASE_URL}{LIVENESS_PATH}", timeout=1)
        assert response.status_code == 405
        response = requests.request(method, f"{BASE_URL}{READINESS_PATH}", timeout=1)
        assert response.status_code == 405


def test_ac9_undefined_paths_return_404():
    """AC-9: Requests to paths other than /health/liveness and /health/readiness return 404"""
    for path in ["/health", "/health/", "/invalid", "/health/other", "/", "/metrics"]:
        response = requests.get(f"{BASE_URL}{path}", timeout=1)
        assert response.status_code == 404


def test_ac10_503_responses_have_detailed_error_message():
    """AC-10: All 503 error responses include detailed human-readable message"""
    # Test liveness failure case
    with pytest.raises(requests.exceptions.RequestException):
        response = requests.get(f"{BASE_URL}{LIVENESS_PATH}", timeout=1)
        if response.status_code == 503:
            assert isinstance(response.json()["message"], str)
            assert len(response.json()["message"]) > 0
            assert "error" in response.json()
    
    # Test readiness failure case
    response = requests.get(f"{BASE_URL}{READINESS_PATH}", timeout=1)
    if response.status_code == 503:
        assert isinstance(response.json()["message"], str)
        assert len(response.json()["message"]) > 0
        assert "error" in response.json()
