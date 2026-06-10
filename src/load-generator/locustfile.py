#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Locust load test definitions for the OpenTelemetry demo application."""

import json
import os
import random
import uuid
import logging
import signal
import sys
import time
from types import FrameType
from typing import Optional, NoReturn

from locust import HttpUser, task, between
from locust_plugins.users.playwright import PlaywrightUser, pw, PageWithRetry, event

from opentelemetry import context, baggage, trace, metrics, _logs as logs
from opentelemetry.context import Context
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.jinja2 import Jinja2Instrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from openfeature import api
from openfeature.contrib.provider.ofrep import OFREPProvider
from openfeature.contrib.hook.opentelemetry import TracingHook


# Alias exporters for testability
TraceExporter = OTLPSpanExporter
MetricExporter = OTLPMetricExporter
LogExporter = OTLPLogExporter

# Load and validate configuration from environment variables
def load_config() -> dict:
    config = {
        "wait_time_min": 1,
        "wait_time_max": 10,
        "browser_navigation_timeout": 15,
        "trace_flush_wait": 2
    }
    
    # Parse and validate wait time min
    wait_min_str = os.environ.get("LOADGEN_WAIT_TIME_MIN_SECONDS")
    if wait_min_str is not None:
        try:
            config["wait_time_min"] = int(wait_min_str)
            if config["wait_time_min"] <= 0:
                logging.error("LOADGEN_WAIT_TIME_MIN_SECONDS must be a positive integer")
                sys.exit(1)
        except ValueError:
            logging.error("LOADGEN_WAIT_TIME_MIN_SECONDS must be a valid integer")
            sys.exit(1)
    
    # Parse and validate wait time max
    wait_max_str = os.environ.get("LOADGEN_WAIT_TIME_MAX_SECONDS")
    if wait_max_str is not None:
        try:
            config["wait_time_max"] = int(wait_max_str)
            if config["wait_time_max"] <= 0:
                logging.error("LOADGEN_WAIT_TIME_MAX_SECONDS must be a positive integer")
                sys.exit(1)
        except ValueError:
            logging.error("LOADGEN_WAIT_TIME_MAX_SECONDS must be a valid integer")
            sys.exit(1)
    
    # Validate min <= max
    if config["wait_time_min"] > config["wait_time_max"]:
        logging.error("LOADGEN_WAIT_TIME_MIN_SECONDS cannot be greater than LOADGEN_WAIT_TIME_MAX_SECONDS")
        sys.exit(1)
    
    # Parse and validate browser navigation timeout
    nav_timeout_str = os.environ.get("LOADGEN_BROWSER_NAVIGATION_TIMEOUT_SECONDS")
    if nav_timeout_str is not None:
        try:
            config["browser_navigation_timeout"] = int(nav_timeout_str)
            if config["browser_navigation_timeout"] <= 0:
                logging.error("LOADGEN_BROWSER_NAVIGATION_TIMEOUT_SECONDS must be a positive integer")
                sys.exit(1)
        except ValueError:
            logging.error("LOADGEN_BROWSER_NAVIGATION_TIMEOUT_SECONDS must be a valid integer")
            sys.exit(1)
    
    # Parse and validate trace flush wait time
    flush_wait_str = os.environ.get("LOADGEN_TRACE_FLUSH_WAIT_SECONDS")
    if flush_wait_str is not None:
        try:
            config["trace_flush_wait"] = int(flush_wait_str)
            if config["trace_flush_wait"] <= 0:
                logging.error("LOADGEN_TRACE_FLUSH_WAIT_SECONDS must be a positive integer")
                sys.exit(1)
        except ValueError:
            logging.error("LOADGEN_TRACE_FLUSH_WAIT_SECONDS must be a valid integer")
            sys.exit(1)
    
    return config

# Load configuration at module level so it's available to all classes
CONFIG = load_config()

def initialize_otel_exporters():
    # Read environment variables with defaults
    traces_endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://otel-collector:4318/v1/traces"
    )
    metrics_endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "http://otel-collector:4318/v1/metrics"
    )
    logs_endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "http://otel-collector:4318/v1/logs"
    )
    
    insecure = os.environ.get("OTEL_EXPORTER_OTLP_INSECURE", "False").lower() == "true"
    
    client_cert = os.environ.get("OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE", "")
    client_key = os.environ.get("OTEL_EXPORTER_OTLP_CLIENT_KEY", "")
    ca_cert = os.environ.get("OTEL_EXPORTER_OTLP_CERTIFICATE_AUTHORITY", "")
    
    retry_max_attempts = int(os.environ.get("OTEL_EXPORTER_OTLP_RETRY_MAX_ATTEMPTS", 5))
    retry_initial_delay = float(os.environ.get("OTEL_EXPORTER_OTLP_RETRY_INITIAL_DELAY", 1.0))
    retry_max_delay = float(os.environ.get("OTEL_EXPORTER_OTLP_RETRY_MAX_DELAY", 5.0))
    
    # Parse OTLP timeout with fallback to 10s for invalid/non-positive values
    try:
        timeout = int(os.environ.get("OTEL_EXPORTER_OTLP_TIMEOUT", 10))
        if timeout <= 0:
            timeout = 10
    except (ValueError, TypeError):
        timeout = 10
    
    # Build common exporter parameters
    common_params = {
        "insecure": insecure,
        "timeout": timeout
    }
    
    if client_cert and client_key:
        common_params["client_cert_path"] = client_cert
        common_params["client_key_path"] = client_key
    
    if ca_cert:
        common_params["certificate_path"] = ca_cert
    
    # Add retry configuration for HTTP exporter
    common_params["retry"] = {
        "max_attempts": retry_max_attempts,
        "initial_delay": retry_initial_delay,
        "max_delay": retry_max_delay,
        "backoff_multiplier": 2,
        "retry_on_status_codes": [429, 502, 503, 504]
    }
    
    # Configure tracer provider first (needed for trace context in logs)
    tracer_provider = TracerProvider()
    trace.set_tracer_provider(tracer_provider)
    trace_exporter = TraceExporter(endpoint=traces_endpoint, **common_params)
    tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    
    # Configure logger provider with the same resource
    logger_provider = LoggerProvider()
    set_logger_provider(logger_provider)
    
    # Set up log exporter and processor
    log_exporter = LogExporter(endpoint=logs_endpoint, **common_params)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    
    # Create logging handler that will include trace context
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    
    # Configure metrics
    metric_exporter = MetricExporter(endpoint=metrics_endpoint, **common_params)
    set_meter_provider(MeterProvider([PeriodicExportingMetricReader(metric_exporter)]))
    
    # Instrument logging to automatically inject trace context
    LoggingInstrumentor().instrument(set_logging_format=True)

initialize_otel_exporters()

# Instrumenting manually to avoid error with locust gevent monkey
Jinja2Instrumentor().instrument()
RequestsInstrumentor().instrument()
SystemMetricsInstrumentor().instrument()
URLLib3Instrumentor().instrument()

logging.info("Instrumentation complete - logs will now include trace context")

def graceful_shutdown(signum: int, frame: Optional[FrameType], environment) -> NoReturn:
    """Signal handler for graceful shutdown sequence"""
    logging.info("Starting graceful shutdown (10s timeout)...")
    
    # Stop Locust runner to prevent new requests/users
    if environment.runner:
        environment.runner.stop()
    
    # Wait for in-flight requests to complete, up to 10s
    start_time = time.time()
    timeout = 10
    
    while time.time() - start_time < timeout:
        # Check if there are any running users
        if not environment.runner or environment.runner.user_count == 0:
            break
        time.sleep(0.1)
    
    # Flush all OTel data
    try:
        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, "force_flush"):
            tracer_provider.force_flush(timeout_millis=2000)
    except Exception as e:
        logging.warning(f"Failed to flush traces: {str(e)}")
    
    try:
        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "force_flush"):
            meter_provider.force_flush(timeout_millis=2000)
    except Exception as e:
        logging.warning(f"Failed to flush metrics: {str(e)}")
    
    try:
        logger_provider = logs.get_logger_provider()
        if hasattr(logger_provider, "force_flush"):
            logger_provider.force_flush(timeout_millis=2000)
    except Exception as e:
        logging.warning(f"Failed to flush logs: {str(e)}")
    
    # Check if we timed out
    if time.time() - start_time >= timeout:
        logging.warning("Graceful shutdown timed out after 10s, forcing exit")
        sys.exit(1)
    
    logging.info("Graceful shutdown completed successfully")
    sys.exit(0)

# Initialize Flagd provider
base_url = f"http://{os.environ.get('FLAGD_HOST', 'localhost')}:{os.environ.get('FLAGD_OFREP_PORT', 8016)}"
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

people_file = open('people.json')
people = json.load(people_file)

class WebsiteUser(HttpUser):
    wait_time = between(CONFIG["wait_time_min"], CONFIG["wait_time_max"])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracer = trace.get_tracer(__name__)

    @task(1)
    def index(self):
        with self.tracer.start_as_current_span("user_index", context=Context()):
            logging.info("User accessing index page")
            self.client.get("/")

    @task(10)
    def browse_product(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span("user_browse_product", context=Context(), attributes={"product.id": product}):
            logging.info(f"User browsing product: {product}")
            self.client.get("/api/products/" + product)

    @task(3)
    def get_recommendations(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span("user_get_recommendations", context=Context(), attributes={"product.id": product}):
            logging.info(f"User getting recommendations for product: {product}")
            params = {
                "productIds": [product],
            }
            self.client.get("/api/recommendations", params=params)

    @task(2)
    def get_product_reviews(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span("user_get_product_reviews", context=Context(), attributes={"product.id": product}):
            logging.info(f"User getting product reviews for product: {product}")
            self.client.get("/api/product-reviews/" + product)

    @task(1)
    def ask_product_ai_assistant(self):
        product = random.choice(products)
        question = 'Can you summarize the product reviews?'
        with self.tracer.start_as_current_span("user_ask_product_ai_assistant", context=Context(), attributes={"product.id": product, "question": question}):
            logging.info(f"Asking the AI Assistant a question for: {product} {question}")
            question = {
                "question": question
            }
            self.client.post("/api/product-ask-ai-assistant/" + product, json=question)

    @task(3)
    def get_ads(self):
        category = random.choice(categories)
        with self.tracer.start_as_current_span("user_get_ads", context=Context(), attributes={"category": str(category)}):
            logging.info(f"User getting ads for category: {category}")
            params = {
                "contextKeys": [category],
            }
            self.client.get("/api/data/", params=params)

    @task(3)
    def view_cart(self):
        with self.tracer.start_as_current_span("user_view_cart", context=Context()):
            logging.info("User viewing cart")
            self.client.get("/api/cart")

    @task(2)
    def add_to_cart(self, user=""):
        if user == "":
            user = str(uuid.uuid1())
        product = random.choice(products)
        quantity = random.choice([1, 2, 3, 4, 5, 10])
        with self.tracer.start_as_current_span("user_add_to_cart", context=Context(), attributes={"user.id": user, "product.id": product, "quantity": quantity}):
            logging.info(f"User {user} adding {quantity} of product {product} to cart")
            self.client.get("/api/products/" + product)
            cart_item = {
                "item": {
                    "productId": product,
                    "quantity": quantity,
                },
                "userId": user,
            }
            self.client.post("/api/cart", json=cart_item)

    @task(1)
    def checkout(self) -> None:
        user = str(uuid.uuid1())
        with self.tracer.start_as_current_span("user_checkout_single", context=Context(), attributes={"user.id": user}):
            self.add_to_cart(user=user)
            checkout_person = random.choice(people)
            checkout_person["userId"] = user
            self.client.post("/api/checkout", json=checkout_person)
            logging.info(f"Checkout completed for user {user}")

    @task(1)
    def checkout_multi(self):
        user = str(uuid.uuid1())
        item_count = random.choice([2, 3, 4])
        with self.tracer.start_as_current_span("user_checkout_multi", context=Context(),
                                            attributes={"user.id": user, "item.count": item_count}):
            for i in range(item_count):
                self.add_to_cart(user=user)
            checkout_person = random.choice(people)
            checkout_person["userId"] = user
            self.client.post("/api/checkout", json=checkout_person)
            logging.info(f"Multi-item checkout completed for user {user}")

    @task(5)
    def flood_home(self):
        flood_count = get_flagd_value("loadGeneratorFloodHomepage")
        if flood_count > 0:
            with self.tracer.start_as_current_span("user_flood_home",  context=Context(), attributes={"flood.count": flood_count}):
                logging.info(f"User flooding homepage {flood_count} times")
                for _ in range(0, flood_count):
                    self.client.get("/")

    def on_start(self):
        with self.tracer.start_as_current_span("user_session_start", context=Context()):
            session_id = str(uuid.uuid4())
            logging.info(f"Starting user session: {session_id}")
            ctx = baggage.set_baggage("session.id", session_id)
            ctx = baggage.set_baggage("synthetic_request", "true", context=ctx)
            context.attach(ctx)
            self.index()


browser_traffic_enabled = os.environ.get("LOCUST_BROWSER_TRAFFIC_ENABLED", "").lower() in ("true", "yes", "on")

if browser_traffic_enabled:
    class WebsiteBrowserUser(PlaywrightUser):
        headless = True  # to use a headless browser, without a GUI

        @task
        @pw
        async def open_cart_page_and_change_currency(self, page: PageWithRetry):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span("browser_change_currency", context=Context()):
                try:
                    page.on("console", lambda msg: print(msg.text))
                    await page.route('**/*', add_baggage_header)
                    await page.goto("/cart", wait_until="domcontentloaded")
                    await page.select_option('[name="currency_code"]', 'CHF')
                    await page.wait_for_timeout(CONFIG["trace_flush_wait"] * 1000)  # giving the browser time to export the traces
                    logging.info("Currency changed to CHF")
                except Exception as e:
                    logging.error(f"Error in change currency task: {str(e)}")

        @task
        @pw
        async def add_product_to_cart(self, page: PageWithRetry):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span("browser_add_to_cart", context=Context()):
                try:
                    page.on("console", lambda msg: print(msg.text))
                    await page.route('**/*', add_baggage_header)
                    await page.goto("/", wait_until="domcontentloaded")
                    # Wait for Roof Binoculars image to load (awaiting successful XHR response in less than configured timeout)
                    await page.wait_for_event(
                        "response",
                        predicate=lambda r: '/images/products/RoofBinoculars.jpg' in r.url and r.status == 200,
                        timeout=CONFIG["browser_navigation_timeout"] * 1000
                    )
                    await page.click('p:has-text("Roof Binoculars")')
                    await page.wait_for_load_state("domcontentloaded")
                    await page.click('button:has-text("Add To Cart")')
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(CONFIG["trace_flush_wait"] * 1000)  # giving the browser time to export the traces
                    logging.info("Product added to cart successfully")
                except Exception as e:
                    logging.error(f"Error in add to cart task: {str(e)}")

async def add_baggage_header(route: Route, request: Request):
    existing_baggage = request.headers.get('baggage', '')
    headers = {
        **request.headers,
        'baggage': ', '.join(filter(None, (existing_baggage, 'synthetic_request=true')))
    }
    await route.continue_(headers=headers)

# Add health and readiness probe endpoints
from locust import events
from flask import Response

@events.init.add_listener
def add_health_probe_endpoints(environment, **kwargs):
    # Register signal handlers for graceful shutdown
    from functools import partial
    signal.signal(signal.SIGINT, partial(graceful_shutdown, environment=environment))
    signal.signal(signal.SIGTERM, partial(graceful_shutdown, environment=environment))
    
    if environment.web_ui:
        app = environment.web_ui.app

        @app.route("/health/liveness")
        def liveness_probe():
            return Response("OK", status=200, mimetype="text/plain")

        @app.route("/health/readiness")
        def readiness_probe():
            if environment.runner is not None:
                return Response("READY", status=200, mimetype="text/plain")
            return Response("NOT_READY", status=503, mimetype="text/plain")
