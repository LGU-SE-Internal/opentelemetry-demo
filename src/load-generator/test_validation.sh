#!/bin/bash
set -euo pipefail

# Test 1: Empty value (allowed)
echo "Test 1: Empty LOCUST_TAGS"
LOCUST_TAGS=""
if [[ -n "${LOCUST_TAGS:-}" ]]; then
    echo "FAIL: Empty value should skip validation"
else
    echo "PASS: Empty value allowed, no validation run"
fi

# Test 2: Valid tags
echo -e "\nTest 2: Valid LOCUST_TAGS = 'checkout browse add_to_cart search-filter 123test'"
LOCUST_TAGS="checkout browse add_to_cart search-filter 123test"
if ! echo "$LOCUST_TAGS" | grep -qE '^[-a-zA-Z0-9_ ]+$'; then
    echo "FAIL: Valid tags should pass regex"
else
    echo "PASS: Valid tags pass regex check"
fi

# Test 3: Invalid character
echo -e "\nTest 3: Invalid LOCUST_TAGS = 'checkout! browse'"
LOCUST_TAGS="checkout! browse"
if echo "$LOCUST_TAGS" | grep -qE '^[-a-zA-Z0-9_ ]+$'; then
    echo "FAIL: Invalid tags should fail regex"
else
    echo "PASS: Invalid tags fail regex check correctly"
fi

# Test 4: Special invalid characters
echo -e "\nTest 4: Invalid LOCUST_TAGS = 'tag@name tag#id'"
LOCUST_TAGS="tag@name tag#id"
if echo "$LOCUST_TAGS" | grep -qE '^[-a-zA-Z0-9_ ]+$'; then
    echo "FAIL: Special characters should not be allowed"
else
    echo "PASS: Special characters correctly rejected"
fi
