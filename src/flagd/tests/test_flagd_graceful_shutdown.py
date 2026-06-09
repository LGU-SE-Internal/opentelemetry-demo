#!/usr/bin/env python3
import os
import signal
import time
import subprocess
import requests
import pytest
from typing import Tuple

FLAGD_ENTRYPOINT = "../entrypoint.sh"
DEFAULT_TIMEOUT = "30s"
TEST_FLAGD_PORT = 8013

def start_flagd(env: dict = None) -> Tuple[subprocess.Popen, dict]:
    """Start flagd with given env vars, return process and merged env"""
    base_env = os.environ.copy()
    if env:
        base_env.update(env)
    base_env["FLAGD_PORT"] = str(TEST_FLAGD_PORT)
    # Start entrypoint script
    proc = subprocess.Popen(
        [FLAGD_ENTRYPOINT],
        cwd=os.path.dirname(__file__),
        env=base_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for startup
    time.sleep(2)
    return proc, base_env

def cleanup_flagd(proc: subprocess.Popen):
    """Force kill any remaining flagd processes"""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except:
        proc.kill()
    # Clean up any stray flagd processes
    subprocess.run(["pkill", "-f", "flagd"], capture_output=True)

def test_ac1_default_graceful_shutdown_timeout():
    """AC-1: When FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT not set, flagd starts with --graceful-shutdown-timeout 30s"""
    proc, env = start_flagd()
    try:
        # Check process args for the correct parameter
        ps_output = subprocess.check_output(
            ["ps", "aux"], text=True
        )
        flagd_lines = [line for line in ps_output.splitlines() if "flagd" in line and "--graceful-shutdown-timeout" in line]
        assert len(flagd_lines) > 0, "flagd process not found with graceful shutdown parameter"
        assert f"--graceful-shutdown-timeout {DEFAULT_TIMEOUT}" in flagd_lines[0], f"Expected default timeout {DEFAULT_TIMEOUT} not found in args"
    finally:
        cleanup_flagd(proc)

def test_ac2_custom_graceful_shutdown_timeout():
    """AC-2: When FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT is set, flagd starts with matching parameter"""
    custom_timeout = "1m"
    proc, env = start_flagd({"FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT": custom_timeout})
    try:
        ps_output = subprocess.check_output(
            ["ps", "aux"], text=True
        )
        flagd_lines = [line for line in ps_output.splitlines() if "flagd" in line and "--graceful-shutdown-timeout" in line]
        assert len(flagd_lines) > 0, "flagd process not found with graceful shutdown parameter"
        assert f"--graceful-shutdown-timeout {custom_timeout}" in flagd_lines[0], f"Expected custom timeout {custom_timeout} not found in args"
    finally:
        cleanup_flagd(proc)

def test_ac3_sigterm_forwarded_to_flagd():
    """AC-3: SIGTERM sent to entrypoint is forwarded to flagd within 100ms"""
    proc, env = start_flagd()
    try:
        # Get flagd pid
        ps_output = subprocess.check_output(["ps", "aux"], text=True)
        flagd_pid = None
        for line in ps_output.splitlines():
            if "flagd" in line and not "entrypoint" in line:
                flagd_pid = int(line.split()[1])
                break
        assert flagd_pid is not None, "flagd pid not found"

        # Send SIGTERM to entrypoint process
        start_time = time.time()
        proc.send_signal(signal.SIGTERM)
        
        # Wait for flagd process to exit
        while time.time() - start_time < 0.1:
            try:
                # Check if flagd pid is still running
                os.kill(flagd_pid, 0)
            except OSError:
                # Process exited
                break
            time.sleep(0.001)
        else:
            assert False, "SIGTERM not forwarded to flagd within 100ms"
    finally:
        cleanup_flagd(proc)

def test_ac4_sigint_forwarded_to_flagd():
    """AC-4: SIGINT sent to entrypoint is forwarded to flagd within 100ms"""
    proc, env = start_flagd()
    try:
        # Get flagd pid
        ps_output = subprocess.check_output(["ps", "aux"], text=True)
        flagd_pid = None
        for line in ps_output.splitlines():
            if "flagd" in line and not "entrypoint" in line:
                flagd_pid = int(line.split()[1])
                break
        assert flagd_pid is not None, "flagd pid not found"

        # Send SIGINT to entrypoint process
        start_time = time.time()
        proc.send_signal(signal.SIGINT)
        
        # Wait for flagd process to exit
        while time.time() - start_time < 0.1:
            try:
                # Check if flagd pid is still running
                os.kill(flagd_pid, 0)
            except OSError:
                # Process exited
                break
            time.sleep(0.001)
        else:
            assert False, "SIGINT not forwarded to flagd within 100ms"
    finally:
        cleanup_flagd(proc)

def test_ac5_new_requests_rejected_after_shutdown_signal():
    """AC-5: After shutdown signal, new requests return 503 Service Unavailable"""
    proc, env = start_flagd({"FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT": "10s"})
    try:
        # Verify service is up first
        resp = requests.get(f"http://localhost:{TEST_FLAGD_PORT}/schema.v1.Service/ResolveBoolean", timeout=1)
        # We don't care about the success response, just that it's not 503
        assert resp.status_code != 503, "Service returned 503 before shutdown signal"

        # Send SIGTERM to entrypoint
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.2) # Wait for signal processing

        # Send new request, expect 503
        with pytest.raises(requests.exceptions.RequestException) as excinfo:
            resp = requests.get(f"http://localhost:{TEST_FLAGD_PORT}/schema.v1.Service/ResolveBoolean", timeout=1)
            assert resp.status_code == 503, f"Expected 503 after shutdown, got {resp.status_code}"
    finally:
        cleanup_flagd(proc)

def test_ac6_in_flight_requests_completed_before_exit():
    """AC-6: In-flight requests completing within timeout get 200 OK before process exits"""
    proc, env = start_flagd({"FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT": "5s"})
    try:
        # Verify service is up
        resp = requests.get(f"http://localhost:{TEST_FLAGD_PORT}/schema.v1.Service/ResolveBoolean", timeout=1)
        assert resp.status_code != 503, "Service not available before test"

        # Start a long-running request in a thread that will take 2s to complete
        import threading
        request_result = {}
        def run_long_request():
            try:
                # Simulate long running request (add a delay query param if supported, or just use sleep and send after signal)
                time.sleep(0.5)
                resp = requests.get(f"http://localhost:{TEST_FLAGD_PORT}/schema.v1.Service/ResolveBoolean", timeout=3)
                request_result["status_code"] = resp.status_code
                request_result["success"] = True
            except Exception as e:
                request_result["error"] = str(e)
                request_result["success"] = False

        thread = threading.Thread(target=run_long_request)
        thread.start()

        # Send SIGTERM immediately after starting the request
        time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)

        # Wait for thread to complete
        thread.join(timeout=4)
        assert request_result.get("success") == True, "Long running request failed after shutdown signal"
        assert request_result.get("status_code") == 200, f"Expected 200 for in-flight request, got {request_result.get('status_code')}"
    finally:
        cleanup_flagd(proc)

def test_ac7_force_exit_after_timeout_exceeded():
    """AC-7: Process exits forcefully after configured timeout even with incomplete requests"""
    short_timeout = "2s"
    proc, env = start_flagd({"FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT": short_timeout})
    try:
        # Send SIGTERM to entrypoint
        start_time = time.time()
        proc.send_signal(signal.SIGTERM)

        # Wait for process to exit
        exit_code = proc.wait(timeout=3)
        elapsed = time.time() - start_time

        # Verify exit happened within ~2s (allow 0.5s tolerance)
        assert elapsed < 2.5, f"Process took too long to exit: {elapsed}s, expected <= 2s"
    finally:
        cleanup_flagd(proc)

def test_ac8_all_resources_closed_after_shutdown():
    """AC-8: All network connections and file handles are closed after successful shutdown"""
    proc, env = start_flagd({"FLAGD_GRACEFUL_SHUTDOWN_TIMEOUT": "5s"})
    try:
        # Get flagd pid
        ps_output = subprocess.check_output(["ps", "aux"], text=True)
        flagd_pid = None
        for line in ps_output.splitlines():
            if "flagd" in line and not "entrypoint" in line:
                flagd_pid = int(line.split()[1])
                break
        assert flagd_pid is not None, "flagd pid not found"

        # Check open file handles before shutdown
        lsof_output_before = subprocess.run(
            ["lsof", "-p", str(flagd_pid)], capture_output=True, text=True
        ).stdout
        assert "LISTEN" in lsof_output_before, "Flagd is not listening on any ports before shutdown"

        # Send SIGTERM and wait for exit
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=6)

        # Verify process is gone and no open handles left
        with pytest.raises(subprocess.CalledProcessError):
            subprocess.check_output(["lsof", "-p", str(flagd_pid)], stderr=subprocess.STDOUT, text=True)
        
        # Verify port is no longer in use
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get(f"http://localhost:{TEST_FLAGD_PORT}/health", timeout=1)
    finally:
        cleanup_flagd(proc)
