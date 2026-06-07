import os
import pytest
import yaml
import subprocess
import time
from typing import Dict, Any

DEFAULT_CONFIG_PATH = "otel-config.yml"

def get_collector_effective_config(env_vars: Dict[str, str] = None) -> Dict[str, Any]:
    """Get the effective collector config after environment variable substitution"""
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    
    # Run collector config validate to get effective config
    result = subprocess.run(
        ["otelcol", "--config", DEFAULT_CONFIG_PATH, "--config-yaml", "{}", "--validate"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Collector config validation failed: {result.stderr}"
    
    # Get effective config by parsing the output or using config export
    result = subprocess.run(
        ["otelcol", "--config", DEFAULT_CONFIG_PATH, "--config-yaml", "{}", "config", "export"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Failed to export collector config: {result.stderr}"
    
    return yaml.safe_load(result.stdout)

def test_ac1_default_values_work():
    """AC-1: Verify default values apply when no custom env vars are set"""
    config = get_collector_effective_config()
    
    # Check memory limiter defaults
    memory_limiter = config["processors"]["memory_limiter"]
    assert memory_limiter["limit_mib"] == 512, f"Expected default memory limit 512 MiB, got {memory_limiter['limit_mib']}"
    assert memory_limiter["spike_limit_mib"] == 256, f"Expected default spike limit 256 MiB, got {memory_limiter['spike_limit_mib']}"
    assert memory_limiter["check_interval"] == "1s", f"Expected default check interval 1s, got {memory_limiter['check_interval']}"
    
    # Check batch processor defaults
    batch_processor = config["processors"]["batch"]
    assert batch_processor["send_batch_size"] == 1000, f"Expected default batch queue size 1000, got {batch_processor['send_batch_size']}"
    
    # Check exporter queue defaults
    for exporter_name, exporter_config in config["exporters"].items():
        if "sending_queue" in exporter_config:
            assert exporter_config["sending_queue"]["queue_size"] == 1000, f"Exporter {exporter_name} expected queue size 1000, got {exporter_config['sending_queue']['queue_size']}"

def test_ac2_custom_memory_limit_works():
    """AC-2: Verify OTEL_COLLECTOR_MEMORY_LIMIT_MIB env var is correctly applied"""
    test_limit = 1024
    config = get_collector_effective_config({
        "OTEL_COLLECTOR_MEMORY_LIMIT_MIB": str(test_limit)
    })
    
    memory_limiter = config["processors"]["memory_limiter"]
    assert memory_limiter["limit_mib"] == test_limit, f"Expected memory limit {test_limit} MiB, got {memory_limiter['limit_mib']}"

def test_ac3_custom_batch_queue_size_works():
    """AC-3: Verify OTEL_COLLECTOR_BATCH_PROCESSOR_QUEUE_SIZE env var is correctly applied"""
    test_size = 5000
    config = get_collector_effective_config({
        "OTEL_COLLECTOR_BATCH_PROCESSOR_QUEUE_SIZE": str(test_size)
    })
    
    batch_processor = config["processors"]["batch"]
    assert batch_processor["send_batch_size"] == test_size, f"Expected batch queue size {test_size}, got {batch_processor['send_batch_size']}"

def test_ac4_custom_exporter_queue_size_works():
    """AC-4: Verify OTEL_COLLECTOR_EXPORTER_QUEUE_SIZE env var applies to all exporters"""
    test_size = 2000
    config = get_collector_effective_config({
        "OTEL_COLLECTOR_EXPORTER_QUEUE_SIZE": str(test_size)
    })
    
    for exporter_name, exporter_config in config["exporters"].items():
        if "sending_queue" in exporter_config:
            assert exporter_config["sending_queue"]["queue_size"] == test_size, f"Exporter {exporter_name} expected queue size {test_size}, got {exporter_config['sending_queue']['queue_size']}"

def test_ac5_config_has_documentation_comments():
    """AC-5: Verify config file has inline documentation for each parameter"""
    with open(DEFAULT_CONFIG_PATH, "r") as f:
        config_content = f.read()
    
    # Check for documentation of each parameter
    assert "OTEL_COLLECTOR_MEMORY_LIMIT_MIB" in config_content, "Missing documentation for OTEL_COLLECTOR_MEMORY_LIMIT_MIB"
    assert "OTEL_COLLECTOR_MEMORY_SPIKE_LIMIT_MIB" in config_content, "Missing documentation for OTEL_COLLECTOR_MEMORY_SPIKE_LIMIT_MIB"
    assert "OTEL_COLLECTOR_MEMORY_CHECK_INTERVAL" in config_content, "Missing documentation for OTEL_COLLECTOR_MEMORY_CHECK_INTERVAL"
    assert "OTEL_COLLECTOR_BATCH_PROCESSOR_QUEUE_SIZE" in config_content, "Missing documentation for OTEL_COLLECTOR_BATCH_PROCESSOR_QUEUE_SIZE"
    assert "OTEL_COLLECTOR_EXPORTER_QUEUE_SIZE" in config_content, "Missing documentation for OTEL_COLLECTOR_EXPORTER_QUEUE_SIZE"
    
    # Check for production tuning guidance
    assert "80% of allocated container memory" in config_content, "Missing production memory limit guidance"
    assert "throughput" in config_content.lower(), "Missing production queue size guidance"

def test_ac6_memory_limiter_rejects_traffic_on_high_load():
    """AC-6: Verify collector rejects traffic instead of crashing when memory limit is exceeded"""
    # Start collector with low memory limit
    env = os.environ.copy()
    env.update({
        "OTEL_COLLECTOR_MEMORY_LIMIT_MIB": "128",
        "OTEL_COLLECTOR_MEMORY_SPIKE_LIMIT_MIB": "32"
    })
    
    collector_proc = subprocess.Popen(
        ["otelcol", "--config", DEFAULT_CONFIG_PATH],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    try:
        # Wait for collector to start
        time.sleep(10)
        assert collector_proc.poll() is None, "Collector crashed immediately on startup"
        
        # Generate high load of telemetry data (10k spans per second)
        load_proc = subprocess.Popen(
            ["telemetrygen", "traces", "--rate", "10000", "--duration", "30s", "--otlp-endpoint", "localhost:4317", "--insecure"],
            capture_output=True,
            text=True
        )
        load_proc.wait(timeout=45)
        
        # Check collector is still running (no OOM kill)
        assert collector_proc.poll() is None, "Collector crashed under high load, expected memory limiter to reject traffic"
        
        # Check for memory limiter rejection metrics
        metrics_result = subprocess.run(
            ["curl", "-s", "http://localhost:8888/metrics"],
            capture_output=True,
            text=True
        )
        assert metrics_result.returncode == 0, "Failed to fetch collector metrics"
        assert "otelcol_processor_memory_limiter_refused_spans" in metrics_result.stdout, "No memory limiter rejection metrics found, expected traffic to be rejected"
    finally:
        # Cleanup processes
        if collector_proc.poll() is None:
            collector_proc.terminate()
            collector_proc.wait(timeout=10)
