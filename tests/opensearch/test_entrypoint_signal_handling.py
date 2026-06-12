#!/usr/bin/env python3
import os
import signal
import subprocess
import time
import tempfile
import json
from pathlib import Path

ENTRYPOINT_SCRIPT = Path(__file__).parent.parent.parent / "src" / "opensearch" / "startup-ilm-config.sh"
MOCK_OPENSEARCH_BIN = Path(__file__).parent / "mock_opensearch.py"

# Write mock opensearch script that records signals and exit codes
MOCK_OPENSEARCH_CONTENT = """#!/usr/bin/env python3
import os
import sys
import time
import signal

# Write PID to file for test to reference
pid_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mock_opensearch.pid"
signal_log = sys.argv[2] if len(sys.argv) > 2 else "/tmp/mock_opensearch_signals.log"
exit_code = int(sys.argv[3]) if len(sys.argv) > 3 else 0
delay_exit = float(sys.argv[4]) if len(sys.argv) >4 else 2.0

with open(pid_file, "w") as f:
    f.write(str(os.getpid()))

def signal_handler(sig, frame):
    with open(signal_log, "a") as f:
        f.write(f"{sig}\\n")
    # Wait delay_exit seconds before exiting
    time.sleep(delay_exit)
    sys.exit(exit_code)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGQUIT, signal_handler)

# Keep running until signal received
while True:
    time.sleep(0.1)
"""

def setup_module():
    # Write mock opensearch bin
    with open(MOCK_OPENSEARCH_BIN, "w") as f:
        f.write(MOCK_OPENSEARCH_CONTENT)
    os.chmod(MOCK_OPENSEARCH_BIN, 0o755)
    os.chmod(ENTRYPOINT_SCRIPT, 0o755)

def teardown_module():
    if MOCK_OPENSEARCH_BIN.exists():
        os.unlink(MOCK_OPENSEARCH_BIN)

def test_ac1_sigterm_forwarded_within_1s():
    """AC-1: SIGTERM received by entrypoint is forwarded to opensearch within 1s"""
    with tempfile.TemporaryDirectory() as tmpdir:
        pid_file = Path(tmpdir) / "opensearch.pid"
        signal_log = Path(tmpdir) / "signals.log"
        
        # Run entrypoint, overriding opensearch command to our mock
        env = os.environ.copy()
        # TODO: Update to match how entrypoint invokes opensearch, adjust test as needed
        proc = subprocess.Popen(
            [ENTRYPOINT_SCRIPT, str(MOCK_OPENSEARCH_BIN), str(pid_file), str(signal_log), "0", "2"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait for mock opensearch to start and write PID
        time.sleep(0.5)
        assert pid_file.exists(), "Mock opensearch PID file not created"
        
        # Send SIGTERM to entrypoint process
        send_time = time.time()
        proc.send_signal(signal.SIGTERM)
        
        # Wait up to 1.5s for signal to appear in log
        for _ in range(15):
            if signal_log.exists() and signal_log.read_text().strip() == str(signal.SIGTERM):
                receive_time = time.time()
                assert receive_time - send_time < 1.0, f"SIGTERM took {receive_time - send_time:.2f}s to forward, expected <1s"
                break
            time.sleep(0.1)
        else:
            assert False, "SIGTERM was not forwarded to opensearch process"
        
        # Cleanup
        proc.terminate()
        proc.wait(timeout=3)

def test_ac2_entrypoint_waits_for_opensearch_exit():
    """AC-2: Entrypoint remains running until opensearch exits completely"""
    with tempfile.TemporaryDirectory() as tmpdir:
        pid_file = Path(tmpdir) / "opensearch.pid"
        signal_log = Path(tmpdir) / "signals.log"
        exit_delay = 2.0
        
        proc = subprocess.Popen(
            [ENTRYPOINT_SCRIPT, str(MOCK_OPENSEARCH_BIN), str(pid_file), str(signal_log), "0", str(exit_delay)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        time.sleep(0.5)
        assert pid_file.exists()
        
        # Send SIGTERM to entrypoint
        send_time = time.time()
        proc.send_signal(signal.SIGTERM)
        
        # Check if entrypoint is still running after 1s (opensearch takes 2s to exit)
        time.sleep(1.0)
        assert proc.poll() is None, "Entrypoint exited before opensearch process"
        
        # Wait for entrypoint to exit
        proc.wait(timeout=3)
        total_time = time.time() - send_time
        assert total_time >= exit_delay, f"Entrypoint exited after {total_time:.2f}s, expected at least {exit_delay}s (opensearch delay)"

def test_ac3_entrypoint_exit_code_matches_opensearch():
    """AC-3: Entrypoint exits with same exit code as opensearch child"""
    test_exit_codes = [0, 1, 143, 130, 131]
    for exit_code in test_exit_codes:
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "opensearch.pid"
            signal_log = Path(tmpdir) / "signals.log"
            
            proc = subprocess.Popen(
                [ENTRYPOINT_SCRIPT, str(MOCK_OPENSEARCH_BIN), str(pid_file), str(signal_log), str(exit_code), "0.5"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            time.sleep(0.5)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=3)
            
            assert proc.returncode == exit_code, f"Expected exit code {exit_code}, got {proc.returncode}"

def test_ac4_sigint_forwarded_and_waits():
    """AC-4: SIGINT is forwarded to opensearch, entrypoint waits for exit"""
    with tempfile.TemporaryDirectory() as tmpdir:
        pid_file = Path(tmpdir) / "opensearch.pid"
        signal_log = Path(tmpdir) / "signals.log"
        
        proc = subprocess.Popen(
            [ENTRYPOINT_SCRIPT, str(MOCK_OPENSEARCH_BIN), str(pid_file), str(signal_log), "130", "1.5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        time.sleep(0.5)
        send_time = time.time()
        proc.send_signal(signal.SIGINT)
        
        # Wait for signal in log
        for _ in range(15):
            if signal_log.exists() and str(signal.SIGINT) in signal_log.read_text():
                break
            time.sleep(0.1)
        else:
            assert False, "SIGINT was not forwarded to opensearch"
        
        # Check entrypoint waits
        time.sleep(1.0)
        assert proc.poll() is None, "Entrypoint exited early for SIGINT"
        
        proc.wait(timeout=2)
        assert proc.returncode == 130, "Exit code mismatch for SIGINT"

def test_ac5_sigquit_forwarded_and_waits():
    """AC-5: SIGQUIT is forwarded to opensearch, entrypoint waits for exit"""
    with tempfile.TemporaryDirectory() as tmpdir:
        pid_file = Path(tmpdir) / "opensearch.pid"
        signal_log = Path(tmpdir) / "signals.log"
        
        proc = subprocess.Popen(
            [ENTRYPOINT_SCRIPT, str(MOCK_OPENSEARCH_BIN), str(pid_file), str(signal_log), "131", "1.5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        time.sleep(0.5)
        send_time = time.time()
        proc.send_signal(signal.SIGQUIT)
        
        # Wait for signal in log
        for _ in range(15):
            if signal_log.exists() and str(signal.SIGQUIT) in signal_log.read_text():
                break
            time.sleep(0.1)
        else:
            assert False, "SIGQUIT was not forwarded to opensearch"
        
        # Check entrypoint waits
        time.sleep(1.0)
        assert proc.poll() is None, "Entrypoint exited early for SIGQUIT"
        
        proc.wait(timeout=2)
        assert proc.returncode == 131, "Exit code mismatch for SIGQUIT"

def test_ac6_existing_startup_steps_run_unchanged():
    """AC-6: All existing startup sequence steps (including ILM config) run in original order"""
    # Run entrypoint without signals, capture output and verify ILM steps run
    proc = subprocess.Popen(
        [ENTRYPOINT_SCRIPT, "echo", "mock_opensearch_start"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = proc.communicate(timeout=10)
    
    # Verify expected ILM config steps are present in output
    assert "ILM policy configuration" in stdout or "ILM policy configuration" in stderr or "ilm" in stdout.lower() or "ilm" in stderr.lower(), "ILM policy configuration step not found"
    assert proc.returncode == 0, "Entrypoint failed to run existing startup steps"

def test_ac7_opensearch_receives_original_args_env():
    """AC-7: Opensearch process is started with all original command-line params and env vars unchanged"""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_env_var = "TEST_OPENSEARCH_ENV_1234"
        test_env_val = "test_value_5678"
        test_args = ["arg1", "--option=test", "arg2"]
        
        # Write mock opensearch that dumps args and env
        mock_dump_script = Path(tmpdir) / "mock_dump.py"
        mock_dump_script.write_text("""#!/usr/bin/env python3
import os
import sys
import json
with open("/tmp/opensearch_args_env.json", "w") as f:
    json.dump({
        "args": sys.argv,
        "env": dict(os.environ)
    }, f)
# Exit immediately
sys.exit(0)
""")
        os.chmod(mock_dump_script, 0o755)
        
        env = os.environ.copy()
        env[test_env_var] = test_env_val
        
        proc = subprocess.Popen(
            [ENTRYPOINT_SCRIPT, str(mock_dump_script)] + test_args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        proc.wait(timeout=10)
        
        dump_file = Path("/tmp/opensearch_args_env.json")
        assert dump_file.exists(), "Opensearch args/env dump not found"
        data = json.loads(dump_file.read_text())
        
        # Check args are preserved
        assert str(mock_dump_script) in data["args"], "Mock opensearch path not found in args"
        for arg in test_args:
            assert arg in data["args"], f"Expected arg {arg} not found in opensearch args"
        
        # Check env is preserved
        assert test_env_var in data["env"], f"Expected env var {test_env_var} not found"
        assert data["env"][test_env_var] == test_env_val, f"Env var {test_env_var} value mismatch"
        
        # Cleanup
        if dump_file.exists():
            os.unlink(dump_file)
