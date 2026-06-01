#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import patch
from locustfile import on_request_success, on_request_failure


def test_on_request_success_increments_counter():
    """Test that successful task execution counter is incremented with correct attributes"""
    with patch('locustfile.successful_tasks_counter') as mock_counter:
        request_type = "GET"
        task_name = "test_task"
        response_time = 100
        response_length = 200

        on_request_success(request_type, task_name, response_time, response_length)

        mock_counter.add.assert_called_once_with(
            1,
            {
                "task_name": task_name,
                "request_type": request_type
            }
        )


def test_on_request_failure_increments_counter():
    """Test that failed task execution counter is incremented with correct attributes"""
    with patch('locustfile.failed_tasks_counter') as mock_counter:
        request_type = "POST"
        task_name = "test_failed_task"
        response_time = 50
        exception = ValueError("Test error")

        on_request_failure(request_type, task_name, response_time, exception)

        mock_counter.add.assert_called_once_with(
            1,
            {
                "task_name": task_name,
                "request_type": request_type,
                "exception_type": "ValueError"
            }
        )
