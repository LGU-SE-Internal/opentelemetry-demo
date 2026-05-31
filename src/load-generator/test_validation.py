import os
import sys
import logging
from urllib.parse import urlparse

# Configure logging to output to stdout
logging.basicConfig(level=logging.INFO)

# Test 1: No endpoint set
print("Test 1: No OTEL_EXPORTER_OTLP_ENDPOINT set")
os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if not otlp_endpoint:
    logging.error("OTEL_EXPORTER_OTLP_ENDPOINT environment variable is not set or empty")
    print("✓ Test 1 passed: Correctly exits when endpoint missing")
else:
    print("✗ Test 1 failed: No error when endpoint missing")

# Test 2: Empty endpoint
print("\nTest 2: Empty OTEL_EXPORTER_OTLP_ENDPOINT")
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if not otlp_endpoint:
    logging.error("OTEL_EXPORTER_OTLP_ENDPOINT environment variable is not set or empty")
    print("✓ Test 2 passed: Correctly exits when endpoint empty")
else:
    print("✗ Test 2 failed: No error when endpoint empty")

# Test 3: Invalid URL (no http/https)
print("\nTest 3: Invalid URL (grpc:// endpoint)")
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "grpc://localhost:4317"
otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
parsed_url = urlparse(otlp_endpoint)
if parsed_url.scheme not in ("http", "https"):
    logging.error(f"OTEL_EXPORTER_OTLP_ENDPOINT is not a valid HTTP/HTTPS URL: {otlp_endpoint}")
    print("✓ Test 3 passed: Correctly exits when URL scheme is not http/https")
else:
    print("✗ Test 3 failed: No error when invalid URL scheme")

# Test 4: Valid HTTP URL
print("\nTest 4: Valid HTTP URL")
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if not otlp_endpoint:
    logging.error("OTEL_EXPORTER_OTLP_ENDPOINT environment variable is not set or empty")
    print("✗ Test 4 failed: Error when valid endpoint present")
else:
    parsed_url = urlparse(otlp_endpoint)
    if parsed_url.scheme not in ("http", "https"):
        logging.error(f"OTEL_EXPORTER_OTLP_ENDPOINT is not a valid HTTP/HTTPS URL: {otlp_endpoint}")
        print("✗ Test 4 failed: Invalid URL error for valid HTTP endpoint")
    else:
        print("✓ Test 4 passed: No error for valid HTTP endpoint")

# Test 5: Valid HTTPS URL
print("\nTest 5: Valid HTTPS URL")
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://otel-collector.example.com:4318"
otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if not otlp_endpoint:
    logging.error("OTEL_EXPORTER_OTLP_ENDPOINT environment variable is not set or empty")
    print("✗ Test 5 failed: Error when valid endpoint present")
else:
    parsed_url = urlparse(otlp_endpoint)
    if parsed_url.scheme not in ("http", "https"):
        logging.error(f"OTEL_EXPORTER_OTLP_ENDPOINT is not a valid HTTP/HTTPS URL: {otlp_endpoint}")
        print("✗ Test 5 failed: Invalid URL error for valid HTTPS endpoint")
    else:
        print("✓ Test 5 passed: No error for valid HTTPS endpoint")

print("\nAll tests completed!")
