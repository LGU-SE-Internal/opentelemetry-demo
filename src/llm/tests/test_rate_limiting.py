#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
import time
import pytest
import logging
from src.llm.app import app
from freezegun import freeze_time
from unittest.mock import patch


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def caplog(caplog):
    caplog.set_level(logging.WARN)
    return caplog


def test_ac1_single_ip_n_success_requests_per_minute(client, monkeypatch):
    """AC-1: When RATE_LIMIT_REQUESTS_PER_MINUTE is set to N, single client IP receives N successful 2xx responses within 60s window"""
    test_limit = 5
    monkeypatch.setenv('RATE_LIMIT_REQUESTS_PER_MINUTE', str(test_limit))
    
    for i in range(test_limit):
        response = client.get('/health', environ_base={'REMOTE_ADDR': '10.0.0.1'})
        assert 200 <= response.status_code < 300, f"Request {i+1} failed with status {response.status_code}"


def test_ac2_excess_request_returns_429(client, monkeypatch):
    """AC-2: When N+1 requests sent within 60s window, N+1th returns 429 status code"""
    test_limit = 3
    monkeypatch.setenv('RATE_LIMIT_REQUESTS_PER_MINUTE', str(test_limit))
    
    # Exhaust the limit
    for _ in range(test_limit):
        client.get('/health', environ_base={'REMOTE_ADDR': '10.0.0.2'})
    
    # Excess request
    response = client.get('/health', environ_base={'REMOTE_ADDR': '10.0.0.2'})
    assert response.status_code == 429


def test_ac3_429_response_has_correct_headers_and_body(client, monkeypatch):
    """AC-3: 429 responses include Retry-After header (1-60) and matching retry_after in JSON body"""
    test_limit = 1
    monkeypatch.setenv('RATE_LIMIT_REQUESTS_PER_MINUTE', str(test_limit))
    
    # Exhaust limit
    client.get('/health', environ_base={'REMOTE_ADDR': '10.0.0.3'})
    
    # Get blocked response
    response = client.get('/health', environ_base={'REMOTE_ADDR': '10.0.0.3'})
    
    # Check headers
    assert 'Retry-After' in response.headers
    retry_after = int(response.headers['Retry-After'])
    assert 1 <= retry_after <= 60
    
    # Check body
    assert response.is_json
    body = response.get_json()
    assert body['error'] == 'Too Many Requests'
    assert body['message'] == 'Rate limit exceeded. Try again later.'
    assert body['retry_after'] == retry_after


def test_ac4_default_rate_limit_of_60_when_no_env_var(client, monkeypatch):
    """AC-4: When RATE_LIMIT_REQUESTS_PER_MINUTE is not set, default limit of 60 requests per minute is enforced"""
    # Clear any existing env var
    monkeypatch.delenv('RATE_LIMIT_REQUESTS_PER_MINUTE', raising=False)
    
    # Send 60 requests - all should pass
    for i in range(60):
        response = client.get('/health', environ_base={'REMOTE_ADDR': '10.0.0.4'})
        assert 200 <= response.status_code < 300, f"Request {i+1} failed with status {response.status_code}"
    
    # 61st should fail
    response = client.get('/health', environ_base={'REMOTE_ADDR': '10.0.0.4'})
    assert response.status_code == 429


def test_ac5_rate_limit_violation_logs_all_required_fields(client, monkeypatch, caplog):
    """AC-5: Every rate limit violation is logged with all required fields at WARN level"""
    test_limit = 2
    test_ip = '10.0.0.5'
    test_endpoint = '/health'
    monkeypatch.setenv('RATE_LIMIT_REQUESTS_PER_MINUTE', str(test_limit))
    
    # Exhaust limit
    client.get(test_endpoint, environ_base={'REMOTE_ADDR': test_ip})
    client.get(test_endpoint, environ_base={'REMOTE_ADDR': test_ip})
    
    # Trigger violation
    with patch('src.llm.app.request_id', return_value='test-req-id-123'):
        violation_time = time.time()
        client.get(test_endpoint, environ_base={'REMOTE_ADDR': test_ip})
    
    # Find the warning log
    rate_limit_logs = [record for record in caplog.records if record.levelname == 'WARN']
    assert len(rate_limit_logs) == 1
    log_record = rate_limit_logs[0]
    
    # Check required fields
    assert hasattr(log_record, 'client_ip')
    assert log_record.client_ip == test_ip
    assert hasattr(log_record, 'request_endpoint')
    assert log_record.request_endpoint == test_endpoint
    assert hasattr(log_record, 'rate_limit_threshold')
    assert log_record.rate_limit_threshold == test_limit
    assert hasattr(log_record, 'violation_timestamp')
    # Check timestamp is recent ISO 8601 string
    assert len(log_record.violation_timestamp) > 10
    assert 'T' in log_record.violation_timestamp
    assert hasattr(log_record, 'request_id')
    assert log_record.request_id == 'test-req-id-123'


def test_ac6_distinct_ips_have_isolated_limits(client, monkeypatch):
    """AC-6: Two distinct client IPs can each make N requests without interfering"""
    test_limit = 2
    monkeypatch.setenv('RATE_LIMIT_REQUESTS_PER_MINUTE', str(test_limit))
    
    ip1 = '10.0.0.6'
    ip2 = '10.0.0.7'
    
    # IP1 uses all their limit
    for _ in range(test_limit):
        client.get('/health', environ_base={'REMOTE_ADDR': ip1})
    # IP1 next request blocked
    assert client.get('/health', environ_base={'REMOTE_ADDR': ip1}).status_code == 429
    
    # IP2 still has full limit
    for i in range(test_limit):
        response = client.get('/health', environ_base={'REMOTE_ADDR': ip2})
        assert 200 <= response.status_code < 300, f"IP2 request {i+1} failed"
    
    # IP2 next request blocked
    assert client.get('/health', environ_base={'REMOTE_ADDR': ip2}).status_code == 429


def test_ac7_performance_impact_under_load(client, monkeypatch):
    """AC-7: Response time at 80% of rate limit is no more than 5% higher than baseline (no rate limit)"""
    test_limit = 100
    test_ip = '10.0.0.8'
    
    # Measure baseline with rate limiting disabled
    monkeypatch.setenv('RATE_LIMIT_REQUESTS_PER_MINUTE', '0')  # 0 = disable
    start = time.time()
    for _ in range(int(test_limit * 0.8)):
        client.get('/health', environ_base={'REMOTE_ADDR': test_ip})
    baseline_avg = (time.time() - start) / (test_limit * 0.8)
    
    # Measure with rate limiting enabled
    monkeypatch.setenv('RATE_LIMIT_REQUESTS_PER_MINUTE', str(test_limit))
    start = time.time()
    for _ in range(int(test_limit * 0.8)):
        client.get('/health', environ_base={'REMOTE_ADDR': test_ip})
    rate_limit_avg = (time.time() - start) / (test_limit * 0.8)
    
    # Check performance impact < 5%
    assert rate_limit_avg <= baseline_avg * 1.05, f"Rate limiting added >5% latency: baseline={baseline_avg}s, with limit={rate_limit_avg}s"
