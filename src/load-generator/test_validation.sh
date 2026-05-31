#!/bin/bash
test_val() {
    local val="$1"
    expected_exit="$2"
    local output
    output=$(
        export LOCUST_HEADLESS="$val"
        bash -c '
        set -euo pipefail
        if [[ -n "${LOCUST_HEADLESS:-}" ]]; then
            shopt -s nocasematch
            if ! [[ "$LOCUST_HEADLESS" =~ ^(true|false)$ ]]; then
                echo "ERROR: Invalid LOCUST_HEADLESS value: '\''$LOCUST_HEADLESS'\''" >&2
                echo "Expected value is either '\''true'\'' or '\''false'\'' (case-insensitive)" >&2
                exit 1
            fi
            shopt -u nocasematch
        fi
        exit 0
        ' 2>&1
    )
    exit_code=$?
    if [[ $exit_code -eq "$expected_exit" ]]; then
        echo "PASS: LOCUST_HEADLESS=$val, expected exit $expected_exit, got $exit_code"
        if [[ $expected_exit -eq 1 ]]; then
            echo "  Error output: $output"
        fi
        return 0
    else
        echo "FAIL: LOCUST_HEADLESS=$val, expected exit $expected_exit, got $exit_code, output: $output"
        return 1
    fi
}

test_val "" 0
test_val "true" 0
test_val "TRUE" 0
test_val "True" 0
test_val "false" 0
test_val "FALSE" 0
test_val "False" 0
test_val "invalid" 1
test_val "1" 1
test_val "yes" 1
test_val "no" 1
