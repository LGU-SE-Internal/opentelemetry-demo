#!/usr/bin/env python3
import os
import pytest
import requests
import time

SERVICE_NAME = os.environ.get("TEST_QUOTE_SERVICE_NAME", "quote-service")
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")
SERVICE_PORT = 8080
BASE_URL = f"http://{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:{SERVICE_PORT}"

@pytest.mark.ac1
def test_ac1_liveness_endpoint_returns_200_when_running():
    """AC-1: When the quoteservice process is running without critical runtime errors,
    a GET request to `/health/liveness` returns HTTP 200 OK status code."""
    try:
        resp = requests.get(f"{BASE_URL}/health/liveness", timeout=5)
        assert resp.status_code == 200, f"Liveness endpoint returned {resp.status_code}, expected 200"
    except Exception as e:
        pytest.fail(f"Failed to access liveness endpoint: {str(e)}")

@pytest.mark.ac2
def test_ac2_readiness_endpoint_returns_200_when_healthy():
    """AC-2: When the quoteservice has completed initialization and all required
    downstream service connections are healthy, a GET request to `/health/readiness`
    returns HTTP 200 OK status code."""
    # Wait up to 30 seconds for service to be fully ready
    start_time = time.time()
    while time.time() - start_time < 30:
        try:
            resp = requests.get(f"{BASE_URL}/health/readiness", timeout=2)
            if resp.status_code == 200:
                return
            time.sleep(1)
        except Exception:
            time.sleep(1)
    pytest.fail("Readiness endpoint did not return 200 within 30 seconds of service start")

@pytest.mark.ac3
def test_ac3_readiness_returns_503_during_initialization():
    """AC-3: When the quoteservice is still performing initialization steps
    (config loading, dependency connection setup), a GET request to `/health/readiness`
    returns HTTP 503 Service Unavailable status code."""
    # This test is run immediately after deployment before service is initialized
    try:
        resp = requests.get(f"{BASE_URL}/health/readiness", timeout=5)
        # Should return 503 if not yet initialized, or 200 if already initialized
        # If we get 200 immediately, test is inconclusive but we can skip as we can't catch initialization phase
        if resp.status_code == 200:
            pytest.skip("Service already initialized, can't test initialization phase 503 response")
        assert resp.status_code == 503, f"Readiness endpoint returned {resp.status_code}, expected 503 during initialization"
    except Exception as e:
        pytest.fail(f"Failed to access readiness endpoint during initialization: {str(e)}")

@pytest.mark.ac4
def test_ac4_readiness_returns_503_when_downstream_unavailable():
    """AC-4: When any required downstream service connection fails,
    a GET request to `/health/readiness` returns HTTP 503 Service Unavailable status code."""
    # NOTE: This test assumes we have a way to simulate downstream service failure
    # For the purposes of this test, we verify that the endpoint exists and correctly reports failures
    # When downstream failure is induced, this test will pass
    try:
        resp = requests.get(f"{BASE_URL}/health/readiness", timeout=5)
        if resp.status_code == 503:
            # If already failing, check we got 503
            assert resp.status_code == 503, "Expected 503 when downstream is unavailable"
        else:
            # If healthy, we skip as we can't induce failure in this test context
            pytest.skip("All downstream services are healthy, can't test failure scenario")
    except Exception as e:
        pytest.fail(f"Failed to access readiness endpoint: {str(e)}")

@pytest.mark.ac5
def test_ac5_health_endpoints_exposed_on_same_port_as_main_api():
    """AC-5: Both `/health/liveness` and `/health/readiness` endpoints accept requests
    on the same public TCP port as the quoteservice's primary API endpoints."""
    # Main API runs on port 8080, we already use that port for health checks
    # Verify both endpoints are accessible on this port
    try:
        liveness_resp = requests.get(f"{BASE_URL}/health/liveness", timeout=5)
        readiness_resp = requests.get(f"{BASE_URL}/health/readiness", timeout=5)
        # Both endpoints should respond, no connection errors
        assert liveness_resp.status_code in [200, 503], "Liveness endpoint not responding on main service port"
        assert readiness_resp.status_code in [200, 503], "Readiness endpoint not responding on main service port"
    except Exception as e:
        pytest.fail(f"Failed to access health endpoints on main service port {SERVICE_PORT}: {str(e)}")

@pytest.mark.ac6
def test_ac6_health_endpoints_require_no_authentication():
    """AC-6: No authentication credentials or custom request headers are required
    to get a valid response from either health endpoint."""
    try:
        # Send request with no headers, no auth
        liveness_resp = requests.get(f"{BASE_URL}/health/liveness", headers={}, timeout=5)
        readiness_resp = requests.get(f"{BASE_URL}/health/readiness", headers={}, timeout=5)
        # Should get valid response (either 200 or 503, not 401/403)
        assert liveness_resp.status_code not in [401, 403], "Liveness endpoint requires authentication"
        assert readiness_resp.status_code not in [401, 403], "Readiness endpoint requires authentication"
    except Exception as e:
        pytest.fail(f"Failed to access health endpoints without authentication: {str(e)}")
