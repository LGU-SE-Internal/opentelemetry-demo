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

def test_ac1_healthz_returns_200_always_when_service_running(client):
    """AC-1: When service is running, GET /healthz returns 200 OK empty body regardless of backend state"""
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.data == b''
    assert not response.is_json

def test_ac2_readyz_returns_200_when_backend_connected(client, monkeypatch):
    """AC-2: When LLM backend is connected and responsive, GET /readyz returns 200 OK empty body"""
    # Simulate connected backend state
    import src.llm.app as app_module
    monkeypatch.setattr(app_module, 'llm_backend_connected', lambda: True)
    
    response = client.get('/readyz')
    assert response.status_code == 200
    assert response.data == b''
    assert not response.is_json

def test_ac3_readyz_returns_503_when_backend_disconnected(client, monkeypatch):
    """AC-3: When LLM backend is not connected, GET /readyz returns 503 with exact plaintext error"""
    # Simulate disconnected backend state
    import src.llm.app as app_module
    monkeypatch.setattr(app_module, 'llm_backend_connected', lambda: False)
    
    response = client.get('/readyz')
    assert response.status_code == 503
    assert response.data.decode('utf-8') == 'LLM backend connection not established'
    assert response.content_type == 'text/plain; charset=utf-8'

def test_ac4_non_get_methods_return_405_for_both_endpoints(client):
    """AC-4: Any non-GET request to /healthz or /readyz returns 405 Method Not Allowed"""
    non_get_methods = ['POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD']
    endpoints = ['/healthz', '/readyz']
    
    for endpoint in endpoints:
        for method in non_get_methods:
            response = client.open(endpoint, method=method)
            assert response.status_code == 405, f"{method} {endpoint} should return 405"

def test_ac5_existing_grpc_chat_completions_endpoint_still_works(client):
    """AC-5: Existing gRPC service functionality remains unchanged"""
    # Test existing HTTP/gRPC endpoint still responds as expected
    # First test existing models endpoint
    models_resp = client.get('/v1/models')
    assert models_resp.status_code == 200
    assert models_resp.is_json
    assert "data" in models_resp.get_json()
    
    # Test chat completions endpoint returns expected error for bad request
    chat_resp = client.post('/v1/chat/completions', json={})
    # We expect a 400 or 422 for empty request, not 404/500
    assert chat_resp.status_code in (400, 422)

