#!/usr/bin/env python3
"""Tests for OpenTelemetry Collector CORS and Redis receiver configuration (issue #1569)"""
import os
import yaml
import pytest
import requests
from kubernetes import client, config

# Constants from spec
OTEL_REDIS_RECEIVER_ENDPOINT_VAR = "OTEL_REDIS_RECEIVER_ENDPOINT"
OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR = "OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS"
DEFAULT_REDIS_ENDPOINT = "redis:6379"
DEFAULT_CORS_ALLOWED_ORIGINS = "*"
COLLECTOR_OTLP_HTTP_PORT = 4318
COLLECTOR_CONFIG_PATH = "otel-collector/otel-collector-config.yaml"
K8S_COLLECTOR_DEPLOYMENT_PATH = "k8s/monitoring/otel-collector-deployment.yaml"
DOCUMENTATION_PATH = "otel-collector/README.md"

@pytest.mark.ac1
def test_ac1_redis_endpoint_default_value():
    """AC-1: When OTEL_REDIS_RECEIVER_ENDPOINT is not set, the Redis receiver uses the original default endpoint value."""
    assert os.path.exists(COLLECTOR_CONFIG_PATH), f"Missing collector config file: {COLLECTOR_CONFIG_PATH}"
    with open(COLLECTOR_CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    
    # Check Redis receiver configuration uses environment variable with correct default
    assert "receivers" in config, "Missing receivers section in collector config"
    assert "redis" in config["receivers"], "Missing redis receiver in config"
    redis_config = config["receivers"]["redis"]
    
    # Check that endpoint uses substitution with correct default
    assert "endpoint" in redis_config, "Missing endpoint field in redis receiver config"
    endpoint_config = redis_config["endpoint"]
    expected_substitution = f"${{{OTEL_REDIS_RECEIVER_ENDPOINT_VAR}:{DEFAULT_REDIS_ENDPOINT}}}"
    assert endpoint_config == expected_substitution, f"Redis endpoint should use {expected_substitution}, got {endpoint_config}"

@pytest.mark.ac2
def test_ac2_redis_endpoint_overridden_by_env():
    """AC-2: When OTEL_REDIS_RECEIVER_ENDPOINT is set to a valid endpoint string, the Redis receiver uses the provided value instead of the default."""
    # Test that the environment variable is respected by collector substitution
    test_endpoint = "custom-redis:6380"
    os.environ[OTEL_REDIS_RECEIVER_ENDPOINT_VAR] = test_endpoint
    
    assert os.path.exists(COLLECTOR_CONFIG_PATH), f"Missing collector config file: {COLLECTOR_CONFIG_PATH}"
    with open(COLLECTOR_CONFIG_PATH, "r") as f:
        raw_config = f.read()
    
    # Substitute environment variables in config (simulate collector behavior)
    import re
    def env_substitute(match):
        var_name = match.group(1)
        default = match.group(2) if len(match.groups()) > 2 else None
        return os.getenv(var_name, default or "")
    
    substituted_config = re.sub(r"\$\{([A-Z_]+)(?::([^}]+))?\}", env_substitute, raw_config)
    loaded_config = yaml.safe_load(substituted_config)
    
    assert loaded_config["receivers"]["redis"]["endpoint"] == test_endpoint, f"Redis endpoint should be {test_endpoint} when env var is set"
    
    # Clean up
    if OTEL_REDIS_RECEIVER_ENDPOINT_VAR in os.environ:
        del os.environ[OTEL_REDIS_RECEIVER_ENDPOINT_VAR]

@pytest.mark.ac3
def test_ac3_cors_origins_default_value():
    """AC-3: When OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS is not set, the OTLP HTTP receiver allows CORS requests from all origins (*)."""
    assert os.path.exists(COLLECTOR_CONFIG_PATH), f"Missing collector config file: {COLLECTOR_CONFIG_PATH}"
    with open(COLLECTOR_CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    
    assert "receivers" in config, "Missing receivers section in collector config"
    assert "otlp" in config["receivers"], "Missing otlp receiver in config"
    assert "http" in config["receivers"]["otlp"], "Missing http section in otlp receiver config"
    otlp_http_config = config["receivers"]["otlp"]["http"]
    
    assert "cors" in otlp_http_config, "Missing cors section in otlp http receiver config"
    assert "allowed_origins" in otlp_http_config["cors"], "Missing allowed_origins in cors config"
    
    allowed_origins_config = otlp_http_config["cors"]["allowed_origins"]
    expected_substitution = f"${{{OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR}:{DEFAULT_CORS_ALLOWED_ORIGINS}}}"
    assert allowed_origins_config == expected_substitution, f"CORS allowed origins should use {expected_substitution}, got {allowed_origins_config}"

@pytest.mark.ac4
def test_ac4_cors_origins_overridden_by_env():
    """AC-4: When OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS is set to a comma-separated list of origins, the OTLP HTTP receiver only allows CORS requests from the explicitly listed origins."""
    test_origins = "https://example.com,https://app.example.com,http://localhost:3000"
    os.environ[OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR] = test_origins
    
    assert os.path.exists(COLLECTOR_CONFIG_PATH), f"Missing collector config file: {COLLECTOR_CONFIG_PATH}"
    with open(COLLECTOR_CONFIG_PATH, "r") as f:
        raw_config = f.read()
    
    # Substitute environment variables
    import re
    def env_substitute(match):
        var_name = match.group(1)
        default = match.group(2) if len(match.groups()) > 2 else None
        return os.getenv(var_name, default or "")
    
    substituted_config = re.sub(r"\$\{([A-Z_]+)(?::([^}]+))?\}", env_substitute, raw_config)
    loaded_config = yaml.safe_load(substituted_config)
    
    allowed_origins = loaded_config["receivers"]["otlp"]["http"]["cors"]["allowed_origins"]
    assert allowed_origins == test_origins.split(","), f"CORS allowed origins should be {test_origins.split(',')} when env var is set"
    
    # Clean up
    if OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR in os.environ:
        del os.environ[OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR]

@pytest.mark.ac5
def test_ac5_k8s_deployment_env_vars():
    """AC-5: The otel-collector Kubernetes deployment manifest includes both new environment variables, with support for runtime value overrides during deployment."""
    assert os.path.exists(K8S_COLLECTOR_DEPLOYMENT_PATH), f"Missing Kubernetes deployment file: {K8S_COLLECTOR_DEPLOYMENT_PATH}"
    with open(K8S_COLLECTOR_DEPLOYMENT_PATH, "r") as f:
        deploy = yaml.safe_load(f)
    
    # Find collector container spec
    containers = deploy["spec"]["template"]["spec"]["containers"]
    collector_container = next(c for c in containers if "otelcol" in c["name"].lower() or "collector" in c["name"].lower())
    
    assert "env" in collector_container, "Missing env section in collector container spec"
    env_vars = {e["name"]: e for e in collector_container["env"]}
    
    # Check Redis endpoint env var exists
    assert OTEL_REDIS_RECEIVER_ENDPOINT_VAR in env_vars, f"Missing {OTEL_REDIS_RECEIVER_ENDPOINT_VAR} in deployment env vars"
    assert "valueFrom" in env_vars[OTEL_REDIS_RECEIVER_ENDPOINT_VAR] or "value" in env_vars[OTEL_REDIS_RECEIVER_ENDPOINT_VAR], f"{OTEL_REDIS_RECEIVER_ENDPOINT_VAR} must support runtime overrides"
    
    # Check CORS origins env var exists
    assert OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR in env_vars, f"Missing {OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR} in deployment env vars"
    assert "valueFrom" in env_vars[OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR] or "value" in env_vars[OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR], f"{OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR} must support runtime overrides"

@pytest.mark.ac6
def test_ac6_documentation_exists():
    """AC-6: Documentation in the otel-collector directory describes both environment variables, including their purpose, default values, and example configurations."""
    assert os.path.exists(DOCUMENTATION_PATH), f"Missing documentation file: {DOCUMENTATION_PATH}"
    with open(DOCUMENTATION_PATH, "r") as f:
        doc_content = f.read()
    
    # Check Redis endpoint variable is documented
    assert OTEL_REDIS_RECEIVER_ENDPOINT_VAR in doc_content, f"Missing {OTEL_REDIS_RECEIVER_ENDPOINT_VAR} in documentation"
    assert "Redis" in doc_content, "Missing Redis receiver purpose description"
    assert DEFAULT_REDIS_ENDPOINT in doc_content, f"Missing default value {DEFAULT_REDIS_ENDPOINT} for Redis endpoint in docs"
    
    # Check CORS origins variable is documented
    assert OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR in doc_content, f"Missing {OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR} in documentation"
    assert "CORS" in doc_content or "Cross-Origin" in doc_content, "Missing CORS purpose description"
    assert DEFAULT_CORS_ALLOWED_ORIGINS in doc_content, f"Missing default value {DEFAULT_CORS_ALLOWED_ORIGINS} for CORS origins in docs"
    assert "comma-separated" in doc_content, "Missing note that CORS origins are comma-separated in docs"

@pytest.mark.ac7
def test_ac7_backward_compatibility_no_env_vars():
    """AC-7: All existing otel-collector functionality remains unchanged when the new environment variables are not explicitly set (full backward compatibility maintained)."""
    # Ensure no env vars are set
    for var in [OTEL_REDIS_RECEIVER_ENDPOINT_VAR, OTEL_OTLP_HTTP_CORS_ALLOWED_ORIGINS_VAR]:
        if var in os.environ:
            del os.environ[var]
    
    # Load and substitute config
    assert os.path.exists(COLLECTOR_CONFIG_PATH), f"Missing collector config file: {COLLECTOR_CONFIG_PATH}"
    with open(COLLECTOR_CONFIG_PATH, "r") as f:
        raw_config = f.read()
    
    import re
    def env_substitute(match):
        var_name = match.group(1)
        default = match.group(2) if len(match.groups()) > 2 else None
        return os.getenv(var_name, default or "")
    
    substituted_config = re.sub(r"\$\{([A-Z_]+)(?::([^}]+))?\}", env_substitute, raw_config)
    loaded_config = yaml.safe_load(substituted_config)
    
    # Verify default values are active
    assert loaded_config["receivers"]["redis"]["endpoint"] == DEFAULT_REDIS_ENDPOINT, f"Redis endpoint should default to {DEFAULT_REDIS_ENDPOINT} when env var is not set"
    assert loaded_config["receivers"]["otlp"]["http"]["cors"]["allowed_origins"] == DEFAULT_CORS_ALLOWED_ORIGINS, f"CORS allowed origins should default to {DEFAULT_CORS_ALLOWED_ORIGINS} when env var is not set"
