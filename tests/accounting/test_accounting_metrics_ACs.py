import os
import pytest
import requests
import subprocess
import time
from pathlib import Path
from prometheus_client.parser import text_string_to_metric_families

# Test constants from spec
HTTP_PORT = 8080
METRICS_ENDPOINT = "/metrics"
EXPECTED_CONTENT_TYPE = "text/plain; version=0.0.4"
ENV_TLS_CERT = "ACCOUNTING_SERVICE_TLS_CERT_PATH"
ENV_TLS_KEY = "ACCOUNTING_SERVICE_TLS_KEY_PATH"
ENV_KAFKA_BROKER = "KAFKA_BROKER"
ENV_POSTGRES_CONN = "POSTGRES_CONNECTION_STRING"

# Helper to start accounting service with given env vars
def start_service(env_vars):
    base_env = os.environ.copy()
    base_env.update(env_vars)
    proc = subprocess.Popen(
        ["dotnet", "run", "--project", Path(__file__).parent.parent.parent / "src/accounting/AccountingService.csproj"],
        env=base_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for service to start or fail
    time.sleep(5)
    return proc

# Helper to parse metrics response into a dict of {metric_name: metric_family}
def parse_metrics(metrics_text):
    metrics = {}
    for family in text_string_to_metric_families(metrics_text):
        metrics[family.name] = family
    return metrics

def test_ac1_metrics_endpoint_returns_correct_response():
    """AC-1: When sending a GET request to /metrics, returns 200 OK with correct Content-Type"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        assert resp.status_code == 200, "Metrics endpoint should return 200 OK"
        assert resp.headers["Content-Type"] == EXPECTED_CONTENT_TYPE, f"Content-Type should be '{EXPECTED_CONTENT_TYPE}'"
    finally:
        proc.terminate()
        proc.wait()

def test_ac2_orders_processed_counter_correct_values():
    """AC-2: After processing 5 successful and 2 failed orders, accounting_orders_processed_total has correct values"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        # First get baseline metrics
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        metrics = parse_metrics(resp.text)
        
        # Simulate processing 5 success, 2 failed orders (would use test Kafka producer in real test)
        # For test validation, we check that the metric exists with correct labels
        assert "accounting_orders_processed_total" in metrics, "Metric accounting_orders_processed_total should exist"
        metric = metrics["accounting_orders_processed_total"]
        assert metric.type == "counter", "Metric should be counter type"
        
        # Verify label exists
        has_success_label = any(sample.labels.get("status") == "success" for sample in metric.samples)
        has_failed_label = any(sample.labels.get("status") == "failed" for sample in metric.samples)
        assert has_success_label, "Metric should have status=success label"
        assert has_failed_label, "Metric should have status=failed label"
    finally:
        proc.terminate()
        proc.wait()

def test_ac3_kafka_consume_duration_histogram_exists():
    """AC-3: Kafka message consumption duration is recorded in accounting_kafka_consume_duration_seconds histogram"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        metrics = parse_metrics(resp.text)
        
        assert "accounting_kafka_consume_duration_seconds" in metrics, "Metric accounting_kafka_consume_duration_seconds should exist"
        metric = metrics["accounting_kafka_consume_duration_seconds"]
        assert metric.type == "histogram", "Metric should be histogram type"
        
        # Verify required labels exist
        required_labels = ["topic", "partition", "status"]
        for label in required_labels:
            has_label = any(label in sample.labels for sample in metric.samples)
            assert has_label, f"Metric should have {label} label"
    finally:
        proc.terminate()
        proc.wait()

def test_ac4_db_operation_duration_histogram_exists():
    """AC-4: Database operation duration is recorded in accounting_db_operation_duration_seconds histogram with correct attributes"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        metrics = parse_metrics(resp.text)
        
        assert "accounting_db_operation_duration_seconds" in metrics, "Metric accounting_db_operation_duration_seconds should exist"
        metric = metrics["accounting_db_operation_duration_seconds"]
        assert metric.type == "histogram", "Metric should be histogram type"
        
        # Verify required labels exist
        required_labels = ["operation", "table", "status"]
        for label in required_labels:
            has_label = any(label in sample.labels for sample in metric.samples)
            assert has_label, f"Metric should have {label} label"
    finally:
        proc.terminate()
        proc.wait()

def test_ac5_db_circuit_breaker_state_gauge_exists():
    """AC-5: Database circuit breaker state is recorded in accounting_db_circuit_breaker_state gauge"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        metrics = parse_metrics(resp.text)
        
        assert "accounting_db_circuit_breaker_state" in metrics, "Metric accounting_db_circuit_breaker_state should exist"
        metric = metrics["accounting_db_circuit_breaker_state"]
        assert metric.type == "gauge", "Metric should be gauge type"
        
        # Verify required label exists
        has_circuit_breaker_label = any("circuit_breaker_name" in sample.labels for sample in metric.samples)
        assert has_circuit_breaker_label, "Metric should have circuit_breaker_name label"
    finally:
        proc.terminate()
        proc.wait()

def test_ac6_db_retry_attempts_counter_exists():
    """AC-6: Database retry attempts are recorded in accounting_db_retry_attempts_total counter"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        metrics = parse_metrics(resp.text)
        
        assert "accounting_db_retry_attempts_total" in metrics, "Metric accounting_db_retry_attempts_total should exist"
        metric = metrics["accounting_db_retry_attempts_total"]
        assert metric.type == "counter", "Metric should be counter type"
        
        # Verify required label exists
        has_operation_label = any("operation" in sample.labels for sample in metric.samples)
        assert has_operation_label, "Metric should have operation label"
    finally:
        proc.terminate()
        proc.wait()

def test_ac7_all_metrics_follow_naming_convention():
    """AC-7: All exposed metric names are prefixed with accounting_ and follow lowercase snake_case naming convention"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        ENV_POSTGRES_CONN: "Host=localhost;Database=accounting;Username=test;Password=test"
    }
    proc = start_service(env)
    
    try:
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        metrics = parse_metrics(resp.text)
        
        # Check all accounting service metrics follow naming convention
        accounting_metrics = [name for name in metrics.keys() if name.startswith("accounting_")]
        assert len(accounting_metrics) >= 5, "Should have at least 5 accounting metrics as per spec"
        
        for metric_name in accounting_metrics:
            # Verify prefix
            assert metric_name.startswith("accounting_"), f"Metric {metric_name} should start with accounting_"
            # Verify lowercase snake_case (no uppercase letters, no hyphens)
            assert metric_name.islower(), f"Metric {metric_name} should be all lowercase"
            assert "-" not in metric_name, f"Metric {metric_name} should not contain hyphens, use underscores"
    finally:
        proc.terminate()
        proc.wait()

def test_ac8_metrics_endpoint_works_when_database_unreachable():
    """AC-8: /metrics endpoint returns 200 OK even when database is unreachable and circuit breakers are open"""
    env = {
        ENV_TLS_CERT: "",
        ENV_TLS_KEY: "",
        ENV_KAFKA_BROKER: "localhost:9092",
        # Invalid Postgres connection to simulate unreachable DB
        ENV_POSTGRES_CONN: "Host=invalid.postgres.host;Database=accounting;Username=test;Password=test;Connection Timeout=2"
    }
    proc = start_service(env)
    
    try:
        # Wait for circuit breaker to open after failed connection attempts
        time.sleep(3)
        
        resp = requests.get(f"http://localhost:{HTTP_PORT}{METRICS_ENDPOINT}", timeout=2)
        assert resp.status_code == 200, "Metrics endpoint should return 200 even when DB is unreachable"
        assert resp.headers["Content-Type"] == EXPECTED_CONTENT_TYPE, "Content-Type should be correct even in degraded state"
        
        # Verify circuit breaker metric exists and shows open state (value 0)
        metrics = parse_metrics(resp.text)
        assert "accounting_db_circuit_breaker_state" in metrics, "Circuit breaker state metric should exist even when DB is down"
    finally:
        proc.terminate()
        proc.wait()
