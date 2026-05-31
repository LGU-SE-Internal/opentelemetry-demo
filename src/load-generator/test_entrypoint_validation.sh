#!/bin/bash
set -euo pipefail

# Path to the entrypoint script we are testing
ENTRYPOINT_SCRIPT="./entrypoint.sh"
PASSED=0
FAILED=0

# Helper function to run a test case
run_test() {
    local test_name="$1"
    local expected_exit_code="$2"
    local env_vars="$3"
    local command="$4"

    echo -n "Running test: $test_name... "
    
    # Run the entrypoint script with the given environment variables, capture output and exit code
    { output=$(env -i PATH=".:$PATH" $env_vars bash "$ENTRYPOINT_SCRIPT" $command 2>&1); exit_code=$?; } || true

    if [ $exit_code -eq $expected_exit_code ]; then
        echo "PASSED"
        PASSED=$((PASSED + 1))
    else
        echo "FAILED (Expected exit code $expected_exit_code, got $exit_code)"
        echo "Output: $output"
        FAILED=$((FAILED + 1))
    fi
}

# Test AC-1: Invalid LOCUST_RUN_TIME values cause exit with error
run_test "Invalid LOCUST_RUN_TIME (123)" 1 "LOCUST_RUN_TIME=123" "--version"
run_test "Invalid LOCUST_RUN_TIME (abc)" 1 "LOCUST_RUN_TIME=abc" "--version"
run_test "Invalid LOCUST_RUN_TIME (1h30x)" 1 "LOCUST_RUN_TIME=1h30x" "--version"
run_test "Invalid LOCUST_RUN_TIME (1.5h)" 1 "LOCUST_RUN_TIME=1.5h" "--version"

# Test AC-2: Valid LOCUST_RUN_TIME values pass validation
run_test "Valid LOCUST_RUN_TIME (300s)" 0 "LOCUST_RUN_TIME=300s" "--version"
run_test "Valid LOCUST_RUN_TIME (20m)" 0 "LOCUST_RUN_TIME=20m" "--version"
run_test "Valid LOCUST_RUN_TIME (1h)" 0 "LOCUST_RUN_TIME=1h" "--version"
run_test "Valid LOCUST_RUN_TIME (1h30m)" 0 "LOCUST_RUN_TIME=1h30m" "--version"
run_test "Valid LOCUST_RUN_TIME (1h30m10s)" 0 "LOCUST_RUN_TIME=1h30m10s" "--version"

# Test AC-3: Invalid LOCUST_SPAWN_RATE values cause exit with error
run_test "Invalid LOCUST_SPAWN_RATE (abc)" 1 "LOCUST_SPAWN_RATE=abc" "--version"
run_test "Invalid LOCUST_SPAWN_RATE (10x)" 1 "LOCUST_SPAWN_RATE=10x" "--version"
run_test "Invalid LOCUST_SPAWN_RATE (-1)" 1 "LOCUST_SPAWN_RATE=-1" "--version"
run_test "Invalid LOCUST_SPAWN_RATE (1.2.3)" 1 "LOCUST_SPAWN_RATE=1.2.3" "--version"

# Test AC-4: Valid LOCUST_SPAWN_RATE values pass validation
run_test "Valid LOCUST_SPAWN_RATE (1)" 0 "LOCUST_SPAWN_RATE=1" "--version"
run_test "Valid LOCUST_SPAWN_RATE (10)" 0 "LOCUST_SPAWN_RATE=10" "--version"
run_test "Valid LOCUST_SPAWN_RATE (0.5)" 0 "LOCUST_SPAWN_RATE=0.5" "--version"
run_test "Valid LOCUST_SPAWN_RATE (100.25)" 0 "LOCUST_SPAWN_RATE=100.25" "--version"

# Test that no env vars set works fine
run_test "No LOCUST_* env vars set" 0 "" "--version"

# Summary
echo -e "\nTest Summary:"
echo "Total tests: $((PASSED + FAILED))"
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [ $FAILED -gt 0 ]; then
    echo "Some tests failed!"
    exit 1
else
    echo "All tests passed successfully!"
    exit 0
fi
