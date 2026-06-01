#!/bin/bash
set +e

# Test valid values
VALID_VALUES=("1s" "30m" "2h" "1h30m" "1h30m10s" "0s" "12345h")
for val in "${VALID_VALUES[@]}"; do
    export LOCUST_RUN_TIME="$val"
    bash -c '
        if [[ -n "${LOCUST_RUN_TIME:-}" ]]; then
            if ! [[ "$LOCUST_RUN_TIME" =~ ^([0-9]+[hms])+$ ]]; then
                exit 1
            fi
        fi
        exit 0
    '
    if [[ $? -ne 0 ]]; then
        echo "FAIL: Valid value '$val' was rejected"
        exit 1
    else
        echo "PASS: Valid value '$val' accepted"
    fi
done

# Test invalid values
INVALID_VALUES=("1" "h" "1hour" "30minutes" "1d" "1.5h" "abc" "1h 30m" "-1s")
for val in "${INVALID_VALUES[@]}"; do
    export LOCUST_RUN_TIME="$val"
    bash -c '
        if [[ -n "${LOCUST_RUN_TIME:-}" ]]; then
            if ! [[ "$LOCUST_RUN_TIME" =~ ^([0-9]+[hms])+$ ]]; then
                exit 1
            fi
        fi
        exit 0
    '
    if [[ $? -eq 0 ]]; then
        echo "FAIL: Invalid value '$val' was accepted"
        exit 1
    else
        echo "PASS: Invalid value '$val' correctly rejected"
    fi
done

echo "All validation tests passed!"
