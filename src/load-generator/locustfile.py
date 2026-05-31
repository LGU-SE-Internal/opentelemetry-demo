#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import json
import os
import urllib.parse
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

SERVICE_VERSION = "1.0.0"

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
logging.info(f"Load generator v{SERVICE_VERSION} starting")

# Initialize Flagd provider
ofrep_endpoint = os.environ.get("OFREP_PROVIDER_ENDPOINT")
if ofrep_endpoint:
    base_url = ofrep_endpoint
else:
    flagd_host = os.environ.get('FLAGD_HOST', 'localhost')
    flagd_port = os.environ.get('FLAGD_OFREP_PORT', 8016)
    base_url = f"http://{flagd_host}:{flagd_port}"

# Validate endpoint URL
try:
    result = urllib.parse.urlparse(base_url)
    if not all([result.scheme, result.netloc]):
        raise ValueError("Missing scheme or network location")
except Exception as e:
    logging.error(f"Invalid OFREP provider endpoint: {base_url}. Error: {str(e)}")
    logging.error("Please set OFREP_PROVIDER_ENDPOINT to a valid URL, or ensure FLAGD_HOST and FLAGD_OFREP_PORT are correctly configured")
    raise SystemExit(1)

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
logging.info(f"Loaded {len(categories)} product categories")
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

logging.info(f"Loaded {len(products)} product IDs")

with open('people.json') as people_file:
    people = json.load(people_file)

logging.info(f"Loaded {len(people)} people entries from people.json")
LOCUST_WAIT_MIN = int(os.environ.get('LOCUST_WAIT_MIN', 1))
LOCUST_WAIT_MAX = int(os.environ.get('LOCUST_WAIT_MAX', 10))

class WebsiteUser(HttpUser):
    wait_time = between(LOCUST_WAIT_MIN, LOCUST_WAIT_MAX)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracer = trace.get_tracer(__name__)

    @task(1)
    def index(self):
        with self.tracer.start_as_current_span("user_index", context=Context()):
            logging.info("User accessing index page")
            response = self.client.get("/")
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "index",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": "/"
                }
            )

    @task(10)
    def browse_product(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span("user_browse_product", context=Context(), attributes={"product.id": product}):
            endpoint = f"/api/products/{product}"
            logging.info(f"User browsing product: {product}")
            response = self.client.get(endpoint)
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "browse_product",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": endpoint
                }
            )

    @task(3)
    def get_recommendations(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span("user_get_recommendations", context=Context(), attributes={"product.id": product}):
            endpoint = "/api/recommendations"
            logging.info(f"User getting recommendations for product: {product}")
            params = {
                "productIds": [product],
            }
            response = self.client.get(endpoint, params=params)
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "get_recommendations",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": endpoint
                }
            )

    @task(2)
    def get_product_reviews(self):
        product = random.choice(products)
        with self.tracer.start_as_current_span("user_get_product_reviews", context=Context(), attributes={"product.id": product}):
            endpoint = f"/api/product-reviews/{product}"
            logging.info(f"User getting product reviews for product: {product}")
            response = self.client.get(endpoint)
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "get_product_reviews",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": endpoint
                }
            )

    @task(1)
    def ask_product_ai_assistant(self):
        product = random.choice(products)
        question = 'Can you summarize the product reviews?'
        with self.tracer.start_as_current_span("user_ask_product_ai_assistant", context=Context(), attributes={"product.id": product, "question": question}):
            endpoint = f"/api/product-ask-ai-assistant/{product}"
            logging.info(f"Asking the AI Assistant a question for: {product} {question}")
            question_payload = {
                "question": question
            }
            response = self.client.post(endpoint, json=question_payload)
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "ask_product_ai_assistant",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": endpoint
                }
            )

    @task(3)
    def get_ads(self):
        category = random.choice(categories)
        with self.tracer.start_as_current_span("user_get_ads", context=Context(), attributes={"category": str(category)}):
            endpoint = "/api/data/"
            logging.info(f"User getting ads for category: {category}")
            params = {
                "contextKeys": [category],
            }
            response = self.client.get(endpoint, params=params)
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "get_ads",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": endpoint
                }
            )

    @task(3)
    def view_cart(self):
        with self.tracer.start_as_current_span("user_view_cart", context=Context()):
            endpoint = "/api/cart"
            logging.info("User viewing cart")
            response = self.client.get(endpoint)
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "view_cart",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": endpoint
                }
            )

    @task(2)
    def add_to_cart(self, user: str = "") -> None:
        if user == "":
            user = str(uuid.uuid1())
            product = random.choice(products)
            quantity = random.choice([1, 2, 3, 4, 5, 10])
            with self.tracer.start_as_current_span("user_add_to_cart", context=Context(), attributes={"user.id": user, "product.id": product, "quantity": quantity}):
                logging.info(f"User {user} adding {quantity} of product {product} to cart")
                endpoint_get = f"/api/products/{product}"
                response_get = self.client.get(endpoint_get)
                logging.info(
                    "Successful task request",
                    extra={
                        "task_name": "add_to_cart",
                        "response_time_ms": round(response_get.elapsed.total_seconds() * 1000, 2),
                        "response_status_code": response_get.status_code,
                        "endpoint": endpoint_get
                    }
                )
                cart_item = {
                    "item": {
                        "productId": product,
                        "quantity": quantity,
                    },
                    "userId": user,
                }
                endpoint_post = "/api/cart"
                response_post = self.client.post(endpoint_post, json=cart_item)
                logging.info(
                    "Successful task request",
                    extra={
                        "task_name": "add_to_cart",
                        "response_time_ms": round(response_post.elapsed.total_seconds() * 1000, 2),
                        "response_status_code": response_post.status_code,
                        "endpoint": endpoint_post
                    }
                )

    @task(1)
    def checkout(self) -> None:
        user = str(uuid.uuid1())
        with self.tracer.start_as_current_span("user_checkout_single", context=Context(), attributes={"user.id": user}):
            self.add_to_cart(user=user)
            checkout_person = random.choice(people)
            checkout_person["userId"] = user
            endpoint = "/api/checkout"
            response = self.client.post(endpoint, json=checkout_person)
            logging.info(
                "Successful task request",
                extra={
                    "task_name": "checkout",
                    "response_time_ms": round(response.elapsed.total_seconds() * 1000, 2),
                    "response_status_code": response.status_code,
                    "endpoint": endpoint
                }
            )
            logging.info(f"Checkout completed for user {user}")

    @task(1)
    def checkout_multi(self) -> None:
        user = str(uuid.uuid1())
        item_count = random.choice([2, 3, 4])
        with self.tracer.start_as_current_span("user_checkout_multi", context=Context(),
                                            attributes={"user.id": user, "item.count": item_count}):
            for i in range(item_count):
                self.add_to_cart(user=user)
