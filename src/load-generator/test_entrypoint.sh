#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT_SCRIPT="$SCRIPT_DIR/entrypoint.sh"

# Create mock locust command so we don't need actual locust installed
MOCK_BIN_DIR=$(mktemp -d)
trap 'rm -rf "$MOCK_BIN_DIR"' EXIT
cat > "$MOCK_BIN_DIR/locust" << 'EOF'
#!/bin/bash
# Mock locust command that just exits successfully
exit 0
EOF
chmod +x "$MOCK_BIN_DIR/locust"
export PATH="$MOCK_BIN_DIR:$PATH"

echo "Running entrypoint.sh validation tests..."
echo "=========================================="

pass_count=0
fail_count=0

run_test() {
    local test_name="$1"
    local expected_exit_code="$2"
    shift 2
    local env_vars=("$@")
    
    echo -n "Test: $test_name ... "
    
    # Run the entrypoint script with provided env vars, just test validation (pass --version so it doesn't run locust for real)
    set +e
    env "${env_vars[@]}" "$ENTRYPOINT_SCRIPT" --version >/dev/null 2>&1
    actual_exit_code=$?
    set -e
    
    if [ "$actual_exit_code" -eq "$expected_exit_code" ]; then
        echo "PASS"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL (expected exit code $expected_exit_code, got $actual_exit_code)"
        fail_count=$((fail_count + 1))
    fi
}

# LOCUST_RUN_TIME tests
echo -e "\nLOCUST_RUN_TIME Tests:"
echo "----------------------"
run_test "Invalid value (no unit)" 1 "LOCUST_RUN_TIME=123"
run_test "Invalid value (wrong unit)" 1 "LOCUST_RUN_TIME=123d"
run_test "Invalid value (text)" 1 "LOCUST_RUN_TIME=abc"
run_test "Invalid value (mixed invalid)" 1 "LOCUST_RUN_TIME=1habc"
run_test "Valid value (seconds)" 0 "LOCUST_RUN_TIME=300s"
run_test "Valid value (minutes)" 0 "LOCUST_RUN_TIME=20m"
run_test "Valid value (hours)" 0 "LOCUST_RUN_TIME=1h"
run_test "Valid value (mixed units)" 0 "LOCUST_RUN_TIME=1h30m"
run_test "Valid value (multiple mixed units)" 0 "LOCUST_RUN_TIME=1h30m15s"

# LOCUST_SPAWN_RATE tests
echo -e "\nLOCUST_SPAWN_RATE Tests:"
echo "------------------------"
run_test "Invalid value (text)" 1 "LOCUST_SPAWN_RATE=abc"
run_test "Invalid value (negative number)" 1 "LOCUST_SPAWN_RATE=-10"
run_test "Invalid value (non-numeric)" 1 "LOCUST_SPAWN_RATE=10x"
run_test "Invalid value (double decimal)" 1 "LOCUST_SPAWN_RATE=10.5.2"
run_test "Valid value (integer)" 0 "LOCUST_SPAWN_RATE=1"
run_test "Valid value (larger integer)" 0 "LOCUST_SPAWN_RATE=100"
run_test "Valid value (float < 1)" 0 "LOCUST_SPAWN_RATE=0.5"
run_test "Valid value (float > 1)" 0 "LOCUST_SPAWN_RATE=100.25"

# Test both variables set valid
echo -e "\nCombined Tests:"
echo "---------------"
run_test "Both variables valid" 0 "LOCUST_RUN_TIME=1h" "LOCUST_SPAWN_RATE=10"
run_test "One invalid, one valid" 1 "LOCUST_RUN_TIME=1h" "LOCUST_SPAWN_RATE=invalid"

echo -e "\n=========================================="
echo "Test Results: $pass_count passed, $fail_count failed"

if [ "$fail_count" -gt 0 ]; then
    exit 1
else
    echo "All tests passed!"
    exit 0
fi
