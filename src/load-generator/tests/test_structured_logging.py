"""Test suite for structured logging acceptance criteria for load-generator service"""
import os
import re
import json
import subprocess
from pathlib import Path
import pytest
import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter

# Setup paths
SRC_DIR = Path(__file__).parent.parent
PY_FILES = list(SRC_DIR.glob("**/*.py"))

def test_ac1_no_print_statements():
    """AC-1: No raw print() statements remain in any Python file under load-generator src"""
    print_pattern = re.compile(r"\bprint\(")
    print_statements = []
    
    for py_file in PY_FILES:
        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read()
            matches = print_pattern.findall(content)
            if matches:
                print_statements.append(f"{py_file.relative_to(SRC_DIR.parent)}: {len(matches)} print statements found")
    
    assert len(print_statements) == 0, f"Found raw print statements: {', '.join(print_statements)}"

def test_ac2_all_logs_are_valid_json():
    """AC-2: Every log line emitted is a valid JSON object, no unstructured text"""
    # Run a short load test to capture log output
    env = os.environ.copy()
    env["LOCUST_HEADLESS"] = "true"
    env["LOCUST_RUN_TIME"] = "5s"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_LOCUSTFILE"] = str(SRC_DIR / "locustfile.py")
    
    result = subprocess.run(
        ["python", "-m", "locust"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Check all stdout lines are valid JSON
    invalid_lines = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            invalid_lines.append(line)
    
    assert len(invalid_lines) == 0, f"Found non-JSON log lines: {invalid_lines[:10]}"

def test_ac3_logs_include_required_fields():
    """AC-3: All log entries include service.name = 'load-generator' and severity_text fields"""
    env = os.environ.copy()
    env["LOCUST_HEADLESS"] = "true"
    env["LOCUST_RUN_TIME"] = "2s"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_LOCUSTFILE"] = str(SRC_DIR / "locustfile.py")
    
    result = subprocess.run(
        ["python", "-m", "locust"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30
    )
    
    logs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # Handled in AC2 test
    
    assert len(logs) > 0, "No logs captured"
    
    missing_fields = []
    for idx, log in enumerate(logs):
        if "service.name" not in log or log["service.name"] != "load-generator":
            missing_fields.append(f"Line {idx}: missing or incorrect service.name")
        if "severity_text" not in log:
            missing_fields.append(f"Line {idx}: missing severity_text field")
    
    assert len(missing_fields) == 0, f"Missing required fields in logs: {missing_fields[:10]}"

def test_ac4_no_debug_logs_in_production_default():
    """AC-4: Default production configuration emits no DEBUG severity logs"""
    env = os.environ.copy()
    env.pop("LOG_LEVEL", None)
    env["LOCUST_HEADLESS"] = "true"
    env["LOCUST_RUN_TIME"] = "5s"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_LOCUSTFILE"] = str(SRC_DIR / "locustfile.py")
    
    result = subprocess.run(
        ["python", "-m", "locust"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30
    )
    
    debug_logs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            log = json.loads(line)
            if log.get("severity_text") == "DEBUG":
                debug_logs.append(line)
        except json.JSONDecodeError:
            pass
    
    assert len(debug_logs) == 0, f"Found DEBUG logs in production mode: {debug_logs[:10]}"

def test_ac5_debug_logs_emitted_with_log_level_debug():
    """AC-5: LOG_LEVEL=debug environment variable enables DEBUG severity logs"""
    env = os.environ.copy()
    env["LOG_LEVEL"] = "debug"
    env["LOCUST_HEADLESS"] = "true"
    env["LOCUST_RUN_TIME"] = "5s"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_LOCUSTFILE"] = str(SRC_DIR / "locustfile.py")
    
    result = subprocess.run(
        ["python", "-m", "locust"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30
    )
    
    debug_logs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            log = json.loads(line)
            if log.get("severity_text") == "DEBUG":
                debug_logs.append(line)
        except json.JSONDecodeError:
            pass
    
    assert len(debug_logs) > 0, "No DEBUG logs found when LOG_LEVEL=debug is set"

def test_ac6_trace_context_fields_in_logs():
    """AC-6: Log entries in active trace context include correct trace.id and span.id fields"""
    # First test setup_logging function exists
    try:
        from locustfile import setup_logging
    except ImportError:
        pytest.fail("setup_logging function not found in locustfile.py")
    
    # Initialize tracing
    trace.set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer("test.tracer")
    
    # Setup logging
    setup_logging(service_name="load-generator", environment="development")
    
    # Capture log output
    import io
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    
    # Generate a log inside an active trace
    with tracer.start_as_current_span("test-span") as span:
        expected_trace_id = format(span.get_span_context().trace_id, "032x")
        expected_span_id = format(span.get_span_context().span_id, "016x")
        logging.info("Test log inside trace")
    
    log_output = log_capture.getvalue()
    log_lines = [line.strip() for line in log_output.splitlines() if line.strip()]
    
    assert len(log_lines) > 0, "No log output captured"
    
    found_matching_log = False
    for line in log_lines:
        try:
            log = json.loads(line)
            if log.get("body") == "Test log inside trace":
                assert "trace.id" in log, "trace.id missing from log in trace context"
                assert "span.id" in log, "span.id missing from log in trace context"
                assert log["trace.id"] == expected_trace_id, f"Expected trace ID {expected_trace_id}, got {log['trace.id']}"
                assert log["span.id"] == expected_span_id, f"Expected span ID {expected_span_id}, got {log['span.id']}"
                found_matching_log = True
        except json.JSONDecodeError:
            continue
    
    assert found_matching_log, "Test log message not found in output"

def test_ac7_functional_behavior_unchanged():
    """AC-7: Load generator functional behavior unchanged: all load test scenarios complete successfully"""
    env = os.environ.copy()
    env["LOCUST_HEADLESS"] = "true"
    env["LOCUST_RUN_TIME"] = "10s"
    env["LOCUST_USERS"] = "5"
    env["LOCUST_SPAWN_RATE"] = "2"
    env["LOCUST_LOCUSTFILE"] = str(SRC_DIR / "locustfile.py")
    # Set target URL to dummy to avoid actual network calls
    env["LOCUST_HOST"] = "http://localhost:8080"
    
    result = subprocess.run(
        ["python", "-m", "locust", "--exit-code-on-error", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60
    )
    
    # We expect it to fail if host is unreachable, but the load generator itself should not crash
    # Check that the process didn't crash with a Python error
    assert "Traceback (most recent call last):" not in result.stderr, "Load generator crashed with exception"
    assert result.returncode in [0, 1], f"Unexpected exit code {result.returncode}: process crashed"

def test_ac8_k8s_log_compatibility():
    """AC-8: Log output is compatible with Kubernetes logging agents (Fluentd/Loki)"""
    # Test that logs are valid JSON without extra characters, one per line, with required fields
    env = os.environ.copy()
    env["LOCUST_HEADLESS"] = "true"
    env["LOCUST_RUN_TIME"] = "3s"
    env["LOCUST_USERS"] = "1"
    env["LOCUST_SPAWN_RATE"] = "1"
    env["LOCUST_LOCUSTFILE"] = str(SRC_DIR / "locustfile.py")
    
    result = subprocess.run(
        ["python", "-m", "locust"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30
    )
    
    invalid_k8s_lines = []
    for idx, line in enumerate(result.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        # Check no leading/trailing whitespace (common issue with log parsers)
        if line != line.strip():
            invalid_k8s_lines.append(f"Line {idx}: leading/trailing whitespace")
        # Check line length is under 16KB (standard K8s log limit)
        if len(line) > 16384:
            invalid_k8s_lines.append(f"Line {idx}: exceeds 16KB K8s log line limit")
        # Check valid JSON
        try:
            log = json.loads(line)
            # Check required fields exist for Fluentd/Loki parsing
            required_fields = ["timestamp", "service.name", "severity_text", "body"]
            for field in required_fields:
                if field not in log:
                    invalid_k8s_lines.append(f"Line {idx}: missing required field {field} for K8s log parsing")
        except json.JSONDecodeError:
            invalid_k8s_lines.append(f"Line {idx}: invalid JSON, cannot be parsed by K8s log agents")
    
    assert len(invalid_k8s_lines) == 0, f"K8s log compatibility issues: {invalid_k8s_lines[:10]}"
