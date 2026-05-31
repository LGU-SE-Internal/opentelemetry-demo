#!/usr/bin/env bats

# Path to entrypoint script
ENTRYPOINT_SCRIPT="${BATS_TEST_DIRNAME}/entrypoint.sh"

setup() {
    # Create a temporary directory for mock locust
    MOCK_DIR=$(mktemp -d)
    export PATH="${MOCK_DIR}:${PATH}"
    
    # Create mock locust command
    cat << 'MOCK_EOF' > "${MOCK_DIR}/locust"
#!/bin/bash
exit 0
MOCK_EOF
    chmod +x "${MOCK_DIR}/locust"
}

teardown() {
    rm -rf "$MOCK_DIR"
}

@test "entrypoint.sh runs without error when LOCUST_RUN_TIME is not set" {
    run "$ENTRYPOINT_SCRIPT" --help
    [ "$status" -eq 0 ]
}

@test "entrypoint.sh accepts valid LOCUST_RUN_TIME values" {
    # Test various valid formats
    valid_values=("300s" "20m" "1h" "1h30m" "2h30m15s" "60s")
    for val in "${valid_values[@]}"; do
        LOCUST_RUN_TIME="$val" run "$ENTRYPOINT_SCRIPT" --help
        [ "$status" -eq 0 ]
    done
}

@test "entrypoint.sh rejects invalid LOCUST_RUN_TIME values" {
    # Test various invalid formats
    invalid_values=("300" "1h30" "1d" "abc" "1hour" "30min" " 1h " "1h 30m")
    for val in "${invalid_values[@]}"; do
        LOCUST_RUN_TIME="$val" run "$ENTRYPOINT_SCRIPT" --help
        [ "$status" -eq 1 ]
        [[ "$output" == *"ERROR: Invalid LOCUST_RUN_TIME value"* ]]
    done
}
