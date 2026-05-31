#!/bin/bash
set -euo pipefail

ENTRYPOINT_SCRIPT="./entrypoint.sh"
MOCK_LOCUST="./mock_locust.sh"

echo "Running entrypoint.sh validation tests..."
echo "========================================"

pass_count=0
fail_count=0

# Helper function to run test cases
run_test() {
    local test_name="$1"
    local env_vars="$2"
    local expect_success="$3"
    
    echo -n "Test: $test_name... "
    
    # Run entrypoint with the given env vars, using mock locust
    output=$(env -i PATH="$PATH" $env_vars "$ENTRYPOINT_SCRIPT" 2>&1 || true)
    
    if [ "$expect_success" = true ]; then
        if echo "$output" | grep -q "ERROR:"; then
            echo "FAIL: Expected success, got error: $output"
            fail_count=$((fail_count + 1))
        else
            echo "PASS"
            pass_count=$((pass_count + 1))
        fi
    else
        if echo "$output" | grep -q "ERROR:"; then
            echo "PASS"
            pass_count=$((pass_count + 1))
        else
            echo "FAIL: Expected error, got success: $output"
            fail_count=$((fail_count + 1))
        fi
    fi
}

# First make mock locust executable
chmod +x "$MOCK_LOCUST"
export PATH="$PWD:$PATH"

# AC-2: Test valid LOCUST_RUN_TIME values pass
run_test "Valid LOCUST_RUN_TIME 300s" "LOCUST_RUN_TIME=300s" true
run_test "Valid LOCUST_RUN_TIME 20m" "LOCUST_RUN_TIME=20m" true
run_test "Valid LOCUST_RUN_TIME 1h" "LOCUST_RUN_TIME=1h" true
run_test "Valid LOCUST_RUN_TIME 1h30m" "LOCUST_RUN_TIME=1h30m" true
run_test "Valid LOCUST_RUN_TIME 1h30m10s" "LOCUST_RUN_TIME=1h30m10s" true

# AC-1: Test invalid LOCUST_RUN_TIME values fail
run_test "Invalid LOCUST_RUN_TIME 123" "LOCUST_RUN_TIME=123" false
run_test "Invalid LOCUST_RUN_TIME 1d" "LOCUST_RUN_TIME=1d" false
run_test "Invalid LOCUST_RUN_TIME 1h30" "LOCUST_RUN_TIME=1h30" false
run_test "Invalid LOCUST_RUN_TIME abc" "LOCUST_RUN_TIME=abc" false
run_test "Invalid LOCUST_RUN_TIME 1.5h" "LOCUST_RUN_TIME=1.5h" false

# AC-4: Test valid LOCUST_SPAWN_RATE values pass
run_test "Valid LOCUST_SPAWN_RATE 1" "LOCUST_SPAWN_RATE=1" true
run_test "Valid LOCUST_SPAWN_RATE 10" "LOCUST_SPAWN_RATE=10" true
run_test "Valid LOCUST_SPAWN_RATE 0.5" "LOCUST_SPAWN_RATE=0.5" true
run_test "Valid LOCUST_SPAWN_RATE 100.25" "LOCUST_SPAWN_RATE=100.25" true
run_test "Valid LOCUST_SPAWN_RATE 0" "LOCUST_SPAWN_RATE=0" true
run_test "Valid LOCUST_SPAWN_RATE 123456.789" "LOCUST_SPAWN_RATE=123456.789" true

# AC-3: Test invalid LOCUST_SPAWN_RATE values fail
run_test "Invalid LOCUST_SPAWN_RATE abc" "LOCUST_SPAWN_RATE=abc" false
run_test "Invalid LOCUST_SPAWN_RATE 1.2.3" "LOCUST_SPAWN_RATE=1.2.3" false
run_test "Invalid LOCUST_SPAWN_RATE -1" "LOCUST_SPAWN_RATE=-1" false
run_test "Invalid LOCUST_SPAWN_RATE 10s" "LOCUST_SPAWN_RATE=10s" false
run_test "Invalid LOCUST_SPAWN_RATE .5" "LOCUST_SPAWN_RATE=.5" false

# Test both variables set to valid values pass
run_test "Both variables valid" "LOCUST_RUN_TIME=1h LOCUST_SPAWN_RATE=2.5" true

# Test neither variable set passes
run_test "No variables set" "" true

echo "========================================"
echo "Results: $pass_count passed, $fail_count failed"

if [ "$fail_count" -gt 0 ]; then
    exit 1
else
    echo "All tests passed!"
    exit 0
fi
