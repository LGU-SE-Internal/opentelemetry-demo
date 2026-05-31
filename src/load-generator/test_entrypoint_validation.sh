#!/bin/bash
set -euo pipefail

# Add current directory to PATH to use mock locust
export PATH=".:$PATH"

echo "Running entrypoint.sh validation tests..."

PASS=0
FAIL=0

# Test helper function
run_test() {
    local test_name="$1"
    local env_vars="$2"
    local expect_success="$3"
    
    echo -n "Test: $test_name... "
    
    # Run entrypoint with the given env vars, capture output and exit code
    # We pass --help as the command so locust just exits 0 without running tests
    set +e
    output=$(env $env_vars bash entrypoint.sh --help 2>&1)
    exit_code=$?
    set -e
    
    if [[ "$expect_success" == "true" ]]; then
        if [[ $exit_code -eq 0 ]]; then
            echo "PASS"
            PASS=$((PASS + 1))
        else
            echo "FAIL (expected success, got exit code $exit_code, output: $output)"
            FAIL=$((FAIL + 1))
        fi
    else
        if [[ $exit_code -ne 0 ]]; then
            echo "PASS"
            PASS=$((PASS + 1))
        else
            echo "FAIL (expected failure, got exit code $exit_code, output: $output)"
            FAIL=$((FAIL + 1))
        fi
    fi
}

# AC-1: Invalid LOCUST_RUN_TIME values cause error
run_test "Invalid LOCUST_RUN_TIME: 123 (no unit)" "LOCUST_RUN_TIME=123" "false"
run_test "Invalid LOCUST_RUN_TIME: abc (non numeric)" "LOCUST_RUN_TIME=abc" "false"
run_test "Invalid LOCUST_RUN_TIME: 1d (invalid unit)" "LOCUST_RUN_TIME=1d" "false"
run_test "Invalid LOCUST_RUN_TIME: 1h30 (missing unit on second part)" "LOCUST_RUN_TIME=1h30" "false"
run_test "Invalid LOCUST_RUN_TIME: empty string" "LOCUST_RUN_TIME=''" "true" # Should pass since empty is allowed

# AC-2: Valid LOCUST_RUN_TIME values pass
run_test "Valid LOCUST_RUN_TIME: 300s" "LOCUST_RUN_TIME=300s" "true"
run_test "Valid LOCUST_RUN_TIME: 20m" "LOCUST_RUN_TIME=20m" "true"
run_test "Valid LOCUST_RUN_TIME: 1h" "LOCUST_RUN_TIME=1h" "true"
run_test "Valid LOCUST_RUN_TIME: 1h30m" "LOCUST_RUN_TIME=1h30m" "true"
run_test "Valid LOCUST_RUN_TIME: 1h30m10s" "LOCUST_RUN_TIME=1h30m10s" "true"
run_test "LOCUST_RUN_TIME not set" "" "true"

# AC-3: Invalid LOCUST_SPAWN_RATE values cause error
run_test "Invalid LOCUST_SPAWN_RATE: abc" "LOCUST_SPAWN_RATE=abc" "false"
run_test "Invalid LOCUST_SPAWN_RATE: 1.2.3" "LOCUST_SPAWN_RATE=1.2.3" "false"
run_test "Invalid LOCUST_SPAWN_RATE: -1" "LOCUST_SPAWN_RATE=-1" "false"
run_test "Invalid LOCUST_SPAWN_RATE: 1,5 (comma instead of dot)" "LOCUST_SPAWN_RATE=1,5" "false"
run_test "Invalid LOCUST_SPAWN_RATE: empty string" "LOCUST_SPAWN_RATE=''" "true" # Should pass since empty is allowed

# AC-4: Valid LOCUST_SPAWN_RATE values pass
run_test "Valid LOCUST_SPAWN_RATE: 1 (integer)" "LOCUST_SPAWN_RATE=1" "true"
run_test "Valid LOCUST_SPAWN_RATE: 10 (integer)" "LOCUST_SPAWN_RATE=10" "true"
run_test "Valid LOCUST_SPAWN_RATE: 0.5 (float)" "LOCUST_SPAWN_RATE=0.5" "true"
run_test "Valid LOCUST_SPAWN_RATE: 100.25 (float)" "LOCUST_SPAWN_RATE=100.25" "true"
run_test "Valid LOCUST_SPAWN_RATE: 123456.789 (large float)" "LOCUST_SPAWN_RATE=123456.789" "true"
run_test "LOCUST_SPAWN_RATE not set" "" "true"

# Test both variables set valid
run_test "Both variables valid: LOCUST_RUN_TIME=1h LOCUST_SPAWN_RATE=2.5" "LOCUST_RUN_TIME=1h LOCUST_SPAWN_RATE=2.5" "true"

# Test both variables invalid
run_test "Both variables invalid: LOCUST_RUN_TIME=abc LOCUST_SPAWN_RATE=def" "LOCUST_RUN_TIME=abc LOCUST_SPAWN_RATE=def" "false"

echo
echo "Test results: $PASS passed, $FAIL failed"

if [[ $FAIL -gt 0 ]]; then
    exit 1
else
    echo "All tests passed!"
    exit 0
fi
