# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import time
import docker
import requests
import pytest

@pytest.fixture(scope="module")
def docker_client():
    return docker.from_env()

@pytest.fixture(scope="module")
def quote_service_container(docker_client):
    containers = docker_client.containers.list(filters={"name": "quoteservice"})
    assert len(containers) == 1, "quoteservice container not found"
    return containers[0]

@pytest.fixture(autouse=True, scope="module")
def wait_for_service_startup(quote_service_container):
    # Wait for start period + 3 retries to ensure initial health checks run
    time.sleep(30)

def test_ac1_health_status_healthy_when_running(quote_service_container):
    """AC-1: When quote service is running normally, docker inspect returns healthy within 30 seconds of startup"""
    health_status = quote_service_container.attrs["State"]["Health"]["Status"]
    assert health_status == "healthy", f"Expected health status 'healthy', got '{health_status}'"

def test_ac2_health_status_unhealthy_when_endpoint_fails(quote_service_container):
    """AC-2: When health endpoint fails, status transitions to unhealthy after 3 consecutive failures"""
    # First confirm service is healthy initially
    initial_health = quote_service_container.attrs["State"]["Health"]["Status"]
    assert initial_health == "healthy", "Service must be healthy before testing failure scenario"
    
    # Simulate health endpoint failure by stopping the service (or we could modify the endpoint, but stopping is simpler)
    exit_code, output = quote_service_container.exec_run("kill -STOP $(pidof php-fpm)")
    assert exit_code == 0, "Failed to suspend php-fpm process"
    
    try:
        # Wait for 3 failed checks: interval 5s * 3 retries + timeout 3s = ~18s wait
        time.sleep(20)
        quote_service_container.reload()
        health_status = quote_service_container.attrs["State"]["Health"]["Status"]
        assert health_status == "unhealthy", f"Expected health status 'unhealthy' after failures, got '{health_status}'"
    finally:
        # Resume the service
        quote_service_container.exec_run("kill -CONT $(pidof php-fpm)")

def test_ac3_container_runs_as_non_root_user(quote_service_container):
    """AC-3: Container process runs as non-root user (uid >= 1000, not 0)"""
    exit_code, output = quote_service_container.exec_run("id -u")
    assert exit_code == 0, "Failed to run id -u in container"
    uid = int(output.strip().decode("utf-8"))
    assert uid != 0, "Container is running as root user (uid 0)"
    assert uid >= 1000, f"Container running as uid {uid}, expected >=1000 for non-root system user"

def test_ac4_compose_has_all_required_healthcheck_parameters():
    """AC-4: Quote service entry in compose.yaml includes all required health check parameters"""
    import yaml
    with open("compose.yaml", "r") as f:
        compose_config = yaml.safe_load(f)
    
    quote_service = compose_config["services"]["quoteservice"]
    assert "healthcheck" in quote_service, "Healthcheck block missing from quoteservice config"
    
    healthcheck = quote_service["healthcheck"]
    required_params = ["test", "interval", "timeout", "retries", "start_period"]
    for param in required_params:
        assert param in healthcheck, f"Required healthcheck parameter '{param}' missing"
    
    # Verify test command matches spec
    expected_test = ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:8080/health"]
    assert healthcheck["test"] == expected_test, f"Healthcheck test command does not match spec: got {healthcheck['test']}"
    
    # Verify parameter values match spec
    assert healthcheck["interval"] == "5s", f"Expected interval 5s, got {healthcheck['interval']}"
    assert healthcheck["timeout"] == "3s", f"Expected timeout 3s, got {healthcheck['timeout']}"
    assert healthcheck["retries"] == 3, f"Expected retries 3, got {healthcheck['retries']}"
    assert healthcheck["start_period"] == "10s", f"Expected start_period 10s, got {healthcheck['start_period']}"

def test_ac5_dockerfile_has_non_root_configuration():
    """AC-5: Quote service Dockerfile includes non-root user creation, permission setup and USER directive"""
    with open("src/quote/Dockerfile", "r") as f:
        dockerfile_content = f.read()
    
    # Check for non-root user creation
    assert "addgroup" in dockerfile_content and "adduser" in dockerfile_content, "No non-root user creation found in Dockerfile"
    assert "appuser" in dockerfile_content, "Non-root user 'appuser' not created in Dockerfile"
    
    # Check for ownership setup
    assert "chown -R appuser:appgroup /var/www/html" in dockerfile_content, "File ownership setup missing for non-root user"
    
    # Check for USER directive switching to non-root
    assert "USER appuser" in dockerfile_content, "USER directive switching to non-root user missing"
    # Ensure USER directive is after installation/setup steps and before entrypoint
    lines = [line.strip() for line in dockerfile_content.splitlines() if line.strip() and not line.strip().startswith("#")]
    user_line_index = lines.index("USER appuser")
    entrypoint_lines = [i for i, line in enumerate(lines) if line.startswith("ENTRYPOINT") or line.startswith("CMD")]
    assert len(entrypoint_lines) > 0, "No ENTRYPOINT or CMD found in Dockerfile"
    assert user_line_index < entrypoint_lines[0], "USER directive should come before ENTRYPOINT/CMD"

def test_ac6_compose_has_all_required_security_context_parameters():
    """AC-6: Quote service entry in compose.yaml includes all required security context parameters"""
    import yaml
    with open("compose.yaml", "r") as f:
        compose_config = yaml.safe_load(f)
    
    quote_service = compose_config["services"]["quoteservice"]
    
    required_security_params = {
        "user": "appuser",
        "privileged": False,
        "allow_privilege_escalation": False,
        "no_new_privileges": True,
        "cap_drop": ["ALL"]
    }
    
    for param, expected_value in required_security_params.items():
        assert param in quote_service, f"Required security parameter '{param}' missing from quoteservice config"
        assert quote_service[param] == expected_value, f"Security parameter '{param}' has incorrect value: expected {expected_value}, got {quote_service[param]}"

def test_ac7_service_functions_normally_as_non_root():
    """AC-7: Quote service returns valid quotes on request to public endpoint while running as non-root"""
    # Try to get a quote from the service
    try:
        response = requests.get("http://localhost:8080/get-quote", timeout=5)
        assert response.status_code == 200, f"Expected 200 status from quote endpoint, got {response.status_code}"
        
        # Verify response is valid JSON with quote data
        quote_data = response.json()
        assert "quote" in quote_data, "Response missing 'quote' field"
        assert isinstance(quote_data["quote"], str), "Quote field is not a string"
        assert len(quote_data["quote"]) > 0, "Quote field is empty"
    except requests.exceptions.ConnectionError:
        pytest.fail("Could not connect to quote service endpoint")
