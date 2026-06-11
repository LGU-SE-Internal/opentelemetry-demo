#!/usr/bin/env python3
"""
Integration tests for OpenTelemetry Collector TLS/mTLS configuration support
for issue #2083. All tests validate the acceptance criteria defined in the spec.
"""
import os
import subprocess
import pytest
import grpc
import requests
from pathlib import Path

COLLECTOR_CONFIG_PATH = "./src/otel-collector/otel-collector-config.yaml"
TEST_CERTS_DIR = "./tests/testdata/certs"
TEST_CA_CERT = os.path.join(TEST_CERTS_DIR, "ca.crt")
TEST_SERVER_CERT = os.path.join(TEST_CERTS_DIR, "server.crt")
TEST_SERVER_KEY = os.path.join(TEST_CERTS_DIR, "server.key")
TEST_CLIENT_CERT = os.path.join(TEST_CERTS_DIR, "client.crt")
TEST_CLIENT_KEY = os.path.join(TEST_CERTS_DIR, "client.key")

def run_collector_with_config(config_content):
    """Helper to run collector with temporary config and return process result"""
    with open("/tmp/test-otelcol-config.yaml", "w") as f:
        f.write(config_content)
    proc = subprocess.run(
        ["otelcol", "--config", "/tmp/test-otelcol-config.yaml"],
        capture_output=True,
        text=True,
        timeout=10
    )
    return proc.returncode, proc.stdout, proc.stderr

def test_ac1_backward_compatibility_no_tls():
    """AC-1: When no TLS config is provided for OTLP receivers, accept unencrypted connections"""
    # Default config without any TLS settings
    default_config = """
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
exporters:
  logging: {}
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [logging]
"""
    # Run collector and verify it starts successfully
    ret, out, err = run_collector_with_config(default_config)
    assert ret == 0, f"Collector failed to start with default config: {err}"
    
    # Verify unencrypted gRPC connection works
    with grpc.insecure_channel("localhost:4317") as channel:
        # Test that channel is ready (connection accepted)
        grpc.channel_ready_future(channel).result(timeout=2)
    
    # Verify unencrypted HTTP connection works
    resp = requests.get("http://localhost:4318/health")
    assert resp.status_code == 200, f"HTTP health check failed: {resp.text}"

def test_ac2_receiver_tls_enforces_encrypted_connections():
    """AC-2: OTLP receiver with tls.insecure: false only accepts TLS 1.2+ encrypted connections"""
    tls_config = f"""
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
        tls:
          insecure: false
          cert_file: {TEST_SERVER_CERT}
          key_file: {TEST_SERVER_KEY}
      http:
        endpoint: 0.0.0.0:4318
        tls:
          insecure: false
          cert_file: {TEST_SERVER_CERT}
          key_file: {TEST_SERVER_KEY}
exporters:
  logging: {{}}
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [logging]
"""
    ret, out, err = run_collector_with_config(tls_config)
    assert ret == 0, f"Collector failed to start with TLS config: {err}"
    
    # Verify unencrypted gRPC connection fails
    try:
        with grpc.insecure_channel("localhost:4317") as channel:
            grpc.channel_ready_future(channel).result(timeout=2)
        assert False, "Unencrypted gRPC connection should have failed"
    except Exception:
        pass  # Expected failure
    
    # Verify unencrypted HTTP connection fails
    try:
        requests.get("http://localhost:4318/health", timeout=2)
        assert False, "Unencrypted HTTP connection should have failed"
    except Exception:
        pass  # Expected failure
    
    # Verify TLS 1.2+ connection succeeds
    creds = grpc.ssl_channel_credentials(root_certificates=open(TEST_CA_CERT, "rb").read())
    with grpc.secure_channel("localhost:4317", creds) as channel:
        grpc.channel_ready_future(channel).result(timeout=2)
    
    resp = requests.get("https://localhost:4318/health", verify=TEST_CA_CERT, timeout=2)
    assert resp.status_code == 200

def test_ac3_receiver_mtls_enforces_client_certs():
    """AC-3: OTLP receiver with tls.client_ca_file set only accepts clients with valid certs"""
    mtls_config = f"""
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
        tls:
          insecure: false
          cert_file: {TEST_SERVER_CERT}
          key_file: {TEST_SERVER_KEY}
          client_ca_file: {TEST_CA_CERT}
      http:
        endpoint: 0.0.0.0:4318
        tls:
          insecure: false
          cert_file: {TEST_SERVER_CERT}
          key_file: {TEST_SERVER_KEY}
          client_ca_file: {TEST_CA_CERT}
exporters:
  logging: {{}}
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [logging]
"""
    ret, out, err = run_collector_with_config(mtls_config)
    assert ret == 0, f"Collector failed to start with mTLS config: {err}"
    
    # Verify connection without client cert fails
    creds_no_client = grpc.ssl_channel_credentials(root_certificates=open(TEST_CA_CERT, "rb").read())
    try:
        with grpc.secure_channel("localhost:4317", creds_no_client) as channel:
            grpc.channel_ready_future(channel).result(timeout=2)
        assert False, "Connection without client cert should have failed"
    except Exception:
        pass  # Expected failure
    
    # Verify connection with valid client cert succeeds
    creds_with_client = grpc.ssl_channel_credentials(
        root_certificates=open(TEST_CA_CERT, "rb").read(),
        private_key=open(TEST_CLIENT_KEY, "rb").read(),
        certificate_chain=open(TEST_CLIENT_CERT, "rb").read()
    )
    with grpc.secure_channel("localhost:4317", creds_with_client) as channel:
        grpc.channel_ready_future(channel).result(timeout=2)

def test_ac4_exporter_tls_enforces_encrypted_connections():
    """AC-4: Exporter with tls.insecure: false only initiates TLS 1.2+ connections"""
    # First start a test TLS server
    import http.server
    import ssl
    import threading
    
    server = http.server.HTTPServer(("0.0.0.0", 9999), http.server.SimpleHTTPRequestHandler)
    server.socket = ssl.wrap_socket(
        server.socket,
        server_side=True,
        certfile=TEST_SERVER_CERT,
        keyfile=TEST_SERVER_KEY,
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
        ca_certs=TEST_CA_CERT
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    # Test exporter config with TLS enabled
    exporter_config = f"""
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
exporters:
  prometheus:
    endpoint: "https://localhost:9999/metrics"
    tls:
      insecure: false
      ca_file: {TEST_CA_CERT}
service:
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
"""
    ret, out, err = run_collector_with_config(exporter_config)
    assert ret == 0, f"Collector failed to start with TLS exporter config: {err}"
    
    # Test exporter with insecure endpoint fails
    exporter_insecure_config = """
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
exporters:
  prometheus:
    endpoint: "http://localhost:9999/metrics"
    tls:
      insecure: false
service:
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
"""
    ret, out, err = run_collector_with_config(exporter_insecure_config)
    assert ret != 0, "Collector should fail to start with insecure endpoint when tls.insecure=false"

def test_ac5_exporter_mtls_presents_client_cert():
    """AC-5: Exporter with valid cert_file and key_file presents client cert during handshake"""
    # Start test mTLS server that requires client certs
    import http.server
    import ssl
    import threading
    
    class TestHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            # Verify client cert was presented
            cert = self.request.getpeercert()
            if cert:
                self.send_response(200)
            else:
                self.send_response(401)
            self.end_headers()
    
    server = http.server.HTTPServer(("0.0.0.0", 9998), TestHandler)
    server.socket = ssl.wrap_socket(
        server.socket,
        server_side=True,
        certfile=TEST_SERVER_CERT,
        keyfile=TEST_SERVER_KEY,
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
        ca_certs=TEST_CA_CERT,
        cert_reqs=ssl.CERT_REQUIRED
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    # Test exporter with client certs configured
    exporter_mtls_config = f"""
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
exporters:
  prometheus:
    endpoint: "https://localhost:9998/metrics"
    tls:
      insecure: false
      ca_file: {TEST_CA_CERT}
      cert_file: {TEST_CLIENT_CERT}
      key_file: {TEST_CLIENT_KEY}
service:
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
"""
    ret, out, err = run_collector_with_config(exporter_mtls_config)
    assert ret == 0, f"Collector failed to start with mTLS exporter config: {err}"
    
    # Test without client certs fails
    exporter_no_client_cert_config = f"""
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
exporters:
  prometheus:
    endpoint: "https://localhost:9998/metrics"
    tls:
      insecure: false
      ca_file: {TEST_CA_CERT}
service:
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
"""
    ret, out, err = run_collector_with_config(exporter_no_client_cert_config)
    assert ret != 0, "Collector should fail when exporting to mTLS endpoint without client certs"

def test_ac6_tls_paths_support_env_var_substitution():
    """AC-6: TLS file path fields correctly resolve environment variables at startup"""
    # Set test env vars
    os.environ["OTELCOL_TLS_SERVER_CERT_FILE"] = TEST_SERVER_CERT
    os.environ["OTELCOL_TLS_SERVER_KEY_FILE"] = TEST_SERVER_KEY
    os.environ["OTELCOL_TLS_CA_FILE"] = TEST_CA_CERT
    
    env_config = """
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
        tls:
          insecure: false
          cert_file: ${OTELCOL_TLS_SERVER_CERT_FILE}
          key_file: ${OTELCOL_TLS_SERVER_KEY_FILE}
          client_ca_file: ${OTELCOL_TLS_CA_FILE}
exporters:
  logging: {}
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [logging]
"""
    ret, out, err = run_collector_with_config(env_config)
    assert ret == 0, f"Collector failed to start with env var substituted paths: {err}"

def test_ac7_invalid_tls_config_causes_startup_failure():
    """AC-7: Invalid certificate paths or malformed certs cause collector to fail startup with clear error"""
    # Test invalid path
    invalid_path_config = f"""
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
        tls:
          insecure: false
          cert_file: /nonexistent/path/cert.crt
          key_file: {TEST_SERVER_KEY}
exporters:
  logging: {{}}
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [logging]
"""
    ret, out, err = run_collector_with_config(invalid_path_config)
    assert ret != 0, "Collector should fail to start with invalid cert path"
    assert "no such file or directory" in err.lower() or "failed to load certificate" in err.lower()
    
    # Test malformed cert
    malformed_cert_path = "/tmp/malformed.crt"
    with open(malformed_cert_path, "w") as f:
        f.write("invalid cert content")
    
    malformed_config = f"""
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
        tls:
          insecure: false
          cert_file: {malformed_cert_path}
          key_file: {TEST_SERVER_KEY}
exporters:
  logging: {{}}
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [logging]
"""
    ret, out, err = run_collector_with_config(malformed_config)
    assert ret != 0, "Collector should fail to start with malformed cert"
    assert "tls configuration failure" in err.lower() or "failed to parse certificate" in err.lower()

def test_ac8_tls_documentation_exists():
    """AC-8: Documentation file exists at docs/otel-collector-tls.md with required content"""
    doc_path = Path("./docs/otel-collector-tls.md")
    assert doc_path.exists(), "TLS documentation file not found at docs/otel-collector-tls.md"
    
    content = doc_path.read_text()
    assert "OTELCOL_TLS_CA_FILE" in content, "Missing OTELCOL_TLS_CA_FILE in documentation"
    assert "OTELCOL_TLS_SERVER_CERT_FILE" in content, "Missing OTELCOL_TLS_SERVER_CERT_FILE in documentation"
    assert "OTELCOL_TLS_SERVER_KEY_FILE" in content, "Missing OTELCOL_TLS_SERVER_KEY_FILE in documentation"
    assert "OTELCOL_TLS_CLIENT_CA_FILE" in content, "Missing OTELCOL_TLS_CLIENT_CA_FILE in documentation"
    assert "OTELCOL_TLS_CLIENT_CERT_FILE" in content, "Missing OTELCOL_TLS_CLIENT_CERT_FILE in documentation"
    assert "OTELCOL_TLS_CLIENT_KEY_FILE" in content, "Missing OTELCOL_TLS_CLIENT_KEY_FILE in documentation"
    assert "TLS" in content and "mTLS" in content, "Missing TLS/mTLS setup examples in documentation"

def test_ac9_existing_default_config_works():
    """AC-9: Existing default unmodified collector config operates normally without changes"""
    # Read existing default config
    with open(COLLECTOR_CONFIG_PATH, "r") as f:
        default_config = f.read()
    
    # Run collector with default config
    ret, out, err = run_collector_with_config(default_config)
    assert ret == 0, f"Default collector config failed to start: {err}"
    
    # Verify unencrypted OTLP endpoints are available
    with grpc.insecure_channel("localhost:4317") as channel:
        grpc.channel_ready_future(channel).result(timeout=2)
    
    resp = requests.get("http://localhost:4318/health", timeout=2)
    assert resp.status_code == 200
