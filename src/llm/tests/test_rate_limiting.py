#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
import time
import pytest
from src.llm.app import app
from freezegun import freeze_time


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_ac1_requests_within_limit_return_ok_with_headers(client, monkeypatch):
    """AC-1: Requests within rate limit return expected status with all rate limit headers"""
    monkeypatch.setenv('RATE_LIMIT_MAX_REQUESTS', '2')
    monkeypatch.setenv('RATE_LIMIT_WINDOW_SECONDS', '60')
    
    # First request
    response = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.1'})
    assert response.status_code != 429
    assert 'X-RateLimit-Limit' in response.headers
    assert response.headers['X-RateLimit-Limit'] == '2'
    assert 'X-RateLimit-Remaining' in response.headers
    assert int(response.headers['X-RateLimit-Remaining']) == 1
    assert 'X-RateLimit-Reset' in response.headers
    assert int(response.headers['X-RateLimit-Reset']) > int(time.time())
    
    # Second request
    response = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.1'})
    assert response.status_code != 429
    assert int(response.headers['X-RateLimit-Remaining']) == 0


def test_ac2_requests_exceeding_limit_return_429(client, monkeypatch):
    """AC-2: Requests exceeding rate limit return 429 with Retry-After header and correct JSON body"""
    monkeypatch.setenv('RATE_LIMIT_MAX_REQUESTS', '2')
    monkeypatch.setenv('RATE_LIMIT_WINDOW_SECONDS', '60')
    
    # Exhaust the limit
    client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.2'})
    client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.2'})
    
    # Third request should be blocked
    response = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.2'})
    assert response.status_code == 429
    assert 'Retry-After' in response.headers
    retry_after = int(response.headers['Retry-After'])
    assert 0 < retry_after <= 60
    
    assert response.is_json
    response_json = response.get_json()
    assert response_json['error'] == 'Rate limit exceeded'
    assert response_json['retry_after'] == retry_after


def test_ac3_custom_max_requests_env_var_used(client, monkeypatch):
    """AC-3: Custom RATE_LIMIT_MAX_REQUESTS environment variable is respected"""
    monkeypatch.setenv('RATE_LIMIT_MAX_REQUESTS', '50')
    monkeypatch.setenv('RATE_LIMIT_WINDOW_SECONDS', '60')
    
    response = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.3'})
    assert response.headers['X-RateLimit-Limit'] == '50'


def test_ac4_custom_window_seconds_env_var_used(client, monkeypatch):
    """AC-4: Custom RATE_LIMIT_WINDOW_SECONDS environment variable is respected"""
    monkeypatch.setenv('RATE_LIMIT_MAX_REQUESTS', '100')
    monkeypatch.setenv('RATE_LIMIT_WINDOW_SECONDS', '120')
    
    with freeze_time() as frozen_time:
        response = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.4'})
        reset_time = int(response.headers['X-RateLimit-Reset'])
        expected_reset = int(time.time()) + 120
        # Allow small time delta for processing
        assert abs(reset_time - expected_reset) < 2


def test_ac5_rate_limits_isolated_per_ip(client, monkeypatch):
    """AC-5: Rate limits are isolated per client IP address"""
    monkeypatch.setenv('RATE_LIMIT_MAX_REQUESTS', '1')
    monkeypatch.setenv('RATE_LIMIT_WINDOW_SECONDS', '60')
    
    # Exhaust limit for first IP
    client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.5'})
    blocked_resp = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.5'})
    assert blocked_resp.status_code == 429
    
    # Second IP should still have full limit
    allowed_resp = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.6'})
    assert allowed_resp.status_code != 429
    assert int(allowed_resp.headers['X-RateLimit-Remaining']) == 0


def test_ac6_all_endpoints_covered_by_rate_limiting(client, monkeypatch):
    """AC-6: All Flask endpoints are covered by rate limiting middleware"""
    monkeypatch.setenv('RATE_LIMIT_MAX_REQUESTS', '1')
    monkeypatch.setenv('RATE_LIMIT_WINDOW_SECONDS', '60')
    
    test_endpoints = [
        '/health',
        '/ready',
        '/v1/models',
        '/v1/chat/completions'
    ]
    
    for endpoint in test_endpoints:
        # First request allowed
        resp1 = client.post(endpoint, environ_base={'REMOTE_ADDR': '192.168.1.7'}) if endpoint == '/v1/chat/completions' else client.get(endpoint, environ_base={'REMOTE_ADDR': '192.168.1.7'})
        # Second request should be blocked regardless of endpoint
        resp2 = client.post(endpoint, environ_base={'REMOTE_ADDR': '192.168.1.7'}) if endpoint == '/v1/chat/completions' else client.get(endpoint, environ_base={'REMOTE_ADDR': '192.168.1.7'})
        assert resp2.status_code == 429, f"Endpoint {endpoint} not covered by rate limiting"


def test_ac7_rate_limit_resets_after_window(client, monkeypatch):
    """AC-7: Rate limit counter resets after window expires"""
    monkeypatch.setenv('RATE_LIMIT_MAX_REQUESTS', '1')
    monkeypatch.setenv('RATE_LIMIT_WINDOW_SECONDS', '60')
    
    with freeze_time() as frozen_time:
        # Exhaust limit
        client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.8'})
        blocked_resp = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.8'})
        assert blocked_resp.status_code == 429
        
        # Move time forward past window
        frozen_time.tick(61)
        
        # Request should be allowed again
        allowed_resp = client.get('/health', environ_base={'REMOTE_ADDR': '192.168.1.8'})
        assert allowed_resp.status_code != 429
        assert int(allowed_resp.headers['X-RateLimit-Remaining']) == 0
