#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import MagicMock, patch
from locust import events

# Import the counter functions and handlers from locustfile
from locustfile import (
    on_request_success,
    on_request_failure,
    successful_tasks_counter,
    failed_tasks_counter
)


def test_successful_task_counter_increment():
    """AC-1: Test that successful task execution counter increments correctly"""
    # Mock the counter add method
    with patch.object(successful_tasks_counter, 'add') as mock_add:
        # Trigger the success event handler
        on_request_success(
            request_type="GET",
            name="test_task",
            response_time=100.0,
            response_length=200
        )
        
        # Verify counter was incremented once with correct attributes
        mock_add.assert_called_once_with(
            1,
            {"task_name": "test_task", "request_type": "GET"}
        )


def test_failed_task_counter_increment():
    """AC-2: Test that failed task execution counter increments correctly"""
    # Mock the counter add method
    with patch.object(failed_tasks_counter, 'add') as mock_add:
        # Create a test exception
        test_exception = ValueError("Test error")
        
        # Trigger the failure event handler
        on_request_failure(
            request_type="POST",
            name="test_failed_task",
            response_time=50.0,
            exception=test_exception
        )
        
        # Verify counter was incremented once with correct attributes
        mock_add.assert_called_once_with(
            1,
            {
                "task_name": "test_failed_task",
                "request_type": "POST",
                "exception_type": "ValueError"
            }
        )


def test_success_event_handler_registered():
    """Test that the success handler is properly registered with Locust events"""
    assert on_request_success in events.request_success._handlers


def test_failure_event_handler_registered():
    """Test that the failure handler is properly registered with Locust events"""
    assert on_request_failure in events.request_failure._handlers
