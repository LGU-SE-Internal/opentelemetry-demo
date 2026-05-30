#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import json
import os
import random
import uuid
import logging

from locust import HttpUser, task, between
from locust_plugins.users.playwright import PlaywrightUser, pw, PageWithRetry, event

from opentelemetry import context, baggage, trace
from opentelemetry.context import Context
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.jinja2 import Jinja2Instrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from openfeature import api
from openfeature.contrib.provider.ofrep import OFREPProvider
from openfeature.contrib.hook.opentelemetry import TracingHook

from playwright.async_api import Route, Request

# Configure tracer provider first (needed for trace context in logs)
tracer_provider = TracerProvider()
trace.set_tracer_provider(tracer_provider)
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(insecure=True)))

# Configure logger provider with the same resource
logger_provider = LoggerProvider()
set_logger_provider(logger_provider)

# Set up log exporter and processor
log_exporter = OTLPLogExporter(insecure=True)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

# Create logging handler that will include trace context
handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)

# Configure root logger
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

# Configure metrics
metric_exporter = OTLPMetricExporter(insecure=True)
set_meter_provider(MeterProvider([PeriodicExportingMetricReader(metric_exporter)]))

# Instrument logging to automatically inject trace context
LoggingInstrumentor().instrument(set_logging_format=True)

# Instrumenting manually to avoid error with locust gevent monkey
Jinja2Instrumentor().instrument()
RequestsInstrumentor().instrument()
SystemMetricsInstrumentor().instrument()
URLLib3Instrumentor().instrument()

logging.info("Instrumentation complete - logs will now include trace context")

# Environment variable configuration and validation
"""
Environment Variables:
- REQUEST_TIMEOUT: Timeout in seconds for HTTP requests. Must be a positive integer >= 1. Default: 10.
- FLAGD_HOST: Hostname or IP address of the Flagd service. Must be a non-empty string. Default: localhost.
- FLAGD_OFREP_PORT: Port number for the Flagd OFREP endpoint. Must be an integer between 1 and 65535. Default: 8016.
"""

# Validate REQUEST_TIMEOUT
REQUEST_TIMEOUT = os.environ.get("REQUEST_TIMEOUT", "10")
try:
    request_timeout_int = int(REQUEST_TIMEOUT)
    if request_timeout_int <= 0:
        raise ValueError(f"REQUEST_TIMEOUT must be a positive integer, got {REQUEST_TIMEOUT}")
except ValueError as e:
    raise ValueError(f"Invalid REQUEST_TIMEOUT value: {REQUEST_TIMEOUT}. Must be a positive integer >= 1.") from e

# Validate FLAGD_HOST
FLAGD_HOST = os.environ.get('FLAGD_HOST', 'localhost')
if not FLAGD_HOST or not isinstance(FLAGD_HOST, str) or len(FLAGD_HOST.strip()) == 0:
    raise ValueError(f"Invalid FLAGD_HOST value: {FLAGD_HOST}. Must be a non-empty hostname or IP address.")
FLAGD_HOST = FLAGD_HOST.strip()

# Validate FLAGD_OFREP_PORT
FLAGD_OFREP_PORT = os.environ.get('FLAGD_OFREP_PORT', 8016)
try:
    flagd_port_int = int(FLAGD_OFREP_PORT)
    if not (1 <= flagd_port_int <= 65535):
        raise ValueError(f"FLAGD_OFREP_PORT must be between 1 and 65535, got {FLAGD_OFREP_PORT}")
except ValueError as e:
    raise ValueError(f"Invalid FLAGD_OFREP_PORT value: {FLAGD_OFREP_PORT}. Must be an integer between 1 and 65535.") from e

# Initialize Flagd provider
base_url = f"http://{FLAGD_HOST}:{flagd_port_int}"
api.set_provider(OFREPProvider(base_url=base_url))
api.add_hooks([TracingHook()])


def get_flagd_value(FlagName):
    # Initialize OpenFeature
    client = api.get_client()
    return client.get_integer_value(FlagName, 0)


categories = [
    "binoculars",
    "telescopes",
    "accessories",
    "assembly",
    "travel",
    "books",
    None,
]

products = [
    "0PUK6V6EV0",
    "1YMWWN1N4O",
    "2ZYFJ3GM2N",
    "66VCHSJNUP",
    "6E92ZMYYFZ",
    "9SIQT8TOJO",
    "L9ECAV7KIM",
    "LS4PSXUNUM",
    "OLJCESPC7Z",
    "HQTGWGPNH4",
]

people_file = open("people.json")
people = json.load(people_file)


class WebsiteUser(HttpUser):
    wait_time = between(1, 10)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracer = trace.get_tracer(__name__)
        self.client.timeout = request_timeout_int

    @task(1)
    def index(self):
        with self.tracer.start_as_current_span("user_index", context=Context()):
            logging.info("User accessing index page")
            self.client.get("/")

    @task(10)
    def browse_product(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span(
            "user_browse_product", context=Context(), attributes={"product.id": product}
        ):
            logging.info(f"User browsing product: {product}")
            self.client.get("/api/products/" + product)

    @task(3)
    def get_recommendations(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span(
            "user_get_recommendations",
            context=Context(),
            attributes={"product.id": product},
        ):
            logging.info(f"User getting recommendations for product: {product}")
            params = {
                "productIds": [product],
            }
            self.client.get("/api/recommendations", params=params)

    @task(2)
    def get_product_reviews(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span(
            "user_get_product_reviews",
            context=Context(),
            attributes={"product.id": product},
        ):
            logging.info(f"User getting product reviews for product: {product}")
            self.client.get("/api/product-reviews/" + product)

    @task(1)
    def ask_product_ai_assistant(self):
        product = random.choice(products)
        question = "Can you summarize the product reviews?"
        with self.tracer.start_as_current_span(
            "user_ask_product_ai_assistant",
            context=Context(),
            attributes={"product.id": product, "question": question},
        ):
            logging.info(
                f"Asking the AI Assistant a question for: {product} {question}"
            )
            question = {"question": question}
            self.client.post("/api/product-ask-ai-assistant/" + product, json=question)

    @task(3)
    def get_ads(self):
        category = random.choice(categories)
        with self.tracer.start_as_current_span(
            "user_get_ads", context=Context(), attributes={"category": str(category)}
        ):
            logging.info(f"User getting ads for category: {category}")
            params = {
                "contextKeys": [category],
            }
            self.client.get("/api/data/", params=params)

    @task(3)
    def view_cart(self):
        with self.tracer.start_as_current_span("user_view_cart", context=Con
