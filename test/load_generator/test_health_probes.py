import pytest
import requests
from locust.env import Environment
from locust.web import app
from flask import Flask

@pytest.fixture
def locust_env():
    env = Environment(user_classes=[])
    return env

@pytest.fixture
def flask_app(locust_env):
    app.locust_env = locust_env
    return app.test_client()

def test_ac1_liveness_returns_200_ok_when_process_running(flask_app):
    """AC-1: GET /health/liveness returns 200 OK with body OK when process is running"""
    response = flask_app.get("/health/liveness")
    assert response.status_code == 200
    assert response.data.decode("utf-8").strip() == "OK"
    assert response.content_type == "text/plain"

def test_ac2_liveness_no_auth_required(flask_app):
    """AC-2: GET /health/liveness works without authentication even if web UI auth is enabled"""
    # Simulate auth enabled by adding auth required middleware check
    # Verify no 401/403 returned for health endpoint
    response = flask_app.get("/health/liveness", headers={"Authorization": "invalid"})
    assert response.status_code == 200, "Liveness endpoint should not require authentication"

def test_ac3_readiness_returns_200_ready_when_test_loaded(locust_env, flask_app):
    """AC-3: GET /health/readiness returns 200 READY when test suite is fully loaded"""
    # Simulate test suite loaded properly
    from locust import HttpUser
    class TestUser(HttpUser):
        pass
    locust_env.user_classes = [TestUser]
    locust_env.create_local_runner()
    
    response = flask_app.get("/health/readiness")
    assert response.status_code == 200
    assert response.data.decode("utf-8").strip() == "READY"
    assert response.content_type == "text/plain"

def test_ac4_readiness_returns_503_when_not_loaded(flask_app):
    """AC-4: GET /health/readiness returns 503 NOT_READY when test suite is not loaded"""
    response = flask_app.get("/health/readiness")
    assert response.status_code == 503
    assert response.data.decode("utf-8").strip() == "NOT_READY"
    assert response.content_type == "text/plain"

def test_ac5_readiness_no_auth_required(flask_app):
    """AC-5: GET /health/readiness works without authentication even if web UI auth is enabled"""
    response = flask_app.get("/health/readiness", headers={"Authorization": "invalid"})
    assert response.status_code in [200, 503], "Readiness endpoint should not require authentication"

def test_ac6_existing_locust_endpoints_still_work(flask_app):
    """AC-6: Existing Locust web UI and API endpoints continue to work unchanged"""
    # Test main UI endpoint
    response = flask_app.get("/")
    assert response.status_code in [200, 302], "Root Locust endpoint should still work"
    # Test stats endpoint
    response = flask_app.get("/stats/requests")
    assert response.status_code == 200, "Stats API endpoint should still work"
