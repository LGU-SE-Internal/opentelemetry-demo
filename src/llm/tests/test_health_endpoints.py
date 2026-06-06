#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
import pytest
from src.llm.app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_ac1_health_endpoint_returns_200_up(client):
    """AC-1: GET /health returns 200 OK with {"status": "UP"} when app is running"""
    response = client.get('/health')
    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {"status": "UP"}

def test_ac2_ready_endpoint_returns_503_when_summaries_not_loaded(client):
    """AC-2: GET /ready returns 503 NOT_READY when product reviews are not loaded"""
    # Reset the global product_review_summaries to None to simulate unloaded state
    from src.llm import app as app_module
    original_summaries = app_module.product_review_summaries
    app_module.product_review_summaries = None
    
    try:
        response = client.get('/ready')
        assert response.status_code == 503
        assert response.is_json
        assert response.get_json() == {"status": "NOT_READY"}
    finally:
        app_module.product_review_summaries = original_summaries

def test_ac3_ready_endpoint_returns_200_when_summaries_loaded(client):
    """AC-3: GET /ready returns 200 READY when product reviews are successfully loaded"""
    from src.llm import app as app_module
    original_summaries = app_module.product_review_summaries
    # Simulate successfully loaded summaries
    app_module.product_review_summaries = {"test_product_id": "test summary"}
    
    try:
        response = client.get('/ready')
        assert response.status_code == 200
        assert response.is_json
        assert response.get_json() == {"status": "READY"}
    finally:
        app_module.product_review_summaries = original_summaries

def test_ac4_endpoints_match_standard_response_format(client):
    """AC-4: Response format matches standard health endpoint conventions"""
    # Test health endpoint format
    health_resp = client.get('/health')
    health_json = health_resp.get_json()
    assert "status" in health_json
    assert isinstance(health_json["status"], str)
    
    # Test ready endpoint format (when not ready)
    from src.llm import app as app_module
    original_summaries = app_module.product_review_summaries
    app_module.product_review_summaries = None
    try:
        ready_resp = client.get('/ready')
        ready_json = ready_resp.get_json()
        assert "status" in ready_json
        assert isinstance(ready_json["status"], str)
    finally:
        app_module.product_review_summaries = original_summaries

def test_ac5_existing_endpoints_still_work(client):
    """AC-5: Adding health endpoints does not break existing functionality"""
    # Test existing /v1/models endpoint still works
    models_resp = client.get('/v1/models')
    assert models_resp.status_code == 200
    assert models_resp.is_json
    assert "data" in models_resp.get_json()
