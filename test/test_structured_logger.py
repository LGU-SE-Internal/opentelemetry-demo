#!/usr/bin/env python3

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import json
import io
import logging
import unittest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
from src.recommendation.logger import getJSONLogger, CustomJsonFormatter


class TestStructuredLogging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Configure trace provider for tests
        trace.set_tracer_provider(TracerProvider())
        tracer_provider = trace.get_tracer_provider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        cls.tracer = trace.get_tracer(__name__)

    def setUp(self):
        # Capture stdout for each test
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.logger = getJSONLogger("test_logger")
        # Replace default handler with our test stream handler
        self.logger.handlers.clear()
        self.formatter = CustomJsonFormatter(
            "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] - %(message)s"
        )
        self.handler.setFormatter(self.formatter)
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)  # Set to debug to test all levels

    def get_log_output(self):
        self.handler.flush()
        output = self.stream.getvalue().strip()
        self.stream.truncate(0)
        self.stream.seek(0)
        return json.loads(output) if output else None

    def test_info_log_format(self):
        test_msg = "Test info message"
        self.logger.info(test_msg)
        log = self.get_log_output()
        self.assertIsNotNone(log)
        self.assertEqual(log["levelname"], "INFO")
        self.assertEqual(log["message"], test_msg)
        self.assertIn("asctime", log)
        self.assertIn("otelTraceID", log)
        self.assertIn("otelSpanID", log)
        self.assertEqual(log["name"], "test_logger")

    def test_warn_log_format(self):
        test_msg = "Test warn message"
        self.logger.warning(test_msg)
        log = self.get_log_output()
        self.assertEqual(log["levelname"], "WARNING")
        self.assertEqual(log["message"], test_msg)
        self.assertIn("asctime", log)
        self.assertIn("otelTraceID", log)

    def test_error_log_format(self):
        test_msg = "Test error message"
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            self.logger.error(test_msg, exc_info=e)
        log = self.get_log_output()
        self.assertEqual(log["levelname"], "ERROR")
        self.assertEqual(log["message"], test_msg)
        self.assertIn("exc_info", log)
        self.assertIn("asctime", log)

    def test_debug_log_format(self):
        test_msg = "Test debug message"
        self.logger.debug(test_msg)
        log = self.get_log_output()
        self.assertEqual(log["levelname"], "DEBUG")
        self.assertEqual(log["message"], test_msg)

    def test_log_fields_with_active_trace(self):
        with self.tracer.start_as_current_span("test-span") as span:
            expected_trace_id = trace.format_trace_id(span.get_span_context().trace_id)
            expected_span_id = trace.format_span_id(span.get_span_context().span_id)
            self.logger.info("Test with trace")
            log = self.get_log_output()
            self.assertEqual(log["otelTraceID"], expected_trace_id)
            self.assertEqual(log["otelSpanID"], expected_span_id)

    def test_empty_message(self):
        self.logger.info("")
        log = self.get_log_output()
        self.assertEqual(log["message"], "")
        self.assertIn("asctime", log)
        self.assertIn("levelname", log)

    def test_custom_metadata_fields(self):
        test_msg = "Test with custom fields"
        custom_data = {"user_id": "12345", "operation": "purchase", "amount": 99.99}
        self.logger.info(test_msg, extra=custom_data)
        log = self.get_log_output()
        self.assertEqual(log["message"], test_msg)
        self.assertEqual(log["user_id"], "12345")
        self.assertEqual(log["operation"], "purchase")
        self.assertEqual(log["amount"], 99.99)

    def test_error_object_passed(self):
        test_err = RuntimeError("Something went very wrong")
        self.logger.error("Error occurred", extra={"error": test_err})
        log = self.get_log_output()
        self.assertEqual(log["levelname"], "ERROR")
        self.assertEqual(log["message"], "Error occurred")
        self.assertIn("error", log)
        self.assertIn("Something went very wrong", log["error"])


if __name__ == "__main__":
    unittest.main()
