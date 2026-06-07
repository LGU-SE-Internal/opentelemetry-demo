#!/usr/bin/env python3
import subprocess
import time
import pytest
import json

FLAGD_CONTAINER_NAME = "flagd"
DOCKER_COMPOSE_CMD = ["docker", "compose"]
DOCKER_COMPOSE_V1_CMD = ["docker-compose"]
FLAGD_DEPENDENT_SERVICES = ["productcatalogservice", "paymentservice", "cartservice", "recommendationservice"]


def run_cmd(cmd, shell=False, check=True):
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=check)


def get_flagd_health_status():
    cmd = ["docker", "inspect", "--format='{{.State.Health.Status}}'", FLAGD_CONTAINER_NAME]
    result = run_cmd(cmd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip().strip("'")


@pytest.mark.ac1
def test_ac1_flagd_transitions_to_healthy_within_15s():
    """AC-1: flagd transitions from starting to healthy within 15 seconds of container creation"""
    # Cleanup any existing flagd container
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", "flagd"], check=False)
    # Start flagd detached
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "flagd"])
    try:
        start_time = time.time()
        status = get_flagd_health_status()
        while status != "healthy" and (time.time() - start_time) < 15:
            time.sleep(0.5)
            status = get_flagd_health_status()
        assert status == "healthy", f"flagd did not become healthy within 15s, last status: {status}"
    finally:
        run_cmd(DOCKER_COMPOSE_CMD + ["stop", "flagd"], check=False)


@pytest.mark.ac2
def test_ac2_flagd_marked_unhealthy_after_5_consecutive_failures():
    """AC-2: flagd is marked unhealthy after 5 consecutive failed health checks"""
    # First start flagd and wait for it to be healthy
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", "flagd"], check=False)
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "flagd"])
    try:
        # Wait for healthy first
        start_time = time.time()
        while get_flagd_health_status() != "healthy" and (time.time() - start_time) < 15:
            time.sleep(0.5)
        # Simulate health endpoint failure by dropping traffic to port 8013 inside container
        run_cmd(["docker", "exec", FLAGD_CONTAINER_NAME, "iptables", "-A", "INPUT", "-p", "tcp", "--dport", "8013", "-j", "DROP"])
        # Wait for 5 consecutive failures (each check runs every 2s, so wait ~12s)
        time.sleep(12)
        status = get_flagd_health_status()
        assert status == "unhealthy", f"flagd was not marked unhealthy after 5 failed checks, status: {status}"
    finally:
        run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", "flagd"], check=False)


@pytest.mark.ac3
def test_ac3_all_dependent_services_use_service_healthy_condition():
    """AC-3: All services with depends_on for flagd use service_healthy condition instead of service_started"""
    # Read compose.yaml
    with open("./compose.yaml", "r") as f:
        compose_content = f.read()
    
    import yaml
    compose_dict = yaml.safe_load(compose_content)
    for service_name, service_config in compose_dict["services"].items():
        if "depends_on" in service_config and "flagd" in service_config["depends_on"]:
            depends_on_flagd = service_config["depends_on"]["flagd"]
            assert isinstance(depends_on_flagd, dict), f"Service {service_name} flagd depends_on is not a dict"
            assert depends_on_flagd.get("condition") == "service_healthy", f"Service {service_name} flagd condition is not service_healthy"
            assert depends_on_flagd.get("condition") != "service_started", f"Service {service_name} uses legacy service_started condition for flagd"


@pytest.mark.ac4
def test_ac4_dependent_services_wait_for_flagd_healthy_before_start():
    """AC-4: Dependent services do not start until flagd is marked healthy"""
    # Cleanup all containers
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv"], check=False)
    # Start all services in detached mode
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d"] + FLAGD_DEPENDENT_SERVICES + ["flagd"])
    try:
        # Check that dependent services are in created/waiting state while flagd is starting
        for _ in range(10):
            flagd_status = get_flagd_health_status()
            if flagd_status == "healthy":
                break
            for service in FLAGD_DEPENDENT_SERVICES:
                service_state = run_cmd(["docker", "inspect", "--format='{{.State.Status}}'", service], check=False).stdout.strip().strip("'")
                assert service_state in ["created", "waiting"], f"Service {service} started before flagd was healthy, state: {service_state}"
            time.sleep(0.5)
    finally:
        run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv"], check=False)


@pytest.mark.ac5
def test_ac5_no_flagd_connection_errors_in_dependent_service_logs():
    """AC-5: No dependent service logs have flagd connection errors during startup"""
    # Cleanup all containers
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv"], check=False)
    # Start all services
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d"])
    try:
        # Wait for all services to be running for 10s
        time.sleep(10)
        # Check logs for each dependent service
        error_patterns = ["flagd connection refused", "feature flag resolution failed", "could not connect to flagd", "flagd unavailable"]
        for service in FLAGD_DEPENDENT_SERVICES:
            logs = run_cmd(DOCKER_COMPOSE_CMD + ["logs", service]).stdout.lower()
            for pattern in error_patterns:
                assert pattern not in logs, f"Service {service} logs contain flagd connection error: {pattern}"
    finally:
        run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv"], check=False)


@pytest.mark.ac6
def test_ac6_compatibility_with_docker_compose_v1_and_v2():
    """AC-6: Configuration works with both docker compose v2+ and legacy docker-compose v1"""
    # Test v2 config validation
    v2_result = run_cmd(DOCKER_COMPOSE_CMD + ["config", "-q"], check=False)
    assert v2_result.returncode == 0, f"Docker compose v2 config validation failed: {v2_result.stderr}"
    # Test v1 config validation if available
    v1_available = run_cmd(["which", "docker-compose"], check=False).returncode == 0
    if v1_available:
        v1_result = run_cmd(DOCKER_COMPOSE_V1_CMD + ["config", "-q"], check=False)
        assert v1_result.returncode == 0, f"Docker compose v1 config validation failed: {v1_result.stderr}"
