#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import pytest
import time
from locust import events
from flask import testing
from unittest.mock import Mock, patch

# Import the locustfile module
import locustfile as lf


@pytest.fixture
def client():
    # Mock the web_ui object
    mock_web_ui = Mock()
    
    # Create a test Flask app
    from flask import Flask
    app = Flask(__name__)
    mock_web_ui.app = app
    
    # Trigger the init event to register the health endpoint
    events.init.fire(web_ui=mock_web_ui)
    
    # Return test client
    return app.test_client()


def test_health_endpoint_returns_200_ok(client):
    """AC-1: Verify GET /health returns 200 OK when initialization is complete"""
    response = client.get('/health')
    assert response.status_code == 200


def test_health_endpoint_returns_correct_payload(client):
    """AC-2: Verify GET /health returns correct status payload with initialization timestamp"""
    # Get the actual init timestamp from locustfile
    response = client.get('/health')
    data = response.get_json()
    
    assert data['status'] == 'ok'
    assert 'initializationTimestamp' in data
    assert isinstance(data['initializationTimestamp'], float)
    # Verify timestamp is reasonable (not in future, not older than 1 hour)
    assert data['initializationTimestamp'] <= time.time()
    assert data['initializationTimestamp'] > time.time() - 3600


def test_health_endpoint_content_type(client):
    """Verify health endpoint returns JSON content"""
    response = client.get('/health')
    assert response.content_type == 'application/json'
