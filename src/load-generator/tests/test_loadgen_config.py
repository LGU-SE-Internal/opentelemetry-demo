import os
import subprocess
import sys
import pytest

LOCUSTFILE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "locustfile.py"))
PYTHON_EXEC = sys.executable

def test_ac1_default_values_when_no_env_vars():
    env = os.environ.copy()
    # Remove all loadgen config env vars if present
    for key in list(env.keys()):
        if key.startswith("LOADGEN_"):
            del env[key]
    
    # Run locust --help to verify it starts without error (no invalid config)
    result = subprocess.run(
        [PYTHON_EXEC, "-m", "locust", "-f", LOCUSTFILE_PATH, "--help"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Load generator failed to start with default values: stderr={result.stderr}"

def test_ac2_wait_time_env_vars_applied():
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith("LOADGEN_"):
            del env[key]
    env["LOADGEN_WAIT_TIME_MIN_SECONDS"] = "3"
    env["LOADGEN_WAIT_TIME_MAX_SECONDS"] = "8"
    
    result = subprocess.run(
        [PYTHON_EXEC, "-m", "locust", "-f", LOCUSTFILE_PATH, "--help"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Load generator failed to start with valid wait time values: stderr={result.stderr}"

def test_ac3_browser_navigation_timeout_env_var_applied():
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith("LOADGEN_"):
            del env[key]
    env["LOADGEN_BROWSER_NAVIGATION_TIMEOUT_SECONDS"] = "20"
    
    result = subprocess.run(
        [PYTHON_EXEC, "-m", "locust", "-f", LOCUSTFILE_PATH, "--help"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Load generator failed to start with valid browser timeout value: stderr={result.stderr}"

def test_ac4_trace_flush_wait_env_var_applied():
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith("LOADGEN_"):
            del env[key]
    env["LOADGEN_TRACE_FLUSH_WAIT_SECONDS"] = "5"
    
    result = subprocess.run(
        [PYTHON_EXEC, "-m", "locust", "-f", LOCUSTFILE_PATH, "--help"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Load generator failed to start with valid trace flush wait value: stderr={result.stderr}"

def test_ac5_non_integer_env_var_fails_start():
    test_cases = [
        ("LOADGEN_WAIT_TIME_MIN_SECONDS", "abc"),
        ("LOADGEN_WAIT_TIME_MAX_SECONDS", "2.5"),
        ("LOADGEN_BROWSER_NAVIGATION_TIMEOUT_SECONDS", "10.5"),
        ("LOADGEN_TRACE_FLUSH_WAIT_SECONDS", "invalid")
    ]
    
    for var_name, invalid_value in test_cases:
        env = os.environ.copy()
        for key in list(env.keys()):
            if key.startswith("LOADGEN_"):
                del env[key]
        env[var_name] = invalid_value
        
        result = subprocess.run(
            [PYTHON_EXEC, "-m", "locust", "-f", LOCUSTFILE_PATH, "--help"],
            env=env,
            capture_output=True,
            text=True
        )
        assert result.returncode != 0, f"Load generator did not fail for non-integer {var_name}={invalid_value}"
        assert var_name in result.stderr, f"Error message does not mention invalid variable {var_name}"

def test_ac6_non_positive_integer_env_var_fails_start():
    test_cases = [
        ("LOADGEN_WAIT_TIME_MIN_SECONDS", "0"),
        ("LOADGEN_WAIT_TIME_MIN_SECONDS", "-1"),
        ("LOADGEN_WAIT_TIME_MAX_SECONDS", "-5"),
        ("LOADGEN_BROWSER_NAVIGATION_TIMEOUT_SECONDS", "0"),
        ("LOADGEN_TRACE_FLUSH_WAIT_SECONDS", "-10")
    ]
    
    for var_name, invalid_value in test_cases:
        env = os.environ.copy()
        for key in list(env.keys()):
            if key.startswith("LOADGEN_"):
                del env[key]
        env[var_name] = invalid_value
        
        result = subprocess.run(
            [PYTHON_EXEC, "-m", "locust", "-f", LOCUSTFILE_PATH, "--help"],
            env=env,
            capture_output=True,
            text=True
        )
        assert result.returncode != 0, f"Load generator did not fail for non-positive {var_name}={invalid_value}"
        assert "positive integer" in result.stderr.lower(), f"Error message does not mention positive integer requirement for {var_name}"

def test_ac7_min_wait_exceeds_max_wait_fails_start():
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith("LOADGEN_"):
            del env[key]
    env["LOADGEN_WAIT_TIME_MIN_SECONDS"] = "10"
    env["LOADGEN_WAIT_TIME_MAX_SECONDS"] = "5"
    
    result = subprocess.run(
        [PYTHON_EXEC, "-m", "locust", "-f", LOCUSTFILE_PATH, "--help"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "Load generator did not fail when min wait time exceeds max wait time"
    assert "min cannot exceed max" in result.stderr.lower() or "min wait time cannot exceed max" in result.stderr.lower(), \
        "Error message does not mention min wait cannot exceed max wait"
