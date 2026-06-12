import yaml
import os
import subprocess
import pytest

LOAD_GENERATOR_DEPLOYMENT_PATH = "./kubernetes/load-generator/deployment.yaml"
APP_CODE_PATH = "./src/load-generator/"

def load_deployment_manifest():
    with open(LOAD_GENERATOR_DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

def test_ac1_termination_grace_period_field_exists_and_set_to_60():
    """AC-1: Deployment manifest contains terminationGracePeriodSeconds=60 at .spec.template.spec"""
    manifest = load_deployment_manifest()
    spec = manifest.get("spec", {}).get("template", {}).get("spec", {})
    assert "terminationGracePeriodSeconds" in spec, "Missing terminationGracePeriodSeconds field"
    assert isinstance(spec["terminationGracePeriodSeconds"], int), "Field must be integer type"
    assert spec["terminationGracePeriodSeconds"] == 60, f"Expected 60, got {spec['terminationGracePeriodSeconds']}"

def test_ac2_termination_grace_period_correct_indentation_level():
    """AC-2: Field is at same level as containers and securityContext in Pod template spec"""
    with open(LOAD_GENERATOR_DEPLOYMENT_PATH, "r") as f:
        lines = f.readlines()
    
    line_numbers = {
        "terminationGracePeriodSeconds": None,
        "containers": None,
        "securityContext": None
    }
    
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("terminationGracePeriodSeconds:"):
            line_numbers["terminationGracePeriodSeconds"] = idx
        elif stripped == "containers:" and line_numbers["containers"] is None:
            line_numbers["containers"] = idx
        elif stripped == "securityContext:" and line_numbers["securityContext"] is None:
            line_numbers["securityContext"] = idx
    
    assert line_numbers["terminationGracePeriodSeconds"] is not None, "Field not found in manifest"
    assert line_numbers["containers"] is not None, "containers field not found"
    assert line_numbers["securityContext"] is not None, "securityContext field not found"
    
    # Check indentation level (number of leading spaces)
    tg_line = lines[line_numbers["terminationGracePeriodSeconds"] - 1]
    tg_indent = len(tg_line) - len(tg_line.lstrip())
    
    containers_line = lines[line_numbers["containers"] - 1]
    containers_indent = len(containers_line) - len(containers_line.lstrip())
    
    sc_line = lines[line_numbers["securityContext"] - 1]
    sc_indent = len(sc_line) - len(sc_line.lstrip())
    
    assert tg_indent == containers_indent, f"Termination grace period indent {tg_indent} != containers indent {containers_indent}"
    assert tg_indent == sc_indent, f"Termination grace period indent {tg_indent} != securityContext indent {sc_indent}"

def test_ac3_termination_grace_period_ge_app_shutdown_timeout():
    """AC-3: 60s grace period >= any configured graceful shutdown timeout in app code"""
    # Find all references to shutdown, timeout, SIGTERM, grace in load generator code
    shutdown_timeouts = []
    for root, _, files in os.walk(APP_CODE_PATH):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                with open(file_path, "r") as f:
                    content = f.read()
                    # Look for timeout values related to shutdown/grace
                    import re
                    # Patterns for timeout configs
                    patterns = [
                        r"grace[_-]period[_-]seconds\s*=\s*(\d+)",
                        r"shutdown[_-]timeout\s*=\s*(\d+)",
                        r"sigterm[_-]timeout\s*=\s*(\d+)",
                        r"stop_timeout\s*=\s*(\d+)",
                        r"LOCUST_SHUTDOWN_TIMEOUT\s*[=:]\s*(\d+)"
                    ]
                    for pat in patterns:
                        matches = re.findall(pat, content, re.IGNORECASE)
                        for match in matches:
                            shutdown_timeouts.append(int(match))
    
    # If no explicit timeouts found, assume default is <= 30s (default k8s grace period)
    if not shutdown_timeouts:
        pytest.skip("No explicit graceful shutdown timeout values found in app code, assuming <=30s")
    
    max_app_timeout = max(shutdown_timeouts)
    assert max_app_timeout <= 60, f"App shutdown timeout {max_app_timeout}s > 60s termination grace period"

def test_ac4_kubectl_apply_no_validation_errors():
    """AC-4: kubectl apply returns no validation errors for modified manifest"""
    # Use kubectl dry-run to validate manifest
    result = subprocess.run(
        ["kubectl", "apply", "-f", LOAD_GENERATOR_DEPLOYMENT_PATH, "--dry-run=client"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"kubectl dry-run failed: {result.stderr}"
    assert "error" not in result.stderr.lower(), f"Validation errors found: {result.stderr}"
