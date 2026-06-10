#!/usr/bin/env python3
import os
import signal
import subprocess
import time
import tempfile
import pytest

JAEGER_ENTRYPOINT = "./entrypoint.sh"
MOCK_JAEGER_BINARY = "./mock_jaeger_query"
TMP_JAEGER_DIR = "/tmp/jaeger/"

@pytest.fixture(autouse=True)
def setup_and_cleanup():
    # Create mock jaeger-query binary
    with open(MOCK_JAEGER_BINARY, "w") as f:
        f.write("""#!/bin/bash
echo "MOCK JAEGER STARTED PID $$"
trap 'echo "MOCK JAEGER GOT SIGTERM"; exit $1' TERM
trap 'echo "MOCK JAEGER GOT SIGINT"; exit $2' INT
sleep $3
exit $4
""")
    os.chmod(MOCK_JAEGER_BINARY, 0o755)
    # Create temp directory for jaeger files
    os.makedirs(TMP_JAEGER_DIR, exist_ok=True)
    # Add current dir to path so entrypoint finds mock
    original_path = os.environ["PATH"]
    os.environ["PATH"] = f"{os.getcwd()}:{original_path}"
    yield
    # Cleanup
    os.remove(MOCK_JAEGER_BINARY)
    if os.path.exists(TMP_JAEGER_DIR):
        for f in os.listdir(TMP_JAEGER_DIR):
            os.remove(os.path.join(TMP_JAEGER_DIR, f))
    os.environ["PATH"] = original_path

def create_test_temp_files():
    # Create test files in temp dir
    for i in range(3):
        with open(os.path.join(TMP_JAEGER_DIR, f"test_{i}.tmp"), "w") as f:
            f.write(f"test content {i}")
    assert len(os.listdir(TMP_JAEGER_DIR)) == 3

def test_ac1_signal_propagation_to_jaeger():
    """AC-1: SIGTERM/SIGINT sent to entrypoint propagates SIGTERM to jaeger-query child"""
    create_test_temp_files()
    
    # Start entrypoint with mock jaeger that sleeps for 60s, exits 0 normally
    env = os.environ.copy()
    env["SHUTDOWN_GRACE_PERIOD_SECONDS"] = "5"
    proc = subprocess.Popen(
        [JAEGER_ENTRYPOINT, "0", "0", "60", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Wait for jaeger to start
    time.sleep(1)
    assert proc.poll() is None, "Entrypoint should still be running"
    
    # Send SIGTERM to entrypoint
    proc.send_signal(signal.SIGTERM)
    
    # Wait for process to exit
    stdout, _ = proc.communicate(timeout=10)
    
    # Check that jaeger received SIGTERM
    assert "MOCK JAEGER GOT SIGTERM" in stdout, "Jaeger should have received SIGTERM from entrypoint"
    
    # Check temp files are cleaned up
    assert len(os.listdir(TMP_JAEGER_DIR)) == 0, "Temp files should be cleaned up"

def test_ac2_graceful_exit_within_grace_period():
    """AC-2: Jaeger exits within grace period -> cleanup, propagate exit code"""
    create_test_temp_files()
    expected_exit_code = 42
    
    # Mock jaeger: exits 42 2s after receiving SIGTERM, normal sleep is 60s
    env = os.environ.copy()
    env["SHUTDOWN_GRACE_PERIOD_SECONDS"] = "10"
    proc = subprocess.Popen(
        [JAEGER_ENTRYPOINT, str(expected_exit_code), "0", "60", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(1)
    proc.send_signal(signal.SIGTERM)
    
    # Wait for exit
    proc.wait(timeout=15)
    
    # Verify exit code is propagated
    assert proc.returncode == expected_exit_code, f"Exit code should be {expected_exit_code}, got {proc.returncode}"
    # Verify cleanup
    assert len(os.listdir(TMP_JAEGER_DIR)) == 0, "Temp files should be cleaned up"

def test_ac3_force_kill_after_grace_period():
    """AC-3: Jaeger stays running after grace period -> SIGKILL, exit 137, cleanup"""
    create_test_temp_files()
    
    # Mock jaeger ignores SIGTERM, sleeps for 60s
    with open(MOCK_JAEGER_BINARY, "w") as f:
        f.write("""#!/bin/bash
echo "MOCK JAEGER STARTED PID $$"
trap '' TERM # Ignore SIGTERM
sleep 60
exit 0
""")
    os.chmod(MOCK_JAEGER_BINARY, 0o755)
    
    env = os.environ.copy()
    env["SHUTDOWN_GRACE_PERIOD_SECONDS"] = "3"
    proc = subprocess.Popen(
        [JAEGER_ENTRYPOINT, "0", "0", "60", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(1)
    proc.send_signal(signal.SIGTERM)
    
    # Wait for exit (should take ~3s grace period + small overhead)
    start_time = time.time()
    proc.wait(timeout=10)
    elapsed = time.time() - start_time
    
    # Verify exit code is 137 (SIGKILL)
    assert proc.returncode == 137, f"Exit code should be 137, got {proc.returncode}"
    # Verify waited approx grace period
    assert 2.5 <= elapsed <= 5, f"Should have waited ~3s grace period, took {elapsed}s"
    # Verify cleanup
    assert len(os.listdir(TMP_JAEGER_DIR)) == 0, "Temp files should be cleaned up"

def test_ac4_custom_grace_period():
    """AC-4: Custom SHUTDOWN_GRACE_PERIOD_SECONDS modifies wait time"""
    create_test_temp_files()
    
    # Mock jaeger ignores SIGTERM
    with open(MOCK_JAEGER_BINARY, "w") as f:
        f.write("""#!/bin/bash
echo "MOCK JAEGER STARTED PID $$"
trap '' TERM
sleep 60
exit 0
""")
    os.chmod(MOCK_JAEGER_BINARY, 0o755)
    
    # Test custom 7s grace period
    env = os.environ.copy()
    env["SHUTDOWN_GRACE_PERIOD_SECONDS"] = "7"
    proc = subprocess.Popen(
        [JAEGER_ENTRYPOINT, "0", "0", "60", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(1)
    proc.send_signal(signal.SIGTERM)
    
    start_time = time.time()
    proc.wait(timeout=15)
    elapsed = time.time() - start_time
    
    assert 6.5 <= elapsed <= 9, f"Should have waited ~7s grace period, took {elapsed}s"
    assert proc.returncode == 137, "Exit code should be 137"

def test_ac5_normal_exit_no_shutdown_signal():
    """AC-5: Normal jaeger exit (no signal) -> cleanup, propagate exit code"""
    create_test_temp_files()
    expected_exit_code = 123
    
    # Mock jaeger exits normally after 2s, no signal needed
    env = os.environ.copy()
    proc = subprocess.Popen(
        [JAEGER_ENTRYPOINT, "0", "0", "2", str(expected_exit_code)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Wait for process to exit on its own
    proc.wait(timeout=10)
    
    assert proc.returncode == expected_exit_code, f"Exit code should be {expected_exit_code}, got {proc.returncode}"
    assert len(os.listdir(TMP_JAEGER_DIR)) == 0, "Temp files should be cleaned up"

def test_ac6_invalid_grace_period_uses_default():
    """AC-6: Grace period <=0 uses default 30s"""
    create_test_temp_files()
    
    # Mock jaeger ignores SIGTERM
    with open(MOCK_JAEGER_BINARY, "w") as f:
        f.write("""#!/bin/bash
echo "MOCK JAEGER STARTED PID $$"
trap '' TERM
sleep 60
exit 0
""")
    os.chmod(MOCK_JAEGER_BINARY, 0o755)
    
    # Test negative grace period
    env = os.environ.copy()
    env["SHUTDOWN_GRACE_PERIOD_SECONDS"] = "-5"
    proc = subprocess.Popen(
        [JAEGER_ENTRYPOINT, "0", "0", "60", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(1)
    proc.send_signal(signal.SIGTERM)
    
    # Wait up to 35s, should exit before that with 137
    try:
        proc.wait(timeout=35)
        assert proc.returncode == 137, "Exit code should be 137"
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("Process did not exit within default 30s grace period when given negative grace period value")
    
    # Test 0 grace period
    env["SHUTDOWN_GRACE_PERIOD_SECONDS"] = "0"
    proc2 = subprocess.Popen(
        [JAEGER_ENTRYPOINT, "0", "0", "60", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(1)
    proc2.send_signal(signal.SIGTERM)
    
    try:
        proc2.wait(timeout=35)
        assert proc2.returncode == 137, "Exit code should be 137"
    except subprocess.TimeoutExpired:
        proc2.kill()
        pytest.fail("Process did not exit within default 30s grace period when given 0 grace period value")
