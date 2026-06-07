#!/usr/bin/env python3
import subprocess
import time
import pytest

@pytest.fixture(scope="module")
def compose_down():
    yield
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        capture_output=True,
        text=True
    )

def test_ac1_otel_collector_transitions_to_healthy_within_30s(compose_down):
    """AC-1: Verify otel-collector service becomes healthy within 30 seconds"""
    # Start the collector service
    subprocess.run(
        ["docker", "compose", "up", "-d", "otel-collector"],
        check=True,
        capture_output=True,
        text=True
    )
    
    # Check health status for up to 30 seconds
    start_time = time.time()
    healthy = False
    while time.time() - start_time < 30:
        result = subprocess.run(
            ["docker", "compose", "ps", "otel-collector", "--format", "{{.Status}}"],
            capture_output=True,
            text=True
        )
        if "(healthy)" in result.stdout:
            healthy = True
            break
        time.sleep(2)
    
    assert healthy, "otel-collector did not transition to healthy state within 30 seconds"

def test_ac2_otel_collector_runs_as_non_root_uid_10001(compose_down):
    """AC-2: Verify otel-collector runs as UID 10001 (non-root)"""
    # Ensure collector is running
    subprocess.run(
        ["docker", "compose", "up", "-d", "otel-collector"],
        check=True,
        capture_output=True,
        text=True
    )
    
    # Wait for service to be running
    time.sleep(5)
    
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "otel-collector", "id", "-u"],
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0, f"Failed to get UID: {result.stderr}"
    assert result.stdout.strip() == "10001", f"Expected UID 10001, got {result.stdout.strip()}"

def test_ac3_otel_collector_has_correct_resource_requests():
    """AC-3: Verify CPU and memory resource requests are configured correctly"""
    result = subprocess.run(
        ["docker", "compose", "config"],
        capture_output=True,
        text=True,
        check=True
    )
    
    config_output = result.stdout
    assert "cpu: 250m" in config_output, "CPU request 250m not found in configuration"
    assert "memory: 256Mi" in config_output, "Memory request 256Mi not found in configuration"

def test_ac4_all_dependencies_use_service_healthy_condition():
    """AC-4: Verify all otel-collector dependencies use service_healthy condition"""
    result = subprocess.run(
        ["docker", "compose", "config"],
        capture_output=True,
        text=True,
        check=True
    )
    
    config_output = result.stdout
    
    # Find all otel-collector depends_on sections
    lines = config_output.split('\n')
    in_depends_on = False
    found_otel_dep = False
    
    for i, line in enumerate(lines):
        if "depends_on:" in line:
            in_depends_on = True
            continue
        if in_depends_on and line.strip() == "otel-collector:" and len(line) - len(line.lstrip()) == 4:
            found_otel_dep = True
            # Check next lines for condition
            for j in range(i+1, min(i+5, len(lines))):
                if "condition:" in lines[j]:
                    assert "service_healthy" in lines[j], f"Found service_started condition for otel-collector dependency in line: {lines[j]}"
                    break
        if in_depends_on and len(line.strip()) > 0 and len(line) - len(line.lstrip()) <= 2:
            in_depends_on = False
    
    assert found_otel_dep, "No dependencies on otel-collector found"
    assert "service_started" not in config_output or "otel-collector" not in config_output.split("service_started")[0], "Found service_started condition for otel-collector dependency"

def test_ac5_dependent_services_wait_for_healthy_collector(compose_down):
    """AC-5: Verify dependent services only start after otel-collector is healthy"""
    # First stop all services
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        capture_output=True,
        text=True
    )
    
    # Start frontend service (which depends on otel-collector)
    proc = subprocess.Popen(
        ["docker", "compose", "up", "frontend"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Wait 10 seconds and check if frontend is running yet
    time.sleep(10)
    ps_result = subprocess.run(
        ["docker", "compose", "ps", "frontend", "--format", "{{.Status}}"],
        capture_output=True,
        text=True
    )
    
    # Frontend should not be running yet because collector is not healthy
    assert "Up" not in ps_result.stdout, "Frontend started before otel-collector became healthy"
    
    # Cleanup
    proc.terminate()
    proc.wait()
