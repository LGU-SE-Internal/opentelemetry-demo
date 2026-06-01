#!/bin/bash
test_val() {
    local val="$1"
    local expected_exit="$2"
    echo "Testing value: '$val'"
    (
        if [[ -n "${val:-}" ]]; then
            lower_val="${val,,}"
            if ! [[ "$lower_val" =~ ^(0|1|true|false)$ ]]; then
                echo "ERROR: Invalid LOCUST_EXIT_CODE_ON_FAILURE value: '$val'" >&2
                echo "Valid values are: 0, 1, true, false (case-insensitive)" >&2
                exit 1
            fi
        fi
        exit 0
    )
    local exit_code=$?
    if [[ $exit_code -eq $expected_exit ]]; then
        echo "PASS: exit code $exit_code as expected"
    else
        echo "FAIL: expected exit code $expected_exit, got $exit_code"
        exit 1
    fi
    echo ""
}

# Test valid values
test_val "" 0
test_val "0" 0
test_val "1" 0
test_val "true" 0
test_val "false" 0
test_val "TRUE" 0
test_val "FALSE" 0
test_val "True" 0
test_val "False" 0

# Test invalid values
test_val "2" 1
test_val "yes" 1
test_val "no" 1
test_val " " 1
test_val "invalid" 1
test_val "tRue1" 1

echo "All tests passed!"
